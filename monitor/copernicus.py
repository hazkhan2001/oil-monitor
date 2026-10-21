"""
copernicus.py: find Sentinel-1 scenes and download just the pixels we need.

THREE COPERNICUS SERVICES ARE USED
  1. Catalogue (OData): "which radar scenes cover this box between these
     dates?" No login needed.
  2. Identity: trades your OAuth client id/secret for a short-lived access
     token (valid about 10 minutes).
  3. Process API (Sentinel Hub): "give me the VV radar band for this box,
     at this scene's time, as a 20 m grid". Costs processing units (PU).

LESSON: the Process API runs a tiny JavaScript program (an "evalscript") on
Copernicus's servers for every pixel. Ours converts radar backscatter to
decibels and packs it into a 16-bit integer, because 16-bit output costs half
the PU of 32-bit floats. We unpack it in Python:
    stored = (dB + 50) * 800      ->      dB = stored / 800 - 50
That's the same trick as storing cents instead of dollars.

LESSON: why no orthorectification? Orthorectifying corrects for terrain
height. Ships sit at sea level, where there is no terrain, so the simple
ellipsoid geocoding is already accurate there and we skip the extra cost.
Tank farms are on flat land near sea level, so the same holds.
"""

from __future__ import annotations

import io
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

DB_OFFSET, DB_SCALE = 50.0, 800.0
MAX_PIXELS_PER_SIDE = 2500  # Process API limit


@dataclass
class Scene:
    """One Sentinel-1 acquisition over an AOI."""
    scene_id: str        # e.g. S1C_20261021T004819, stable across COG/SAFE copies
    name: str
    start: datetime
    end: datetime
    platform: str        # S1A / S1C / S1D
    polarisation: str    # DV, SV, DH or SH
    orbit_direction: str
    relative_orbit: int | None


@dataclass
class Raster:
    """A downloaded image: decibel values + a valid-data mask + where it is."""
    db: np.ndarray       # 2-D array of backscatter in dB
    valid: np.ndarray    # True where the satellite actually had data
    bbox: tuple          # (lon_min, lat_min, lon_max, lat_max)
    pu_spent: float

    def pixel_to_lonlat(self, row, col):
        """Convert (row, col) pixel positions to longitude/latitude."""
        lon_min, lat_min, lon_max, lat_max = self.bbox
        h, w = self.db.shape
        lon = lon_min + (np.asarray(col) + 0.5) * (lon_max - lon_min) / w
        lat = lat_max - (np.asarray(row) + 0.5) * (lat_max - lat_min) / h  # row 0 = north
        return lon, lat


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def bbox_to_wkt(bbox) -> str:
    lon0, lat0, lon1, lat1 = bbox
    return f"POLYGON(({lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}))"


def grid_size(bbox, resolution_m: int) -> tuple[int, int]:
    """How many pixels wide/tall the box is at a given resolution."""
    lon0, lat0, lon1, lat1 = bbox
    mid_lat = math.radians((lat0 + lat1) / 2)
    width_m = (lon1 - lon0) * 111_320 * math.cos(mid_lat)   # a degree of longitude shrinks toward the poles
    height_m = (lat1 - lat0) * 110_574
    w = max(1, min(MAX_PIXELS_PER_SIDE, round(width_m / resolution_m)))
    h = max(1, min(MAX_PIXELS_PER_SIDE, round(height_m / resolution_m)))
    return w, h


def estimate_pu(bbox, resolution_m: int) -> float:
    """Rough PU estimate: 1 PU per 512x512 px for 3 bands; we use 1 band."""
    w, h = grid_size(bbox, resolution_m)
    return max(0.01, w * h / (512 * 512) / 3)


# --------------------------------------------------------------------------
# 1. Catalogue search
# --------------------------------------------------------------------------

def _attr(product: dict, name: str):
    for a in product.get("Attributes", []) or []:
        if a.get("Name") == name:
            return a.get("Value")
    return None


