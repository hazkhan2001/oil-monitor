"""
ais.py: Phase 3. Record AIS around satellite passes, then find "dark" ships.

AIS is the radio transponder every big ship is supposed to broadcast:
identity (MMSI), position, speed, course. A ship the radar sees but that
isn't broadcasting near that spot is a "dark candidate".

WHY "CANDIDATE" AND NOT "DARK SHIP"
    AISstream is crowd-sourced from shore receivers. Far from the coast,
    or where receivers are sparse, honest ships can be missing too. So each
    candidate gets a confidence based on how well AIS covered its
    neighbourhood: if most nearby radar targets matched AIS but this one
    didn't, that's meaningful. If nothing nearby matched, coverage was bad.

LESSON (async): AISstream is a websocket, a connection that stays open and
pushes messages at you. `async def` / `await` let Python wait on the network
without freezing. asyncio.run(...) starts the event loop that drives it.

LESSON (dead reckoning): a ship last reported 4 minutes before the satellite
pass has moved since. With speed (knots) and course (degrees) we project it
forward: distance = speed * time, split into north/east components with
cos/sin of the course. Sailors did this with a chart and dividers.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config

WS_URL = "wss://stream.aisstream.io/v0/stream"
KNOT_MS = 0.514444
EARTH_R = 6_371_000
AIS_COLUMNS = ["mmsi", "time", "lat", "lon", "sog", "cog", "name", "ship_type", "length_m", "aoi"]


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

def _parse_time(meta: dict) -> datetime:
    raw = meta.get("time_utc")
    if raw:
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", raw)
        if m:
            return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}+00:00")
    return datetime.now(timezone.utc)


def _which_aoi(lat, lon, aois, margin=0.2):
    for a in aois:
        lon0, lat0, lon1, lat1 = a.bbox
        if lat0 - margin <= lat <= lat1 + margin and lon0 - margin <= lon <= lon1 + margin:
            return a.key
    return None


async def _record(api_key: str, aois, seconds: float) -> pd.DataFrame:
    import websockets

    margin = 0.2  # record a bit around each box: ships drift in during the window
    boxes = [[[a.bbox[1] - margin, a.bbox[0] - margin], [a.bbox[3] + margin, a.bbox[2] + margin]]
             for a in aois]                      # AISstream wants [lat, lon] corners
    sub = {"APIKey": api_key, "BoundingBoxes": boxes,
           "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"]}
    positions, statics = {}, {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            async with websockets.connect(WS_URL, max_size=None, open_timeout=20) as ws:
                await ws.send(json.dumps(sub))
                while time.monotonic() < deadline:
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(frame.decode() if isinstance(frame, bytes) else frame)
                    if "error" in msg:
                        raise RuntimeError(f"AISstream: {msg['error']}")
                    kind, meta = msg.get("MessageType"), msg.get("MetaData", {})
                    body = msg.get("Message", {}).get(kind, {})
                    mmsi = meta.get("MMSI")
                    if not mmsi:
                        continue
                    if kind == "ShipStaticData":
                        dim = body.get("Dimension") or {}
                        length = (dim.get("A") or 0) + (dim.get("B") or 0)
                        statics[mmsi] = {"ship_type": body.get("Type"), "length_m": length or None,
                                         "name": (body.get("Name") or meta.get("ShipName") or "").strip()}
                        continue
                    lat, lon = meta.get("latitude", meta.get("Latitude")), meta.get("longitude", meta.get("Longitude"))
                    if lat is None or lon is None:
                        continue
                    t = _parse_time(meta)
                    # keep at most one report per ship per minute: plenty for dead reckoning
                    positions[(mmsi, t.strftime("%Y%m%d%H%M"))] = {
                        "mmsi": mmsi, "time": t.isoformat(), "lat": lat, "lon": lon,
                        "sog": body.get("Sog"), "cog": body.get("Cog"),
                        "name": (meta.get("ShipName") or "").strip(),
                        "aoi": _which_aoi(lat, lon, aois),
                    }
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as exc:
            # dropped connection: wait a moment and reconnect until the window ends
            print(f"  AIS connection dropped ({exc}); reconnecting")
            await asyncio.sleep(3)
    df = pd.DataFrame(list(positions.values()))
    if df.empty:
        return pd.DataFrame(columns=AIS_COLUMNS)
    st = pd.DataFrame.from_dict(statics, orient="index")
    if not st.empty:
        df = df.merge(st[["ship_type", "length_m"]], left_on="mmsi", right_index=True, how="left")
    for col in AIS_COLUMNS:
        if col not in df:
            df[col] = None
    return df[AIS_COLUMNS]


def record(api_key: str, aois, seconds: float) -> pd.DataFrame:
    return asyncio.run(_record(api_key, aois, seconds))


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; works on whole arrays at once."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


def positions_at(ais: pd.DataFrame, when: datetime, max_gap_min: float) -> pd.DataFrame:
    """Each ship's estimated position at `when`, from its closest report."""
    if ais.empty:
        return ais.assign(dt_s=[])
    df = ais.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["dt_s"] = (pd.Timestamp(when) - df["time"]).dt.total_seconds()
    df = df[df["dt_s"].abs() <= max_gap_min * 60]
    df = df.loc[df.groupby("mmsi")["dt_s"].apply(lambda s: s.abs().idxmin())] if len(df) else df
    sog = pd.to_numeric(df["sog"], errors="coerce").fillna(0).clip(upper=40)  # 102.3 = "not available"
    cog = pd.to_numeric(df["cog"], errors="coerce").fillna(0)
    dist = sog * KNOT_MS * df["dt_s"]
    north, east = dist * np.cos(np.radians(cog)), dist * np.sin(np.radians(cog))
    df = df.copy()
    df["lat_pred"] = df["lat"].astype(float) + np.degrees(north / EARTH_R)
    df["lon_pred"] = df["lon"].astype(float) + np.degrees(east / (EARTH_R * np.cos(np.radians(df["lat"].astype(float)))))
    return df.reset_index(drop=True)


