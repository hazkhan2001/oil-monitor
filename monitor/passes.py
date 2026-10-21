"""
passes.py: predict when a Sentinel-1 satellite will next fly over each box.

Free AIS (AISstream) is live-only: it can't tell us where ships *were*.
So to compare radar detections with AIS we have to be recording AIS at the
moment the satellite passes. This module works out when that is.

THE TRICK
    Each Sentinel-1 satellite repeats exactly the same ground track every
    12 days, at the same time of day to within seconds. So "a scene over
    Hormuz at 02:14 UTC on Oct 9" predicts "another at about 02:14 UTC on
    Oct 21". We take the last 12 days of scenes, add 12 days to each, and
    keep the ones coming up. Mission planners occasionally skip a pass, so a
    prediction can be a no-show; the cost is a wasted AIS recording, not a
    wrong number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import copernicus

REPEAT = timedelta(days=12)


def predict(aois, now: datetime | None = None, horizon_h: int = 36, session=None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    kwargs = {"session": session} if session else {}
    out = []
    for aoi in aois:
        if not aoi.ais:
            continue
        history = copernicus.search_scenes(aoi.bbox, now - REPEAT - timedelta(hours=1),
                                           now + timedelta(hours=1), **kwargs)
        for s in history:
            nxt = s.start + REPEAT
            while nxt < now:          # a scene from >12 days ago would need two hops
                nxt += REPEAT
            if nxt <= now + timedelta(hours=horizon_h):
                out.append({"aoi": aoi.key, "time": nxt.isoformat(), "platform": s.platform})
    # identical predictions can arise from overlapping scenes; keep one each
    seen, unique = set(), []
    for p in sorted(out, key=lambda p: p["time"]):
        key = (p["aoi"], p["time"][:16])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def windows(predictions: list[dict], now: datetime, lookahead_min: int,
            before_min: int = 6, after_min: int = 4, merge_gap_min: int = 20) -> list[dict]:
    """Group upcoming passes into recording windows.

    Passes over neighbouring boxes (Hormuz, then Bab el-Mandeb 50 min later on
    the same orbit) are merged when close, so one recording covers several.
    Returns [{"start": dt, "end": dt, "aois": [...]}, ...].
    """
    upcoming = []
    for p in predictions:
        t = datetime.fromisoformat(p["time"])
        if now - timedelta(minutes=after_min) <= t <= now + timedelta(minutes=lookahead_min):
            upcoming.append((t, p["aoi"]))
    upcoming.sort()
    groups = []
    for t, aoi in upcoming:
        start, end = t - timedelta(minutes=before_min), t + timedelta(minutes=after_min)
        if groups and start <= groups[-1]["end"] + timedelta(minutes=merge_gap_min):
            groups[-1]["end"] = max(groups[-1]["end"], end)
            if aoi not in groups[-1]["aois"]:
                groups[-1]["aois"].append(aoi)
        else:
            groups.append({"start": start, "end": end, "aois": [aoi]})
    return groups
