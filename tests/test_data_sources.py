"""PortWatch maths, Copernicus parsing, storage calibration, sheet upserts."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from monitor import copernicus, portwatch, report, storage


def test_portwatch_rolling_averages_by_hand():
    rows = [{"date": f"2026-08-{d:02d}" if d <= 31 else f"2026-09-{d - 31:02d}", "portname": p,
             "n_total": base + d, "n_tanker": (base + d) // 4}
            for p, base in [("Strait of Hormuz", 100), ("Suez Canal", 50)] for d in range(1, 46)]
    df = portwatch.add_metrics(portwatch.to_dataframe(rows))
    last = df[df.portname == "Strait of Hormuz"].iloc[-1]
    assert last.n_total_avg7 == np.mean(range(139, 146))     # 142
    assert last.n_total_avg30 == np.mean(range(116, 146))    # 130.5
    suez = df[df.portname == "Suez Canal"].reset_index(drop=True)
    assert suez.n_total_avg7[:6].isna().all() and pd.notna(suez.n_total_avg7[6])  # no leakage
    assert portwatch.find_gaps(df) == []


def test_parse_real_product_name_and_dedupe_key():
    product = {"Name": "S1C_IW_GRDH_1SDV_20261021T004819_20261021T004853_009976_013E4D_F4DC_COG.SAFE",
               "ContentDate": {"Start": "2026-10-21T00:48:19.753512Z", "End": "2026-10-21T00:48:53.011631Z"},
               "Attributes": [{"Name": "orbitDirection", "Value": "ASCENDING"},
                              {"Name": "relativeOrbitNumber", "Value": 78}]}
    s = copernicus.parse_product(product)
    assert s.scene_id == "S1C_20261021T004819" and s.polarisation == "DV"
    assert s.orbit_direction == "ASCENDING" and s.relative_orbit == 78
    twin = dict(product, Name=product["Name"].replace("_COG.SAFE", ".SAFE"))
    assert copernicus.parse_product(twin).scene_id == s.scene_id


def test_db_packing_roundtrip():
    for db in [-50, -23.4, 0, 12.7, 25]:
        stored = round((db + copernicus.DB_OFFSET) * copernicus.DB_SCALE)
        assert stored <= 65535
        assert abs(stored / copernicus.DB_SCALE - copernicus.DB_OFFSET - db) < 0.001


def test_grid_and_pu_budget_fit_free_tier():
    import config
    total = 0
    for a in config.AOIS:
        w, h = copernicus.grid_size(a.bbox, a.resolution_m)
        assert w <= 2500 and h <= 2500
        total += copernicus.estimate_pu(a.bbox, a.resolution_m)
    # ~15 scenes per box per month must fit the budget
    assert total * 15 < config.MONTHLY_PU_BUDGET, total


def test_storage_calibration_detects_a_real_relationship():
    rng = np.random.default_rng(1)
    dates = pd.date_range("2026-01-02", periods=40, freq="7D")
    stocks = pd.DataFrame({"date": dates, "cushing_kbbl": 25000 + 3000 * np.sin(np.arange(40) / 5)})
    scene_t = dates[:-1] + pd.Timedelta(days=3)
    true = np.interp(scene_t.astype("int64"), dates.astype("int64"), stocks.cushing_kbbl)
    idx = pd.DataFrame({"aoi": "cushing", "scene_time": scene_t.strftime("%Y-%m-%d %H:%M"), "relative_orbit": "107",
                        "bright_frac": 0.01 + true / 1e6 + rng.normal(0, 0.0003, len(scene_t)),
                        "p95_db": rng.normal(0, 1, len(scene_t)), "mean_db": rng.normal(-10, 1, len(scene_t))})
    cal = storage.calibrate(idx, stocks)
    bf = cal[cal.metric == "bright_frac"].iloc[0]
    assert bf.pearson_r > 0.9 and bf.verdict.startswith("usable")
    noise = cal[cal.metric == "p95_db"].iloc[0]
    assert noise.verdict.startswith("NOT usable")


def test_storage_zscores_only_compare_same_orbit():
    df = pd.DataFrame({"aoi": "fujairah", "scene_time": [f"2026-0{m}-01 02:00" for m in range(1, 10)],
                       "relative_orbit": ["1", "2"] * 4 + ["1"], "bright_frac": [0.1, 0.9] * 4 + [0.1]})
    z = storage.add_orbit_zscores(df)
    assert z.bright_frac_z_same_orbit.dropna().abs().max() < 1e-6 or z.bright_frac_z_same_orbit.isna().all()


def test_sheet_upsert_is_idempotent(local_sheet):
    df = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]), "portname": ["A", "A"], "n": [1, 2]})
    local_sheet.upsert("T", df, keys=["date", "portname"])
    local_sheet.upsert("T", df, keys=["date", "portname"])
    newer = pd.DataFrame({"date": pd.to_datetime(["2026-10-02"]), "portname": ["A"], "n": [5]})
    n = local_sheet.upsert("T", newer, keys=["date", "portname"], sort_by=["date"])
    out = local_sheet.read("T")
    assert n == 2 and list(out.n) == ["1", "5"] and list(out.date) == ["2026-10-01", "2026-10-02"]


def test_summary_flags_unusual_tanker_week():
    pw = pd.DataFrame([{"date": "2026-10-18", "portname": "Strait of Hormuz", "n_total_avg7": 100,
                        "n_total_avg30": 104, "n_tanker_avg30": 38,
                        "n_total_avg7_vs_avg30_pct": -5, "n_tanker_avg7": 30, "n_tanker_avg7_vs_avg30_pct": -22}])
    s = report.build_summary(pw, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert "tanker transits -22% vs 30d" in s.set_index("location").loc["Strait of Hormuz", "flags"]


def test_tiny_base_is_not_flagged_but_regime_change_is():
    # The real October 2026 Hormuz case: ~1 tanker/day, vs ~45/day a year ago.
    pw = pd.DataFrame([{"date": "2026-10-18", "portname": "Strait of Hormuz", "n_total_avg7": 3.6,
                        "n_total_avg30": 3.7, "n_tanker_avg30": 1.0, "n_total_avg7_vs_avg30_pct": -3,
                        "n_tanker_avg7": 0.7, "n_tanker_avg7_vs_avg30_pct": -29,
                        "n_total_avg7_vs_avg365_pct": -94, "n_tanker_avg7_vs_avg365_pct": -98}])
    flags = report.build_summary(pw, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()) \
        .set_index("location").loc["Strait of Hormuz", "flags"]
    assert flags == "tanker transits -98% vs 1-year average"
