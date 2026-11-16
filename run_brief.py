"""
run_brief.py: the numbers half of the morning brief.

Runs on GitHub Actions on weekday mornings (see .github/workflows/brief.yml)
and writes these tabs into the same Google Sheet as the Oil Monitor:

    brief_markets    indices, futures, commodities, FX, plus a crack spread
    brief_yields     US curve (official Treasury) and foreign 10Y/2Y yields
    brief_weather    today's forecast for home and the major capitals
    brief_watchlist  stats for every ticker and pair on the Watchlist tab
    brief_status     one row per step: ok / warning / error, and why

Hamza edits only the "Watchlist" tab. Claude reads the rest at 6:45 AM and
writes the words.

LESSON: each step is wrapped in try/except inside main(). If Yahoo is down,
the weather and yields still get written, and brief_status records exactly
which step failed. A scheduled job you can't watch has to report on itself.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone

import pandas as pd

from brief import sources, stats
from brief.universe import (CITIES, INTL_YIELDS, MARKETS, PAIR_Z_WINDOW, TAB_MARKETS, TAB_STATUS,
                            TAB_WATCH, TAB_WATCHLIST_INPUT, TAB_WEATHER, TAB_YIELDS)
from monitor.sheets import SheetWriter

RUN_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
STATUS: list[dict] = []


def log(step: str, status: str, detail: str = "") -> None:
    STATUS.append({"run_utc": RUN_UTC, "step": step, "status": status, "detail": detail[:500]})
    print(f"[{status}] {step}: {detail}")


def r(x, d=2):
    """Round if it's a number, otherwise pass None through."""
    return None if x is None else round(x, d)


# ---- steps --------------------------------------------------------------------
def step_markets(sheet: SheetWriter) -> None:
    symbols = [m[2] for m in MARKETS]
    data = sources.closes(symbols)
    rows, missing = [], []
    for group, name, sym in MARKETS:
        if sym not in data:
            missing.append(sym)
            continue
        s = stats.summarize(data[sym])
        rows.append({"group": group, "name": name, "symbol": sym, "last": r(s["last"], 4),
                     "chg_1d_pct": r(s["chg_1d_pct"]), "chg_5d_pct": r(s["chg_5d_pct"]),
                     "chg_1m_pct": r(s["chg_1m_pct"]), "avg_daily_5d_pct": r(s["avg_daily_5d_pct"], 3),
                     "move_z": r(s["move_z"]), "unusual": s["unusual"], "asof": s["asof"]})
    # 3-2-1 crack spread from the three front-month futures we already have.
    last = {sym: float(data[sym].iloc[-1]) for sym in ("CL=F", "RB=F", "HO=F") if sym in data}
    crack = stats.crack_321(last.get("CL=F"), last.get("RB=F"), last.get("HO=F"))
    if crack is not None:
        rows.append({"group": "Energy", "name": "3-2-1 crack ($/bbl)", "symbol": "calc",
                     "last": r(crack), "asof": data["CL=F"].index[-1].strftime("%Y-%m-%d")})
    sheet.replace(TAB_MARKETS, pd.DataFrame(rows))
    log("markets", "warning" if missing else "ok",
        f"{len(rows)} rows" + (f"; Yahoo returned nothing for {', '.join(missing)}" if missing else ""))


def step_us_yields() -> list[dict]:
    df = sources.us_curve()
    rows = []
    cols = {"2Y": "2 Yr", "10Y": "10 Yr", "30Y": "30 Yr"}
    for tenor, col in cols.items():
        v = pd.to_numeric(df[col], errors="coerce")
        rows.append({"country": "United States", "tenor": tenor, "yield_pct": r(v.iloc[0], 3),
                     "chg_1d_bp": r((v.iloc[0] - v.iloc[1]) * 100, 1),
                     "chg_5d_bp": r((v.iloc[0] - v.iloc[5]) * 100, 1),
                     "asof": df["Date"].iloc[0].strftime("%Y-%m-%d"),
                     "source": "US Treasury (official close)"})
    two, ten = rows[0]["yield_pct"], rows[1]["yield_pct"]
    rows.append({"country": "United States", "tenor": "2s10s (bp)", "yield_pct": r((ten - two) * 100, 1),
                 "asof": rows[0]["asof"], "source": "calc"})
    log("us_yields", "ok", f"as of {rows[0]['asof']}: 2Y {two}, 10Y {ten}, 30Y {rows[2]['yield_pct']}")
    return rows


