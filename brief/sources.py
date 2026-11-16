"""
sources.py: everything that talks to the internet for the morning brief.

Four free sources, none needing a key:
    Yahoo Finance (via the yfinance library)  indices, futures, FX, stocks
    US Treasury daily par yield curve (CSV)   the official US 2Y/10Y/30Y
    CNBC quote service                        foreign government bond yields
    Open-Meteo                                weather forecasts

LESSON: every fetcher returns data OR raises an exception. It never returns
half-made-up numbers. run_brief.py catches the exception, writes it to the
brief_status tab, and moves on, so one dead source never sinks the others.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (morning-brief; github.com/hazkhan2001/oil-monitor)"}


# ---- Yahoo Finance --------------------------------------------------------
def closes(symbols: list[str], period: str = "6mo") -> dict[str, pd.Series]:
    """Daily closing prices for many symbols in one request.

    Returns {symbol: Series of closes}. Symbols Yahoo doesn't know are simply
    missing from the result; the caller reports them.

    LESSON: yf.download with group_by="ticker" gives a table whose columns are
    two-level: (symbol, field). df["SPY"]["Close"] picks one column out.
    """
    import yfinance as yf  # imported here so offline tests don't need it

    df = yf.download(symbols, period=period, interval="1d", auto_adjust=False,
                     group_by="ticker", progress=False, threads=True)
    out = {}
    for sym in symbols:
        try:
            col = df[sym]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
        except KeyError:
            continue
        col = pd.to_numeric(col, errors="coerce").dropna()
        if len(col):
            col.index = pd.to_datetime(col.index).tz_localize(None)
            out[sym] = col
    return out


def next_earnings(symbol: str) -> str:
    """Next earnings date as 'YYYY-MM-DD', or '' if Yahoo doesn't say."""
    import yfinance as yf

    try:
        cal = yf.Ticker(symbol).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            d = dates[0]
            return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
    except Exception:
        pass
    return ""


# ---- US Treasury ------------------------------------------------------------
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")


def us_curve(session=requests) -> pd.DataFrame:
    """Official US Treasury par yields, newest first, for this year and last.

    Published around 6 PM New York time each business day, so the morning
    brief always shows the previous session's close. The 'asof' column says so.
    """
    year = datetime.now(timezone.utc).year
    frames = []
    for y in (year, year - 1):
        r = session.get(TREASURY_URL.format(year=y), timeout=60, headers=UA)
        r.raise_for_status()
        frames.append(pd.read_csv(io.StringIO(r.text)))
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    return df.sort_values("Date", ascending=False).reset_index(drop=True)


# ---- CNBC (foreign yields) ------------------------------------------------
CNBC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            "?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1")


def _num(text) -> float | None:
    try:
        return float(str(text).replace("%", "").replace(",", "").replace("+", "").strip())
    except ValueError:
        return None


def cnbc_quotes(symbols: list[str], session=requests) -> dict[str, dict]:
    """{symbol: {'last': float, 'change': float, 'time': str}} for symbols
    CNBC recognises. Unknown symbols come back without a price and are left out.
    """
    r = session.get(CNBC_URL.format(syms="|".join(symbols)), timeout=30, headers=UA)
    r.raise_for_status()
    quotes = r.json().get("FormattedQuoteResult", {}).get("FormattedQuote", [])
    if isinstance(quotes, dict):        # one symbol comes back as a dict, not a list
        quotes = [quotes]
    out = {}
    for q in quotes:
        last = _num(q.get("last"))
        if last is None:
            continue
        out[q.get("symbol")] = {"last": last, "change": _num(q.get("change")),
                                "time": str(q.get("last_time", ""))[:16]}
    return out


# ---- Open-Meteo -------------------------------------------------------------
WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
       51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
       71: "Snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
       82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms",
       96: "Thunderstorms", 99: "Thunderstorms"}


def weather(cities: list[tuple], session=requests) -> pd.DataFrame:
    """Today's high, low, rain chance and conditions for every city, in one call.

    LESSON: Open-Meteo accepts comma-separated latitudes and longitudes and
    answers with a list in the same order, so zip() pairs each answer back
    with its city name.
    """
    params = {
        "latitude": ",".join(str(c[1]) for c in cities),
        "longitude": ",".join(str(c[2]) for c in cities),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "current": "temperature_2m",
        "timezone": "auto",
        "forecast_days": 1,
    }
    r = session.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=60, headers=UA)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    rows = []
    for (name, _, _), d in zip(cities, data):
        day = d.get("daily", {})
        hi, lo = day.get("temperature_2m_max", [None])[0], day.get("temperature_2m_min", [None])[0]
        c_to_f = lambda c: None if c is None else round(c * 9 / 5 + 32)
        rows.append({
            "city": name,
            "high_f": c_to_f(hi), "low_f": c_to_f(lo),
            "high_c": None if hi is None else round(hi), "low_c": None if lo is None else round(lo),
            "now_c": d.get("current", {}).get("temperature_2m"),
            "rain_pct": day.get("precipitation_probability_max", [None])[0],
            "conditions": WMO.get(day.get("weather_code", [None])[0], ""),
            "local_date": day.get("time", [""])[0],
        })
    return pd.DataFrame(rows)
