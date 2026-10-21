"""
storage.py: Phase 4. A radar storage index for tank farms, tested against EIA.

THE PHYSICS (and its limits)
    Most big crude tanks have floating roofs that ride on the oil. Radar hits
    the tank wall and the roof together and bounces back strongly (a "double
    bounce"). As the roof sinks, more wall is exposed and the signature
    changes. Commercial firms read this per tank at sub-metre resolution.
    At Sentinel-1's 10 m a tank is only 5-10 pixels wide, so we can't measure
    a single tank. We measure the farm as a whole:
        bright_frac = share of pixels brighter than BRIGHT_DB
        p95_db      = brightness of the brightest 5% of pixels

WHY CALIBRATION COMES FIRST
    Radar brightness also depends on viewing angle, so a scene from an
    ascending orbit can't be compared with a descending one. We therefore
    compare each scene only with earlier scenes from the *same relative
    orbit* (same angle). Then for Cushing, where EIA publishes the real stock
    level every week, we check whether the index tracks it. If it doesn't
    (|correlation| below 0.5 after 20+ scenes), the sheet says so, and the
    index for the other farms should be treated as noise.

LESSON: np.interp fills in a value between two known points on a line.
EIA reports stocks every Friday; a scene on Tuesday gets a value 4/7 of the
way between the surrounding Fridays.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import requests

EIA_CUSHING_XLS = "https://www.eia.gov/dnav/pet/hist_xls/W_EPC0_SAX_YCUOK_MBBLw.xls"
BRIGHT_DB = 0.0
MIN_SCENES_FOR_VERDICT = 20


def farm_metrics(raster) -> dict:
    vals = raster.db[raster.valid]
    if vals.size == 0:
        return {"valid_px": 0}
    return {
        "valid_px": int(vals.size),
        "mean_db": round(float(vals.mean()), 3),
        "p95_db": round(float(np.percentile(vals, 95)), 3),
        "bright_frac": round(float((vals > BRIGHT_DB).mean()), 5),
    }


def add_orbit_zscores(df: pd.DataFrame, col: str = "bright_frac") -> pd.DataFrame:
    """Z-score of each scene vs earlier scenes from the same farm and geometry."""
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("scene_time")
    grp = df.groupby(["aoi", "relative_orbit"])[col]
    mean = grp.transform(lambda s: s.shift().expanding(min_periods=4).mean())
    std = grp.transform(lambda s: s.shift().expanding(min_periods=4).std())
    df[f"{col}_z_same_orbit"] = ((df[col] - mean) / std).round(2)
    return df


def fetch_cushing_stocks(session=requests) -> pd.DataFrame:
    """EIA weekly Cushing crude stocks (thousand barrels), as date/stocks."""
    resp = session.get(EIA_CUSHING_XLS, timeout=60, headers={"User-Agent": "oil-monitor/1.0"})
    resp.raise_for_status()
    raw = pd.read_excel(io.BytesIO(resp.content), sheet_name="Data 1", skiprows=2)
    df = raw.iloc[:, :2].copy()
    df.columns = ["date", "cushing_kbbl"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["cushing_kbbl"] = pd.to_numeric(df["cushing_kbbl"], errors="coerce")
    return df.dropna().sort_values("date").reset_index(drop=True)


def calibrate(index: pd.DataFrame, stocks: pd.DataFrame, farm: str = "cushing") -> pd.DataFrame:
    """Correlation between the radar index and EIA stocks, per orbit geometry."""
    rows = index[index["aoi"] == farm].copy()
    if rows.empty or stocks.empty:
        return pd.DataFrame()
    t = pd.to_datetime(rows["scene_time"], utc=True).dt.tz_localize(None)
    x = stocks["date"].astype("int64").values
    rows["eia_kbbl_interp"] = np.interp(t.astype("int64").values, x, stocks["cushing_kbbl"].values)
    out = []
    for orbit, g in rows.groupby("relative_orbit"):
        for metric in ["bright_frac", "p95_db", "mean_db"]:
            v = pd.to_numeric(g[metric], errors="coerce")
            ok = v.notna()
            n = int(ok.sum())
            r = float(np.corrcoef(v[ok], g.loc[ok, "eia_kbbl_interp"])[0, 1]) if n >= 3 and v[ok].std() > 0 else np.nan
            if n < MIN_SCENES_FOR_VERDICT:
                verdict = f"collecting ({n}/{MIN_SCENES_FOR_VERDICT} scenes)"
            elif abs(r) >= 0.5:
                verdict = "usable: tracks EIA"
            else:
                verdict = "NOT usable: does not track EIA"
            out.append({"farm": farm, "relative_orbit": orbit, "metric": metric, "n_scenes": n,
                        "pearson_r": round(r, 3) if r == r else "", "verdict": verdict})
    return pd.DataFrame(out)
