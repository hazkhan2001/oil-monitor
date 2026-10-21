"""Ship detector tests: plant known ships in fake radar, check we find exactly them."""

import numpy as np
import pandas as pd

from conftest import make_scene
from monitor import detect, landmask

SHIPS = [(100, 100, 12), (300, 600, 8), (500, 250, 15), (800, 800, 5), (650, 120, 10)]


def test_finds_every_planted_ship_and_nothing_else():
    raster = make_scene(SHIPS)
    sea = np.ones(raster.db.shape, bool)
    dets = detect.detect_ships(raster, sea, pixel_m=20)
    assert len(dets) == len(SHIPS), dets
    for r, c, length in SHIPS:
        hit = dets[(abs(dets.row - r) <= 3) & (abs(dets.col - (c + length / 2)) <= 3)]
        assert len(hit) == 1, f"ship at {(r, c)} not found once"


def test_length_estimate_is_roughly_right():
    raster = make_scene([(400, 400, 15)])       # 15 px * 20 m = 300 m
    dets = detect.detect_ships(raster, np.ones(raster.db.shape, bool), pixel_m=20)
    assert 240 <= dets.iloc[0]["length_m"] <= 360


def test_pure_sea_gives_no_false_alarms():
    raster = make_scene([], seed=7)
    dets = detect.detect_ships(raster, np.ones(raster.db.shape, bool), pixel_m=20)
    assert len(dets) <= 1


def test_ships_outside_sea_mask_are_ignored():
    raster = make_scene(SHIPS)
    sea = np.ones(raster.db.shape, bool)
    sea[:, :500] = False                       # pretend the left half is land
    dets = detect.detect_ships(raster, sea, pixel_m=20)
    assert (dets["col"] >= 500).all()


def test_pixel_to_lonlat_corners():
    raster = make_scene([])
    lon, lat = raster.pixel_to_lonlat(0, 0)
    assert lon > raster.bbox[0] and lat < raster.bbox[3]     # top-left is north-west
    lon, lat = raster.pixel_to_lonlat(999, 999)
    assert lon < raster.bbox[2] and lat > raster.bbox[1]


def test_persistent_objects_get_flagged_after_repeats():
    memory = {}
    platform = pd.DataFrame({"lat": [26.5], "lon": [56.4], "length_m": [80]})
    for _ in range(7):   # counts fade slightly each scene, so it takes ~6 to reach 5
        out = detect.flag_persistent("hormuz", platform, memory)
    assert bool(out["persistent"].iloc[0])
    passer = pd.DataFrame({"lat": [26.4], "lon": [56.3], "length_m": [250]})
    assert not bool(detect.flag_persistent("hormuz", passer, memory)["persistent"].iloc[0])


def test_landmask_sees_land_at_hormuz():
    from config import AOI_BY_KEY
    from monitor.copernicus import Raster
    aoi = AOI_BY_KEY["hormuz"]
    r = Raster(db=np.zeros((200, 250), np.float32), valid=np.ones((200, 250), bool), bbox=aoi.bbox, pu_spent=0)
    sea = landmask.sea_mask(r, 1500, pixel_m=200)
    assert 0.2 < sea.mean() < 0.95   # Musandam and the Iranian islands are in the box