def parse_product(product: dict) -> Scene | None:
    name = product["Name"]
    parts = name.split("_")
    # S1C_IW_GRDH_1SDV_20261021T004819_20261021T004853_009976_013E4D_F4DC_COG.SAFE
    if len(parts) < 6 or parts[1] != "IW" or not parts[2].startswith("GRD"):
        return None
    pol = parts[3][2:4]  # "1SDV" -> "DV"
    start = datetime.fromisoformat(product["ContentDate"]["Start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(product["ContentDate"]["End"].replace("Z", "+00:00"))
    rel = _attr(product, "relativeOrbitNumber")
    return Scene(
        scene_id=f"{parts[0]}_{parts[4]}",
        name=name, start=start, end=end, platform=parts[0], polarisation=pol,
        orbit_direction=str(_attr(product, "orbitDirection") or "UNKNOWN"),
        relative_orbit=int(rel) if rel is not None else None,
    )


def search_scenes(bbox, start: datetime, end: datetime, session=requests) -> list[Scene]:
    """All Sentinel-1 IW GRD scenes intersecting bbox in [start, end)."""
    flt = (
        "Collection/Name eq 'SENTINEL-1' and contains(Name,'IW_GRDH') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_to_wkt(bbox)}') "
        f"and ContentDate/Start gt {_iso(start)} and ContentDate/Start lt {_iso(end)}"
    )
    params = {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": 200,
              "$expand": "Attributes"}
    resp = session.get(CATALOG_URL, params=params, timeout=60)
    resp.raise_for_status()
    scenes: dict[str, Scene] = {}
    for product in resp.json().get("value", []):
        scene = parse_product(product)
        # The same acquisition is often listed twice (.SAFE and _COG.SAFE);
        # keying the dict on scene_id keeps one copy.
        if scene and scene.scene_id not in scenes:
            scenes[scene.scene_id] = scene
    return sorted(scenes.values(), key=lambda s: s.start)


# --------------------------------------------------------------------------
# 2. Authentication
# --------------------------------------------------------------------------

class TokenCache:
    """Fetches an access token and re-fetches it shortly before it expires."""

    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.environ.get("CDSE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("CDSE_CLIENT_SECRET")
        self._token, self._expires = None, 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def get(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires = time.time() + body.get("expires_in", 600)
        return self._token


# --------------------------------------------------------------------------
# 3. Fetch pixels
# --------------------------------------------------------------------------

def _evalscript(band: str) -> str:
    return f"""//VERSION=3
function setup() {{
  return {{ input: [{{ bands: ["{band}", "dataMask"] }}],
           output: {{ bands: 2, sampleType: "UINT16" }} }};
}}
function evaluatePixel(s) {{
  var v = s.{band};
  var db = v > 0 ? 10 * Math.log(v) / Math.LN10 : -{DB_OFFSET};
  db = Math.max(-{DB_OFFSET}, Math.min(25, db));
  return [Math.round((db + {DB_OFFSET}) * {DB_SCALE}), s.dataMask];
}}"""


def fetch_raster(scene: Scene, bbox, resolution_m: int, tokens: TokenCache) -> Raster:
    """Download VV (or HH) backscatter for one scene over one box."""
    import tifffile

    band = "VV" if scene.polarisation in ("DV", "SV") else "HH"
    w, h = grid_size(bbox, resolution_m)
    body = {
        "input": {
            "bounds": {"bbox": list(bbox),
                       "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    # A window around this exact scene so no other pass gets mixed in.
                    "timeRange": {"from": _iso(scene.start - timedelta(seconds=30)),
                                  "to": _iso(scene.end + timedelta(seconds=30))},
                    "acquisitionMode": "IW",
                    "polarization": scene.polarisation,
                    "resolution": "HIGH",
                },
                "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": False},
            }],
        },
        "output": {"width": w, "height": h,
                   "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": _evalscript(band),
    }
    for attempt in range(3):
        resp = requests.post(PROCESS_URL, json=body, timeout=180,
                             headers={"Authorization": f"Bearer {tokens.get()}"})
        if resp.status_code == 429 or resp.status_code >= 500:   # rate limit / server hiccup
            time.sleep(10 * (attempt + 1))
            continue
        break
    if resp.status_code != 200:
        raise RuntimeError(f"Process API {resp.status_code}: {resp.text[:300]}")

    arr = tifffile.imread(io.BytesIO(resp.content))
    if arr.ndim == 3 and arr.shape[0] == 2 and arr.shape[-1] != 2:
        arr = np.moveaxis(arr, 0, -1)  # some TIFFs come band-first
    db = arr[..., 0].astype(np.float32) / DB_SCALE - DB_OFFSET
    valid = arr[..., 1] > 0
    pu = float(resp.headers.get("x-processingunits-spent", estimate_pu(bbox, resolution_m)))
    return Raster(db=db, valid=valid, bbox=tuple(bbox), pu_spent=pu)