def step_intl_yields() -> list[dict]:
    every = [s for _, _, cands in INTL_YIELDS for s in cands]
    quotes = sources.cnbc_quotes(every)
    rows, missing = [], []
    for country, tenor, cands in INTL_YIELDS:
        hit = next((s for s in cands if s in quotes), None)
        if hit is None:
            missing.append(f"{country} {tenor}")
            continue
        q = quotes[hit]
        # CNBC quotes a yield's change in percentage points; x100 gives basis points.
        rows.append({"country": country, "tenor": tenor, "yield_pct": r(q["last"], 3),
                     "chg_1d_bp": r(q["change"] * 100, 1) if q["change"] is not None else None,
                     "asof": q["time"], "source": f"CNBC {hit}"})
    log("intl_yields", "warning" if missing else "ok",
        f"{len(rows)} found" + (f"; not found: {', '.join(missing)}" if missing else ""))
    return rows


def step_yields(sheet: SheetWriter) -> None:
    rows = []
    for fn in (step_us_yields, step_intl_yields):
        try:
            rows += fn()
        except Exception as exc:
            log(fn.__name__.replace("step_", ""), "error", repr(exc))
    sheet.replace(TAB_YIELDS, pd.DataFrame(rows))


def step_weather(sheet: SheetWriter) -> None:
    df = sources.weather(CITIES)
    sheet.replace(TAB_WEATHER, df)
    home = df.iloc[0]
    log("weather", "ok", f"{len(df)} cities; {home.city} high {home.high_f}F, {home.conditions}")


def step_watchlist(sheet: SheetWriter) -> None:
    raw = sheet.read(TAB_WATCHLIST_INPUT)
    if raw.empty:
        # First run: create the tab with a few example rows to overwrite.
        raw = pd.DataFrame({"item": ["XOM", "CVX", "PSX/VLO"],
                            "note": ["example: one ticker per row", "example", "example: a pair as A/B"]})
        sheet.replace(TAB_WATCHLIST_INPUT, raw)
        log("watchlist", "warning", "Watchlist tab was empty, so example rows were added")
    items = stats.parse_watchlist(list(raw.iloc[:, 0]))
    notes = dict(zip(raw.iloc[:, 0].astype(str).str.strip().str.upper(),
                     raw.iloc[:, 1] if raw.shape[1] > 1 else [""] * len(raw)))
    tickers = sorted({i["a"] for i in items} | {i["b"] for i in items if i["kind"] == "pair"})
    data = sources.closes(tickers, period="1y") if tickers else {}
    rows, missing = [], [t for t in tickers if t not in data]
    for it in items:
        if it["kind"] == "stock":
            if it["a"] not in data:
                continue
            s = stats.summarize(data[it["a"]])
            rows.append({"item": it["a"], "kind": "stock", "last": r(s["last"]),
                         "chg_1d_pct": r(s["chg_1d_pct"]), "chg_5d_pct": r(s["chg_5d_pct"]),
                         "avg_daily_5d_pct": r(s["avg_daily_5d_pct"], 3), "chg_1m_pct": r(s["chg_1m_pct"]),
                         "vol20_ann_pct": r(s["vol20_ann_pct"], 1), "unusual": s["unusual"],
                         "next_earnings": sources.next_earnings(it["a"]),
                         "note": notes.get(it["a"], ""), "asof": s["asof"]})
        else:
            a, b = it["a"], it["b"]
            if a not in data or b not in data:
                continue
            p = stats.pair_stats(data[a], data[b], PAIR_Z_WINDOW)
            sa, sb = stats.summarize(data[a]), stats.summarize(data[b])
            rows.append({"item": f"{a}/{b}", "kind": "pair", "last": r(p["ratio"], 4),
                         "chg_1d_pct": r(p["ratio_1d_pct"]), "chg_5d_pct": r(p["ratio_5d_pct"]),
                         "z20": r(p["z"]), "z20_prev": r(p["z_prev"]), "z_direction": p["direction"],
                         "leg_a_1d_pct": r(sa["chg_1d_pct"]), "leg_b_1d_pct": r(sb["chg_1d_pct"]),
                         "note": notes.get(f"{a}/{b}", notes.get(f"{a} / {b}", "")), "asof": p["asof"]})
    sheet.replace(TAB_WATCH, pd.DataFrame(rows))
    log("watchlist", "warning" if missing else "ok",
        f"{len(rows)} items" + (f"; no prices for {', '.join(missing)}" if missing else ""))


def main() -> int:
    sheet = SheetWriter()
    log("start", "ok", f"sheet mode: {sheet.mode}")
    for name, fn in (("markets", step_markets), ("yields", step_yields),
                     ("weather", step_weather), ("watchlist", step_watchlist)):
        try:
            fn(sheet)
        except Exception as exc:
            log(name, "error", f"{exc!r} | {traceback.format_exc()[-300:]}")
    sheet.replace(TAB_STATUS, pd.DataFrame(STATUS))
    # Exit code 1 only if every data step failed, so GitHub emails a failure.
    return 1 if all(s["status"] == "error" for s in STATUS[1:]) else 0


if __name__ == "__main__":
    sys.exit(main())
