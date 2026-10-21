"""AIS matching, dark-candidate scoring and pass prediction."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from monitor import ais, passes
from monitor.copernicus import Scene

T = datetime(2026, 10, 21, 2, 14, tzinfo=timezone.utc)


def _dets(points, persistent=None, length=200):
    df = pd.DataFrame(points, columns=["lat", "lon"])
    df["length_m"] = length
    df["persistent"] = persistent if persistent is not None else False
    return df


def test_dead_reckoning_moves_ship_along_course():
    # due north at 10 knots, reported 5 minutes before the pass
    rep = pd.DataFrame([{"mmsi": 1, "time": (T - timedelta(minutes=5)).isoformat(), "lat": 26.0, "lon": 56.0,
                         "sog": 10, "cog": 0, "name": "A", "ship_type": 80, "length_m": 250, "aoi": "hormuz"}])
    now = ais.positions_at(rep, T, 15)
    moved = ais.haversine_m(26.0, 56.0, now.lat_pred[0], now.lon_pred[0])
    assert abs(moved - 10 * 0.514444 * 300) < 5          # ~1543 m
    assert now.lat_pred[0] > 26.0 and abs(now.lon_pred[0] - 56.0) < 1e-9


def test_old_reports_are_dropped_and_closest_kept():
    rep = pd.DataFrame([
        {"mmsi": 1, "time": (T - timedelta(minutes=40)).isoformat(), "lat": 26, "lon": 56, "sog": 0, "cog": 0, "name": "", "aoi": "x"},
        {"mmsi": 2, "time": (T - timedelta(minutes=9)).isoformat(), "lat": 26, "lon": 56, "sog": 0, "cog": 0, "name": "", "aoi": "x"},
        {"mmsi": 2, "time": (T - timedelta(minutes=1)).isoformat(), "lat": 26.1, "lon": 56, "sog": 0, "cog": 0, "name": "", "aoi": "x"},
    ])
    now = ais.positions_at(rep, T, 15)
    assert list(now.mmsi) == [2] and now.lat[0] == 26.1


def test_match_pairs_each_ship_once_and_leaves_dark_one():
    dets = _dets([(26.40, 56.40), (26.45, 56.45), (26.50, 56.50)])
    ais_now = pd.DataFrame({"mmsi": [11, 22], "name": ["ONE", "TWO"],
                            "lat_pred": [26.401, 26.451], "lon_pred": [56.401, 56.449]})
    out = ais.match(dets, ais_now)
    assert list(out.mmsi[:2]) == [11, 22] and pd.isna(out.mmsi[2])
    scored = ais.score_dark(out, n_ais_ships=2)
    assert list(scored.dark_candidate) == [False, False, True]
    assert scored.dark_confidence[2] == "high"            # its neighbours matched fine


def test_no_ais_means_no_coverage_label_not_high():
    dets = ais.match(_dets([(26.4, 56.4)]), pd.DataFrame(columns=["mmsi", "name", "lat_pred", "lon_pred"]))
    scored = ais.score_dark(dets, n_ais_ships=0)
    assert not scored.dark_candidate.any()          # no AIS data: unknown, so nothing is called dark


def test_persistent_and_small_targets_are_not_dark():
    dets = ais.match(_dets([(26.4, 56.4), (26.5, 56.5)], persistent=[True, False]),
                     pd.DataFrame(columns=["mmsi", "name", "lat_pred", "lon_pred"]))
    dets.loc[1, "length_m"] = 30
    assert not ais.score_dark(dets, 5).dark_candidate.any()


def test_prediction_adds_twelve_days(monkeypatch):
    from config import AOI_BY_KEY
    past = Scene("S1C_x", "n", T - timedelta(days=12), T - timedelta(days=12) + timedelta(seconds=30),
                 "S1C", "DV", "DESCENDING", 5)
    monkeypatch.setattr(passes.copernicus, "search_scenes", lambda *a, **k: [past])
    preds = passes.predict([AOI_BY_KEY["hormuz"]], now=T - timedelta(hours=2))
    assert preds == [{"aoi": "hormuz", "time": T.isoformat(), "platform": "S1C"}]


def test_windows_merge_neighbouring_passes():
    preds = [{"aoi": "hormuz", "time": T.isoformat()},
             {"aoi": "bab", "time": (T + timedelta(minutes=25)).isoformat()},
             {"aoi": "cape", "time": (T + timedelta(hours=5)).isoformat()}]
    g = passes.windows(preds, now=T - timedelta(minutes=30), lookahead_min=75)
    assert len(g) == 1 and g[0]["aois"] == ["hormuz", "bab"]
    assert g[0]["start"] == T - timedelta(minutes=6) and g[0]["end"] == T + timedelta(minutes=29)
