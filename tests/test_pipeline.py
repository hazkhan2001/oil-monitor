"""End-to-end: run the whole morning job with the internet swapped for fakes.

LESSON: monkeypatch replaces a function for the length of one test. Here we
replace every network call (PortWatch, Copernicus, EIA) with functions that
return synthetic data, then check what lands in the (local) sheet.
"""

import gzip
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from conftest import make_scene


def test_full_daily_run_offline(tmp_path, monkeypatch, local_sheet):
    import config
    import run_daily
    from monitor import copernicus, portwatch, state, storage
    from monitor.copernicus import Scene

    now = datetime(2026, 10, 21, 10, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "NOW", now)
    monkeypatch.setattr(run_daily, "OUT", tmp_path / "output")
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run_daily, "AIS_DIR", tmp_path / "state" / "ais")
    monkeypatch.setattr(run_daily, "LOG", [])
    monkeypatch.setenv("CDSE_CLIENT_ID", "x")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "y")

    # Only two boxes to keep it quick: one open-ocean "chokepoint" and one tank farm.
    sea_aoi = config.AOI("testsea", "Test Sea", "chokepoint", (-30.0, 30.0, -29.8, 30.18), ais=True,
                         portwatch="Strait of Hormuz")
    farm = config.AOI_BY_KEY["cushing"]
    monkeypatch.setattr(config, "AOIS", [sea_aoi, farm])
    monkeypatch.setitem(config.AOI_BY_KEY, "testsea", sea_aoi)

    scene_t = now - timedelta(hours=8)
    scene = Scene("S1A_20261021T0230", "n", scene_t, scene_t + timedelta(seconds=25), "S1A", "DV", "DESCENDING", 5)
    monkeypatch.setattr(copernicus, "search_scenes", lambda bbox, a, b, **k: [scene] if b > scene_t else [])

    ships = [(100, 100, 12), (300, 600, 8), (500, 250, 15)]

    def fake_fetch(sc, bbox, res, tokens):
        r = make_scene(ships, bbox=bbox)
        return r

    monkeypatch.setattr(copernicus, "fetch_raster", fake_fetch)
    monkeypatch.setattr(copernicus.TokenCache, "get", lambda self: "fake-token")   # no real login offline
    from monitor import optical
    monkeypatch.setattr(optical, "search_s2", lambda *a, **k: [])   # no real Sentinel-2 search offline
    pw = pd.DataFrame({"date": pd.date_range("2026-09-01", periods=45), "portname": "Strait of Hormuz",
                       "n_total": range(100, 145), "n_tanker": range(30, 75)})
    monkeypatch.setattr(portwatch, "pull", lambda names, first_year: (portwatch.add_metrics(pw), []))
    stocks = pd.DataFrame({"date": pd.date_range("2026-06-05", periods=20, freq="7D"),
                           "cushing_kbbl": np.linspace(22000, 26000, 20)})
    monkeypatch.setattr(storage, "fetch_cushing_stocks", lambda: stocks)

    # AIS recording: ships 1 and 2 broadcast, ship 3 (at row 500) does not.
    probe = make_scene([], bbox=sea_aoi.bbox)
    ais_rows = []
    for mmsi, (r, c, L) in zip([111, 222], ships[:2]):
        lon, lat = probe.pixel_to_lonlat(r, c + L / 2)
        ais_rows.append({"mmsi": mmsi, "time": (scene_t - timedelta(minutes=1)).isoformat(), "lat": float(lat),
                         "lon": float(lon), "sog": 0, "cog": 0, "name": f"SHIP{mmsi}", "ship_type": 80,
                         "length_m": 250, "aoi": "testsea"})
    # plus a few far-away AIS ships so the neighbourhood counts as covered
    ais_dir = tmp_path / "state" / "ais"
    ais_dir.mkdir(parents=True)
    with gzip.open(ais_dir / f"{scene_t - timedelta(minutes=6):%Y%m%dT%H%M}_testsea.csv.gz", "wt") as fh:
        pd.DataFrame(ais_rows).to_csv(fh, index=False)

    import sys
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--only", "portwatch,sar,eia,summary"])
    code = run_daily.main()

    sar = local_sheet.read("SAR_Detections")
    assert code == 0, run_daily.LOG
    assert sar.iloc[0]["n_vessels"] == "3" and sar.iloc[0]["n_matched"] == "2"
    dark = local_sheet.read("Dark_Candidates")
    assert len(dark) == 1 and dark.iloc[0]["dark_confidence"] in ("high", "medium")
    assert len(local_sheet.read("Storage_Index")) == 1
    assert not local_sheet.read("Summary").empty
    assert (tmp_path / "output" / "crops").exists()

    # Second run: same scene must not be processed (or billed) twice.
    monkeypatch.setattr(run_daily, "LOG", [])
    run_daily.main()
    assert len(local_sheet.read("SAR_Detections")) == 1
    assert state.load("pu_usage", {})["2026-10"] == 2.0