def match(dets: pd.DataFrame, ais_now: pd.DataFrame, radius_m: float = config.AIS_MATCH_RADIUS_M) -> pd.DataFrame:
    """Pair detections with AIS ships, closest pairs first, each used once."""
    dets = dets.copy()
    dets["mmsi"], dets["ais_name"], dets["match_dist_m"] = None, None, np.nan
    if dets.empty or ais_now.empty:
        return dets
    d = haversine_m(dets["lat"].values[:, None], dets["lon"].values[:, None],
                    ais_now["lat_pred"].values[None, :], ais_now["lon_pred"].values[None, :])
    pairs = np.argwhere(d <= radius_m)
    order = np.argsort(d[pairs[:, 0], pairs[:, 1]])
    used_det, used_ais = set(), set()
    for i, j in pairs[order]:          # greedy: best remaining pair each time
        if i in used_det or j in used_ais:
            continue
        used_det.add(i); used_ais.add(j)
        dets.loc[dets.index[i], ["mmsi", "ais_name", "match_dist_m"]] = [
            ais_now.at[j, "mmsi"], ais_now.at[j, "name"], round(float(d[i, j]))]
    return dets


def score_dark(dets: pd.DataFrame, n_ais_ships: int, neighbourhood_m: float = 20_000) -> pd.DataFrame:
    """Label unmatched, non-persistent, ship-sized detections with a confidence."""
    dets = dets.copy()
    matched = dets["mmsi"].notna().values
    if len(dets):
        d = haversine_m(dets["lat"].values[:, None], dets["lon"].values[:, None],
                        dets["lat"].values[None, :], dets["lon"].values[None, :])
        near = (d <= neighbourhood_m) & ~np.eye(len(dets), dtype=bool)
        n_near = near.sum(1)
        rate = np.where(n_near > 0, (near & matched[None, :]).sum(1) / np.maximum(n_near, 1), np.nan)
    else:
        rate = np.array([])
    dets["local_ais_match_rate"] = np.round(rate, 2)
    candidate = (~matched) & (~dets["persistent"].astype(bool).values) & (dets["length_m"].values >= config.DARK_MIN_LENGTH_M)
    conf = np.where(n_ais_ships == 0, "no_ais_coverage",
           np.where(np.nan_to_num(rate, nan=0) >= 0.5, "high",
           np.where(np.nan_to_num(rate, nan=0) >= 0.25, "medium", "low")))
    dets["dark_candidate"] = candidate
    dets["dark_confidence"] = np.where(candidate, conf, "")
    return dets
