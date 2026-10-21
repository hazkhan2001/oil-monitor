"""
optical.py: a clear-sky Sentinel-2 cross-check for the tank farms.

WHY A SECOND SATELLITE
    Radar (Sentinel-1) works every pass but is indirect. Sentinel-2 takes real
    colour pictures. On a clear day you can see each tank's roof and the
    shadow it casts inside the tank wall: a roof riding low leaves a big
    crescent of shadow. That is a direct check on what radar suggests.

WHAT THIS MODULE DOES (and does not)
    It finds Sentinel-2 scenes, asks Copernicus for a true-colour picture of
    just the tank farm, measures how much of THAT picture is cloud, and keeps
    it only if it is clear. It records the scene in a sheet tab and saves the
    picture for a human to read. It does NOT yet turn shadows into a number:
    that needs a calibration against EIA, which needs a pile of clear images.

LESSON: a *pure function* (cloud_fraction) only looks at its inputs, so it can
be tested without the internet. Network code stays in the other functions.
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

from monitor.copernicus import CATALOG_URL, PROCESS_URL, TokenCache, _attr, _iso, bbox_to_wkt, grid_size

MAX_TILE_CLOUD_PCT = 40.0     # skip scenes the catalogue already says are mostly cloud
MAX_LOCAL_CLOUD = 0.10        # keep a picture only if under 10% of the farm is cloud
# Scene classification (SCL) codes that mean "can't see the ground":
# 3 cloud shadow, 8 and 9 cloud (medium/high probability), 10 thin cirrus.
CLOUDY_SCL = (3, 8, 9, 10)


def cloud_fraction(flags: np.ndarray, valid: np.ndarray) -> float | None:
    """Share of data-bearing pixels flagged as cloud. None if nothing is valid."""
    n = int(valid.sum())
    if n == 0:
        return None
    return float((flags.astype(bool) & valid.astype(bool)).sum() / n)


def parse_s2_name(name: str) -> dict | None:
    # S2B_MSIL2A_20261021T165329_N0511_R069_T14SPG_20261021T200012.SAFE
    p = name.split("_")
    if len(p) < 6 or "MSIL2A" not in p[1]:
        return None
    try:
        start = datetime.strptime(p[2], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"scene_id": f"{p[0]}_{p[2]}_{p[5]}", "platform": p[0], "start": start, "tile": p[5]}


def search_s2(bbox, start: datetime, end: datetime, session=requests) -> list[dict]:
    flt = (
        "Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_to_wkt(bbox)}') "
        f"and ContentDate/Start gt {_iso(start)} and ContentDate/Start lt {_iso(end)}"
    )
    params = {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": 100, "$expand": "Attributes"}
    resp = session.get(CATALOG_URL, params=params, timeout=60)
    resp.raise_for_status()
    found: dict[str, dict] = {}
    for prod in resp.json().get("value", []):
        info = parse_s2_name(prod["Name"])
        if not info:
            continue
        cc = _attr(prod, "cloudCover")
        info["tile_cloud_pct"] = float(cc) if cc is not None else None
        found.setdefault(info["scene_id"], info)
    return sorted(found.values(), key=lambda s: s["start"])


_EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: [{ bands: ["B04", "B03", "B02", "SCL", "dataMask"] }],
           output: { bands: 5, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  var g = 2.5 * 255;                       // brighten reflectance (0-1) to 8-bit
  var cloudy = (s.SCL == 3 || s.SCL == 8 || s.SCL == 9 || s.SCL == 10) ? 255 : 0;
  return [Math.min(255, s.B04 * g), Math.min(255, s.B03 * g), Math.min(255, s.B02 * g), cloudy, s.dataMask * 255];
}"""


def fetch_true_colour(scene: dict, bbox, tokens: TokenCache, resolution_m: int = 10):
    """Returns (rgb uint8 array HxWx3, cloud flags HxW, valid HxW)."""
    import tifffile

    w, h = grid_size(bbox, resolution_m)
    body = {
        "input": {
            "bounds": {"bbox": list(bbox), "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {"timeRange": {"from": _iso(scene["start"] - timedelta(minutes=2)),
                                                    "to": _iso(scene["start"] + timedelta(minutes=2))}}}],
        },
        "output": {"width": w, "height": h, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": _EVALSCRIPT,
    }
    for attempt in range(3):
        resp = requests.post(PROCESS_URL, json=body, timeout=180, headers={"Authorization": f"Bearer {tokens.get()}"})
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        break
    if resp.status_code != 200:
        raise RuntimeError(f"Process API {resp.status_code}: {resp.text[:300]}")
    arr = tifffile.imread(io.BytesIO(resp.content))
    if arr.ndim == 3 and arr.shape[0] == 5 and arr.shape[-1] != 5:
        arr = np.moveaxis(arr, 0, -1)
    return arr[..., :3], arr[..., 3] > 0, arr[..., 4] > 0
