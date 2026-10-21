"""
behavior.py: summarise how ships are behaving inside each AIS recording window.

LESSON: groupby() splits a table into one chunk per key (here, per area) so we
can run the same summary on each chunk. We keep only the *latest* report per
ship (drop_duplicates) so a ship that reported 50 times counts once.
"""
from __future__ import annotations

import gzip
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SLOW_KN = 1.0  # at or below this a ship is effectively stopped (anchored or queuing)
COLUMNS = ["window", "aoi", "n_ships", "n_tankers", "tanker_share", "tanker_median_sog",
           "tanker_slow_share", "median_length_m", "tanker_mean_cog_sin", "tanker_mean_cog_cos"]


def is_tanker(ship_type) -> pd.Series:
    t = pd.to_numeric(ship_type, errors="coerce")
    return t.between(80, 89)  # AIS ship-type codes 80-89 are tankers


def summarize(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """One row per area for one recording. Empty input gives an empty table."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    df = df.sort_values("time").drop_duplicates(["aoi", "mmsi"], keep="last")
    rows = []
    for aoi, g in df.groupby("aoi"):
        tk = g[is_tanker(g["ship_type"])]
        sog = pd.to_numeric(tk["sog"], errors="coerce")
        sog = sog[sog < 102.3]  # 102.3 is the AIS code for "speed not available"
        cog = pd.to_numeric(tk["cog"], errors="coerce")
        cog = cog[cog < 360]  # 360 means "course not available"
        rad = np.deg2rad(cog)
        n = len(g)
        rows.append({
            "window": window, "aoi": aoi, "n_ships": n, "n_tankers": len(tk),
            "tanker_share": round(len(tk) / n, 3) if n else None,
            "tanker_median_sog": round(float(sog.median()), 1) if len(sog) else None,
            "tanker_slow_share": round(float((sog <= SLOW_KN).mean()), 3) if len(sog) else None,
            "median_length_m": round(float(pd.to_numeric(g["length_m"], errors="coerce").median()), 0)
            if pd.to_numeric(g["length_m"], errors="coerce").notna().any() else None,
            # direction as a vector average (sin, cos) so 359 deg and 1 deg don't average to 180
            "tanker_mean_cog_sin": round(float(np.sin(rad).mean()), 3) if len(rad) else None,
            "tanker_mean_cog_cos": round(float(np.cos(rad).mean()), 3) if len(rad) else None,
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def summarize_dir(ais_dir: Path) -> pd.DataFrame:
    parts = []
    for path in sorted(ais_dir.glob("*.csv.gz")):
        try:
            datetime.strptime(path.name[:13], "%Y%m%dT%H%M")
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                df = pd.read_csv(fh)
        except (ValueError, OSError, pd.errors.EmptyDataError):
            continue
        parts.append(summarize(df, path.name[:13]))
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
