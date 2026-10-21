"""
config.py: every location the monitor watches, plus the tuning knobs.

LESSON: a "dataclass" is a quick way to define a record type. Writing
    @dataclass
    class AOI:
        name: str
gives you AOI(name="Hormuz") with a readable print-out and no boilerplate.
`frozen=True` makes instances read-only, so no code can accidentally
change a bounding box while the program runs.

COORDINATE ORDER WARNING (a classic bug source):
    bbox here is (lon_min, lat_min, lon_max, lat_max), i.e. x before y,
    which is what Copernicus wants. AISstream wants [lat, lon] pairs instead.
    Each module converts explicitly; never assume.

The boxes are hand-drawn around shipping lanes and anchorages and kept tight
on purpose: the free Copernicus tier gives 10,000 processing units a month,
and cost scales with box area. Every first run saves a quicklook PNG per box
(output/quicklooks/) so you can eyeball whether a box sits where it should.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AOI:
    """An Area Of Interest: one box on the map we analyse."""

    key: str                    # short id used in file names and the sheet
    name: str                   # human-readable name
    kind: str                   # "chokepoint", "terminal" or "tankfarm"
    bbox: tuple                 # (lon_min, lat_min, lon_max, lat_max)
    resolution_m: int = 20      # output pixel size in metres
    ais: bool = False           # record AIS around passes for dark-vessel matching?
    portwatch: str | None = None  # matching PortWatch chokepoint name, if any
    land_buffer_m: int = 1500   # ignore detections this close to land


AOIS = [
    # --- chokepoints: ship counts + dark-vessel matching --------------------
    AOI("hormuz", "Strait of Hormuz", "chokepoint",
        (56.20, 26.30, 56.70, 26.65), ais=True, portwatch="Strait of Hormuz"),
    AOI("bab", "Bab el-Mandeb", "chokepoint",
        (43.20, 12.45, 43.50, 12.75), ais=True, portwatch="Bab el-Mandeb Strait"),
    AOI("malacca", "Malacca Strait (Port Dickson narrows)", "chokepoint",
        (101.60, 2.00, 102.10, 2.40), ais=True, portwatch="Malacca Strait"),
    # The canal itself is ~300 m wide, narrower than our 1 km land mask, so we
    # watch the southern anchorage where ships queue for the convoy instead.
    AOI("suez", "Suez southern anchorage", "chokepoint",
        (32.45, 29.75, 32.65, 29.95), ais=True, portwatch="Suez Canal",
        land_buffer_m=1000),
    AOI("cape", "Cape of Good Hope", "chokepoint",
        (18.00, -34.60, 18.60, -34.20), ais=True, portwatch="Cape of Good Hope"),

    # Where the WSJ (Oct 20, 2026) says tankers hand cargo to other ships after
    # leaving Hormuz, often with transponders off. Approximate box: check the
    # quicklook after the first pass and adjust.
    AOI("gulfoman", "Gulf of Oman (Fujairah anchorage)", "terminal",
        (56.35, 25.15, 56.85, 25.60), ais=True),

    # --- Chinese import terminals: anchorage queues -------------------------
    AOI("qingdao", "Qingdao anchorage", "terminal",
        (120.20, 35.80, 120.60, 36.05), ais=True),
    AOI("ningbo", "Ningbo-Zhoushan anchorage", "terminal",
        (122.00, 29.85, 122.35, 30.05), ais=True),

    # --- tank farms: storage index (10 m, no land mask, no AIS) -------------
    # Approximate boxes. Check the quicklooks after the first run.
    AOI("cushing", "Cushing, OK", "tankfarm",
        (-96.80, 35.93, -96.72, 36.00), resolution_m=10),
    AOI("fujairah", "Fujairah", "tankfarm",
        (56.33, 25.17, 56.38, 25.23), resolution_m=10),
    AOI("rotterdam", "Rotterdam Europoort/Maasvlakte", "tankfarm",
        (4.00, 51.93, 4.15, 51.97), resolution_m=10),
    AOI("rastanura", "Ras Tanura", "tankfarm",
        (50.12, 26.63, 50.17, 26.70), resolution_m=10),
    AOI("kharg", "Kharg Island", "tankfarm",
        (50.29, 29.22, 50.33, 29.27), resolution_m=10),
]

AOI_BY_KEY = {a.key: a for a in AOIS}   # dict comprehension: key -> AOI

# --- detector tuning --------------------------------------------------------
CFAR_GUARD_PX = 4          # half-width of the "don't look here" box around a pixel
CFAR_BACKGROUND_PX = 14    # half-width of the box used to measure the local sea
CFAR_K = 5.0               # how many standard deviations above the sea = target
MIN_TARGET_DB = -10.0      # targets must also be at least this bright
MIN_TARGET_PIXELS = 2      # ignore single-pixel speckle spikes
MAX_TARGET_LENGTH_M = 600  # longer blobs are clutter, not ships

# --- AIS matching -----------------------------------------------------------
# SAR shifts moving ships sideways along the satellite track (Doppler
# "train off the track" effect), often by several hundred metres, so the
# match radius is deliberately generous.
AIS_MATCH_RADIUS_M = 2000
AIS_MAX_TIME_GAP_MIN = 15
DARK_MIN_LENGTH_M = 60

# --- budget -----------------------------------------------------------------
MONTHLY_PU_BUDGET = 9000   # stop fetching imagery before the free 10,000 runs out
SCENE_LOOKBACK_DAYS = 3    # each daily run looks this far back for new scenes
