"""Regression tests for bugs found in the independent code review."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from conftest import make_scene
from monitor import ais, copernicus, detect, storage
from monitor.copernicus import Scene

T = datetime(2026, 10, 21, 2, 14, tzinfo=timezone.utc)


def test_unknown_speed_and_course_are_not_projected():
    rep = pd.DataFrame([
        {"mmsi": 1, "time": (T - timedelta(minutes=10)).isoformat(), "lat": 26.0, "lon": 56.0, "sog": 102.3, "cog": 90},
        {"mmsi": 2, "time": (T - timedelta(minutes=10)).isoformat(), "lat": 26.0, "lon": 56.0, "sog": 12, "cog": 360},
    ])
    now = ais.positions_at(rep, T, 15)
    assert np.allclose(now.lat_pred, 26.0) and np.allclose(now.lon_pred, 56.0)


def test_ship_touching_image_edge_is_found():
    raster = make_scene([(0, 300, 10), (500, 990, 10)])   # top edge, right edge
    dets = detect.detect_ships(raster, np.ones(raster.db.shape, bool), pixel_m=20)
    assert len(dets) == 2


def test_length_estimate_is_unbiased():
    for L in (5, 10, 15):
        mask = np.zeros((50, 50), bool); mask[20, 10:10 + L] = True
        raster = make_scene([], shape=(50, 50))
        dets = detect.blobs_to_table(mask, np.full((50, 50), 5.0), raster, pixel_m=20)
        assert abs(dets.iloc[0]["length_m"] - L * 20) <= 10, (L, dets.iloc[0]["length_m"])


def test_jittering_platform_is_still_flagged():
    memory = {}
    a = pd.DataFrame({"lat": [26.5000], "lon": [56.4], "length_m": [80]})
    b = pd.DataFrame({"lat": [26.5031], "lon": [56.4], "length_m": [80]})   # next cell north
    for i in range(12):
        out = detect.flag_persistent("hormuz", a if i % 2 else b, memory)
    assert bool(out["persistent"].iloc[0])


def test_ship_that_leaves_stops_being_persistent_and_memory_stays_small():
    memory = {}
    anchored = pd.DataFrame({"lat": [36.0], "lon": [120.4], "length_m": [300]})
    for _ in range(10):
        detect.flag_persistent("qingdao", anchored, memory)
    empty = pd.DataFrame(columns=["lat", "lon", "length_m"])
    for _ in range(120):     # ~4 months of scenes without it
        detect.flag_persistent("qingdao", empty, memory)
    assert not bool(detect.flag_persistent("qingdao", anchored, memory, record=False)["persistent"].iloc[0])
    assert len(memory["qingdao"]["hits"]) == 0


def test_partial_scenes_do_not_dilute_memory():
    memory = {}
    p = pd.DataFrame({"lat": [26.5], "lon": [56.4], "length_m": [80]})
    for _ in range(6):
        detect.flag_persistent("hormuz", p, memory)
    before = dict(memory["hormuz"])
    detect.flag_persistent("hormuz", p.iloc[:0], memory, record=False)
    assert memory["hormuz"] == before


def test_adjacent_slices_of_one_pass_are_merged():
    def sc(platform, t0):
        return Scene(f"{platform}_{t0:%H%M%S}", "n", t0, t0 + timedelta(seconds=25), platform, "DV", "DESCENDING", 5)
    slices = [sc("S1C", T), sc("S1C", T + timedelta(seconds=25)), sc("S1D", T + timedelta(seconds=40)),
              sc("S1C", T + timedelta(days=6))]
    merged = copernicus.one_per_pass(slices)
    assert [s.scene_id for s in merged] == [slices[0].scene_id, slices[2].scene_id, slices[3].scene_id]
    assert merged[0].end == slices[1].end and slices[0].end == T + timedelta(seconds=25)


def test_integer_keys_stay_integers_when_a_value_is_missing(local_sheet):
    local_sheet.upsert("S", pd.DataFrame({"aoi": ["x"], "scene_id": ["a"], "relative_orbit": [12]}), keys=["aoi", "scene_id"])
    local_sheet.upsert("S", pd.DataFrame({"aoi": ["x", "x"], "scene_id": ["b", "c"], "relative_orbit": [12, None]}),
                       keys=["aoi", "scene_id"])
    assert list(local_sheet.read("S").relative_orbit.fillna("")) == ["12", "12", ""]


def test_calibration_ignores_scenes_after_last_eia_week():
    dates = pd.date_range("2026-01-02", periods=30, freq="7D")
    stocks = pd.DataFrame({"date": dates, "cushing_kbbl": np.linspace(20000, 26000, 30)})
    t = list(dates[:-1] + pd.Timedelta(days=3)) + [dates[-1] + pd.Timedelta(days=20)]
    idx = pd.DataFrame({"aoi": "cushing", "scene_time": [x.strftime("%Y-%m-%d %H:%M") for x in t],
                        "relative_orbit": "1", "bright_frac": np.linspace(0.01, 0.02, 30),
                        "p95_db": 0.0, "mean_db": 0.0})
    cal = storage.calibrate(idx, stocks)
    assert cal[cal.metric == "bright_frac"].iloc[0]["n_scenes"] == 29


def test_zero_baseline_gives_blank_not_inf():
    from monitor import portwatch
    rows = [{"date": f"2026-0{1 + d // 28}-{1 + d % 28:02d}", "portname": "X", "n_total": 0, "n_tanker": 0} for d in range(40)]
    df = portwatch.add_metrics(portwatch.to_dataframe(rows))
    df = df.replace([np.inf, -np.inf], np.nan)  # what pull() does
    assert not np.isinf(pd.to_numeric(df["n_total_avg7_vs_avg30_pct"], errors="coerce")).any()


def test_copernicus_login_strips_whitespace_and_explains_rejection(monkeypatch):
    import pytest
    from monitor import copernicus as cop

    sent = {}

    class Resp:
        def __init__(self, code): self.status_code, self.text = code, '{"error":"invalid_client"}'
        def raise_for_status(self): pass
        def json(self): return {"access_token": "tok", "expires_in": 600}

    def fake_post(url, data, timeout):
        sent.update(data)
        return Resp(200 if data["client_secret"] == "sekret" else 401)

    monkeypatch.setattr(cop.requests, "post", fake_post)
    assert cop.TokenCache(" abc\n", "sekret \n").get() == "tok"
    assert sent["client_id"] == "abc" and sent["client_secret"] == "sekret"
    with pytest.raises(RuntimeError, match="rejected the login.*invalid_client"):
        cop.TokenCache("abc", "wrong").get()


def test_unknown_dark_counts_are_retracted(local_sheet):
    """Old rows written without AIS data get their dark counts blanked."""
    import pandas as pd
    import run_daily
    local_sheet.replace("SAR_Detections", pd.DataFrame([
        {"scene_time": "2026-10-18 17:25", "aoi": "cape", "scene_id": "a", "ais_ships_in_box": 0, "n_dark": 62, "n_dark_high": 0},
        {"scene_time": "2026-10-19 17:25", "aoi": "cape", "scene_id": "b", "ais_ships_in_box": 14, "n_dark": 3, "n_dark_high": 1}]))
    local_sheet.replace("Dark_Candidates", pd.DataFrame([
        {"aoi": "cape", "dark_confidence": "no_ais_coverage"}, {"aoi": "cape", "dark_confidence": "high"}]))
    run_daily.retract_unknown_dark(local_sheet)
    run_daily.retract_unknown_dark(local_sheet)          # repeating changes nothing
    sar = local_sheet.read("SAR_Detections")
    assert sar.loc[0, "n_dark"] in ("", None) or pd.isna(sar.loc[0, "n_dark"])
    assert str(sar.loc[1, "n_dark"]) == "3"
    assert list(local_sheet.read("Dark_Candidates")["dark_confidence"]) == ["high"]
