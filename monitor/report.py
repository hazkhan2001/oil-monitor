"""
report.py: turn the raw tabs into a one-screen Summary with flags.

A flag is a plain-English note when a number is unusual against its own
history. The thresholds are starting points; tune them once you've watched
the data for a few weeks.

LESSON: a z-score says how unusual a value is: (value - average) / spread.
z = +2 means "two standard deviations above normal", which by chance
happens roughly 1 time in 40. It lets us compare a strait that sees 30
ships a scene with one that sees 300, on the same scale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config

PORTWATCH_FLAG_PCT = 15     # 7-day vs 30-day average beyond +/-15%
PORTWATCH_MIN_BASE = 5      # ...but only if the 30-day average is >= 5 ships/day.
                            # A % change on a tiny base is noise: 0.7 vs 1.0
                            # tankers a day is "-30%" and means nothing.
REGIME_FLAG_PCT = 40        # 7-day vs 1-year average beyond +/-40% = regime change
SAR_FLAG_Z = 2.0
STORAGE_FLAG_Z = 2.0


def add_sar_zscores(sar: pd.DataFrame) -> pd.DataFrame:
    """Vessel count vs the previous 10 scenes of the same box and orbit direction."""
    if sar.empty:
        return sar
    df = sar.copy()
    df["n_vessels"] = pd.to_numeric(df["n_vessels"], errors="coerce")
    df = df.sort_values("scene_time")
    grp = df.groupby(["aoi", "orbit_direction"])["n_vessels"]
    mean = grp.transform(lambda s: s.shift().rolling(10, min_periods=4).mean())
    std = grp.transform(lambda s: s.shift().rolling(10, min_periods=4).std())
    df["n_vessels_avg_prev10"] = mean.round(1)
    df["n_vessels_z"] = ((df["n_vessels"] - mean) / std.replace(0, np.nan)).round(2)
    return df


def _num(x):
    v = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(v) else float(v)


def build_summary(portwatch: pd.DataFrame, sar: pd.DataFrame, storage: pd.DataFrame,
                  calib: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for aoi in config.AOIS:
        row = {"location": aoi.name, "type": aoi.kind}
        flags = []
        if aoi.portwatch and not portwatch.empty:
            pw = portwatch[portwatch["portname"] == aoi.portwatch].sort_values("date")
            if len(pw):
                last = pw.iloc[-1]
                dev = _num(last.get("n_total_avg7_vs_avg30_pct"))
                tdev = _num(last.get("n_tanker_avg7_vs_avg30_pct"))
                yr = _num(last.get("n_total_avg7_vs_avg365_pct"))
                tyr = _num(last.get("n_tanker_avg7_vs_avg365_pct"))
                base = _num(last.get("n_total_avg30")) or 0
                tbase = _num(last.get("n_tanker_avg30")) or 0
                row.update({"portwatch_date": str(last["date"])[:10],
                            "transits_avg7": _num(last.get("n_total_avg7")),
                            "transits_7v30_pct": dev,
                            "transits_7v1y_pct": yr,
                            "tankers_avg7": _num(last.get("n_tanker_avg7")),
                            "tankers_7v30_pct": tdev,
                            "tankers_7v1y_pct": tyr})
                if tyr is not None and abs(tyr) >= REGIME_FLAG_PCT:
                    flags.append(f"tanker transits {tyr:+.0f}% vs 1-year average")
                elif yr is not None and abs(yr) >= REGIME_FLAG_PCT:
                    flags.append(f"transits {yr:+.0f}% vs 1-year average")
                if tdev is not None and tbase >= PORTWATCH_MIN_BASE and abs(tdev) >= PORTWATCH_FLAG_PCT:
                    flags.append(f"tanker transits {tdev:+.0f}% vs 30d")
                elif dev is not None and base >= PORTWATCH_MIN_BASE and abs(dev) >= PORTWATCH_FLAG_PCT:
                    flags.append(f"transits {dev:+.0f}% vs 30d")
        if aoi.kind != "tankfarm" and not sar.empty:
            s = sar[sar["aoi"] == aoi.key].sort_values("scene_time")
            if len(s):
                last = s.iloc[-1]
                z = _num(last.get("n_vessels_z"))
                dark_hi = _num(last.get("n_dark_high"))   # None = unknown (no AIS), not zero
                row.update({"last_scene": str(last["scene_time"])[:16],
                            "sar_vessels": _num(last.get("n_vessels")),
                            "sar_vessels_z": z,
                            "persistent_objects": _num(last.get("n_persistent")),
                            "dark_candidates": _num(last.get("n_dark")),
                            "dark_high_conf": dark_hi})
                if z is not None and abs(z) >= SAR_FLAG_Z:
                    flags.append(f"radar vessel count z={z:+.1f}")
                if dark_hi is not None and dark_hi >= 1:
                    flags.append(f"{int(dark_hi)} high-confidence dark candidate(s)")
        if aoi.kind == "tankfarm" and not storage.empty:
            s = storage[storage["aoi"] == aoi.key].sort_values("scene_time")
            if len(s):
                last = s.iloc[-1]
                z = _num(last.get("bright_frac_z_same_orbit"))
                row.update({"last_scene": str(last["scene_time"])[:16],
                            "storage_bright_frac": _num(last.get("bright_frac")),
                            "storage_z_same_orbit": z})
                usable = (not calib.empty) and calib["verdict"].astype(str).str.startswith("usable").any()
                row["storage_trust"] = "calibrated vs EIA" if usable else "uncalibrated"
                if z is not None and abs(z) >= STORAGE_FLAG_Z:
                    flags.append(f"storage index z={z:+.1f}" + ("" if usable else " (uncalibrated)"))
        row["flags"] = "; ".join(flags)
        rows.append(row)
    return pd.DataFrame(rows)
