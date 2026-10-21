import pandas as pd
from monitor import behavior


def _df():
    return pd.DataFrame({
        "mmsi": [1, 1, 2, 3, 4], "time": ["2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"] + ["2026-01-01T00:05:00Z"] * 3,
        "sog": [10, 0.2, 12, 0.5, 5], "cog": [90, 90, 270, 359, 10], "ship_type": [80, 80, 82, 70, 80],
        "length_m": [250, 250, 300, 180, 200], "aoi": ["hormuz"] * 5,
    })


def test_latest_report_per_ship_and_shares():
    r = behavior.summarize(_df(), "w").iloc[0]
    assert r["n_ships"] == 4 and r["n_tankers"] == 3          # mmsi 1 counted once
    assert r["tanker_slow_share"] == round(1 / 3, 3)          # only mmsi 1's latest 0.2 kn is slow
    assert r["tanker_median_sog"] == 5.0


def test_empty_is_empty():
    assert behavior.summarize(pd.DataFrame(), "w").empty
