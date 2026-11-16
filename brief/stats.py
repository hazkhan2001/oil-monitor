"""
stats.py: the arithmetic behind every number in the brief.

Nothing in this file touches the internet. Every function takes a pandas
Series of daily closing prices (index = dates) and returns plain numbers.
That makes the maths testable offline: tests/test_brief_stats.py feeds in
made-up prices whose answers we can work out by hand.

LESSON: .pct_change() turns prices into daily returns.
    prices  100, 102, 99
    returns  NaN, +0.02, -0.0294
The first value is NaN (Not a Number) because day one has no "yesterday".

LESSON: .iloc[-1] is the last row, .iloc[-6] is six rows from the end.
"5 trading days ago" is iloc[-6] because the last row itself is day 0.
"""

from __future__ import annotations

import math

import pandas as pd


def _clean(prices: pd.Series) -> pd.Series:
    """Drop missing days and sort oldest to newest."""
    s = pd.to_numeric(prices, errors="coerce").dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def pct(new: float, old: float) -> float | None:
    """Percent change from old to new, or None if it can't be computed."""
    if old is None or new is None or old == 0 or math.isnan(old) or math.isnan(new):
        return None
    return (new / old - 1) * 100


def summarize(prices: pd.Series) -> dict:
    """Last price, 1-day, 5-day and 1-month changes, average daily return,
    20-day volatility, and an 'unusual move' flag.

    The flag fires when today's move is more than 2 standard deviations of
    the last 20 daily moves. LESSON: dividing a move by its typical size is
    the same idea as a Z-score: it says how surprising the move is for THIS
    instrument, so a 2% day in Bitcoin and a 2% day in a bond ETF are not
    treated alike.
    """
    s = _clean(prices)
    out = {"last": None, "chg_1d_pct": None, "chg_5d_pct": None, "chg_1m_pct": None,
           "avg_daily_5d_pct": None, "vol20_ann_pct": None, "move_z": None,
           "unusual": "", "asof": None}
    if len(s) == 0:
        return out
    out["last"] = float(s.iloc[-1])
    out["asof"] = s.index[-1].strftime("%Y-%m-%d")
    rets = s.pct_change().dropna() * 100            # daily returns in percent
    if len(s) >= 2:
        out["chg_1d_pct"] = pct(s.iloc[-1], s.iloc[-2])
    if len(s) >= 6:
        out["chg_5d_pct"] = pct(s.iloc[-1], s.iloc[-6])
        out["avg_daily_5d_pct"] = float(rets.iloc[-5:].mean())
    if len(s) >= 22:
        out["chg_1m_pct"] = pct(s.iloc[-1], s.iloc[-22])
    if len(rets) >= 21:
        window = rets.iloc[-21:-1]                    # the 20 days BEFORE today
        sd = window.std()
        out["vol20_ann_pct"] = float(rets.iloc[-20:].std() * math.sqrt(252))
        if sd and sd > 0:
            z = (rets.iloc[-1] - window.mean()) / sd
            out["move_z"] = float(z)
            out["unusual"] = "UNUSUAL" if abs(z) >= 2 else ""
    return out


def pair_stats(a: pd.Series, b: pd.Series, window: int = 20) -> dict:
    """Stats for a pair A/B using the price ratio, the way the PM's sheet does.

    Z-score = (today's ratio - average ratio over the window) / standard
    deviation of the ratio over the window.

    LESSON: pd.concat([...], axis=1) lines the two price series up side by
    side on their dates; .dropna() then keeps only days when BOTH traded, so
    a holiday in one market can't create a fake ratio.
    """
    both = pd.concat([_clean(a), _clean(b)], axis=1, keys=["a", "b"]).dropna()
    out = {"ratio": None, "z": None, "z_prev": None, "z_change": None,
           "ratio_1d_pct": None, "ratio_5d_pct": None, "direction": "", "asof": None}
    if len(both) < window + 1:
        return out
    ratio = both["a"] / both["b"]
    mean = ratio.rolling(window).mean()
    sd = ratio.rolling(window).std()
    z = (ratio - mean) / sd
    out["ratio"] = float(ratio.iloc[-1])
    out["asof"] = ratio.index[-1].strftime("%Y-%m-%d")
    out["z"] = float(z.iloc[-1])
    out["z_prev"] = float(z.iloc[-2]) if not math.isnan(z.iloc[-2]) else None
    if out["z_prev"] is not None:
        out["z_change"] = out["z"] - out["z_prev"]
        # Converging = |Z| shrinking toward 0 (the spread is reverting).
        out["direction"] = "converging" if abs(out["z"]) < abs(out["z_prev"]) else "diverging"
    out["ratio_1d_pct"] = pct(ratio.iloc[-1], ratio.iloc[-2])
    if len(ratio) >= 6:
        out["ratio_5d_pct"] = pct(ratio.iloc[-1], ratio.iloc[-6])
    return out


def crack_321(cl: float, rb: float, ho: float) -> float | None:
    """3-2-1 crack spread in dollars a barrel.

    Crude is quoted in $/barrel, gasoline (RB) and heating oil (HO) in
    $/gallon, and a barrel holds 42 gallons. Three barrels of crude make
    roughly two of gasoline and one of distillate, so:
        (2 * RB * 42 + 1 * HO * 42 - 3 * CL) / 3
    """
    if None in (cl, rb, ho):
        return None
    return (2 * rb * 42 + ho * 42 - 3 * cl) / 3


def parse_watchlist(rows: list[str]) -> list[dict]:
    """Turn the hand-typed Watchlist column into clean items.

    'xom'        -> {'kind': 'stock', 'a': 'XOM'}
    'PSX / VLO'  -> {'kind': 'pair', 'a': 'PSX', 'b': 'VLO'}
    Blank lines and lines starting with '#' are skipped, as are duplicates.
    """
    items, seen = [], set()
    for raw in rows:
        text = str(raw or "").strip().upper()
        if not text or text.startswith("#") or text in ("NAN", "NONE"):
            continue
        if "/" in text:
            a, b = (p.strip() for p in text.split("/", 1))
            if not a or not b:
                continue
            key, item = f"{a}/{b}", {"kind": "pair", "a": a, "b": b}
        else:
            key, item = text, {"kind": "stock", "a": text}
        if key not in seen:
            seen.add(key)
            items.append(item)
    return items
