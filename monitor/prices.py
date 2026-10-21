"""
prices.py: daily Brent and WTI crude spot prices from the EIA (free, no key).

Same file layout as the Cushing stocks file: an old-style .xls with the data on
the sheet called "Data 1", two header rows to skip, then date and value.

LESSON: merge() joins two tables on a shared column (like a spreadsheet
VLOOKUP, but for whole tables). An "outer" join keeps every date from either
table, so one missing day in a series doesn't delete the other's row.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

URLS = {
    "brent": "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls",
    "wti": "https://www.eia.gov/dnav/pet/hist_xls/RWTCd.xls",
}


def _one(url: str, name: str, session=requests) -> pd.DataFrame:
    resp = session.get(url, timeout=60, headers={"User-Agent": "oil-monitor/1.0"})
    resp.raise_for_status()
    raw = pd.read_excel(io.BytesIO(resp.content), sheet_name="Data 1", skiprows=2)
    df = raw.iloc[:, :2].copy()
    df.columns = ["date", name]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df[name] = pd.to_numeric(df[name], errors="coerce")
    return df.dropna(subset=["date"])


def combine(brent: pd.DataFrame, wti: pd.DataFrame) -> pd.DataFrame:
    df = brent.merge(wti, on="date", how="outer").sort_values("date")
    df["brent_wti_spread"] = (df["brent"] - df["wti"]).round(2)   # Brent minus WTI, dollars a barrel
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.dropna(subset=["brent", "wti"], how="all").reset_index(drop=True)


def fetch(session=requests) -> pd.DataFrame:
    return combine(*(_one(URLS[k], k, session) for k in ("brent", "wti")))
