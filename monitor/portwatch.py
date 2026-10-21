"""
portwatch.py: Phase 1. Daily chokepoint transit counts from IMF PortWatch.

This is your Phase 1 script turned into a module (a file of functions that
other files import). The teaching notes from that version still apply; the
main changes:
  * names come from config.py instead of a list here
  * the live service sends dates as "2026-10-18" text, so we parse text
  * it returns a DataFrame instead of saving a CSV; the caller decides where
    data goes (the sheet). Keeping "get data" separate from "store data"
    makes each piece testable on its own.

PortWatch refreshes weekly (Tuesdays), so most days this re-downloads the
same rows; the sheet upsert makes that harmless.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

BASE_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
PAGE_SIZE = 1000
KEEP_COLUMNS = ["date", "portname", "n_total", "n_tanker", "n_container",
                "n_dry_bulk", "n_general_cargo", "n_roro", "capacity_tanker", "capacity"]


def fetch_chokepoint(portname: str, first_year: int, session=requests) -> list[dict]:
    """Download every daily row for one chokepoint, page by page."""
    rows, offset = [], 0
    while True:
        params = {
            "where": f"portname = '{portname}' AND year >= {first_year}",
            "outFields": ",".join(KEEP_COLUMNS),
            "orderByFields": "date ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "false",
            "f": "json",
        }
        resp = session.get(BASE_URL, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"PortWatch error for {portname}: {payload['error']}")
        features = payload.get("features", [])
        rows.extend(f["attributes"] for f in features)
        if not payload.get("exceededTransferLimit", False) or not features:
            break
        offset += len(features)
        time.sleep(0.5)
    return rows


def to_dataframe(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if pd.api.types.is_numeric_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], unit="ms")   # epoch milliseconds
    else:
        df["date"] = pd.to_datetime(df["date"])              # "2026-10-18"
    return df.sort_values(["portname", "date"]).reset_index(drop=True)


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Tanker share, 7/30-day averages per chokepoint, and 7d-vs-30d deviation."""
    df = df.copy()
    df["tanker_share"] = (df["n_tanker"] / df["n_total"]).where(df["n_total"] > 0)
    by_port = df.groupby("portname")
    for col in ["n_total", "n_tanker"]:
        df[f"{col}_avg7"] = by_port[col].transform(lambda s: s.rolling(7, min_periods=7).mean())
        df[f"{col}_avg30"] = by_port[col].transform(lambda s: s.rolling(30, min_periods=30).mean())
        df[f"{col}_avg7_vs_avg30_pct"] = (df[f"{col}_avg7"] / df[f"{col}_avg30"] - 1) * 100
        # A 1-year baseline catches regime changes that a 30-day window has
        # already absorbed (e.g. Hormuz since March 2026: the 30-day average
        # is itself near zero, so "vs 30d" looks calm while traffic is ~95%
        # below normal).
        df[f"{col}_avg365"] = by_port[col].transform(lambda s: s.rolling(365, min_periods=180).mean())
        df[f"{col}_avg7_vs_avg365_pct"] = (df[f"{col}_avg7"] / df[f"{col}_avg365"] - 1) * 100
    return df


def find_gaps(df: pd.DataFrame) -> list[str]:
    """Rolling windows assume one row per day; report chokepoints with holes."""
    problems = []
    for name, g in df.groupby("portname"):
        expected = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        missing = expected.difference(g["date"])
        if len(missing):
            problems.append(f"{name}: {len(missing)} missing day(s), first {missing[0].date()}")
    return problems


def pull(portnames: list[str], first_year: int) -> tuple[pd.DataFrame, list[str]]:
    """Fetch, clean and enrich all chokepoints. Returns (table, warnings)."""
    frames = [to_dataframe(fetch_chokepoint(n, first_year)) for n in portnames]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(), ["PortWatch returned no rows"]
    data = pd.concat(frames, ignore_index=True)
    warnings = find_gaps(data)
    out = add_metrics(data)
    for col in out.columns:
        if out[col].dtype.kind == "f":
            out[col] = out[col].round(3)
    return out, warnings
