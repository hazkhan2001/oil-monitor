"""
render.py: turns the sheet's brief_* tabs plus Claude's written notes into
the Bloomberg-style HTML page.

    python3 -I brief/render.py SHEET.xlsx NOTES.json OUT.html

SHEET.xlsx  the Oil Monitor sheet exported from Google Drive
NOTES.json  what Claude wrote this morning (schema in the docstring of
            render() below)
OUT.html    the page that gets published

LESSON: splitting "numbers" (the sheet) from "words" (the JSON) means the
morning write-up can never mistype a price. Every figure in a table comes
straight from the sheet; the JSON only holds prose, links and the three
yields that have no free data feed (clearly labelled "web").

LESSON: html.escape() turns characters like < and & into safe codes, so a
headline containing "<script>" shows up as text instead of running.
"""

from __future__ import annotations

import html
import json
import math
import sys
from datetime import datetime

import pandas as pd

E = html.escape


# ---- small formatting helpers ------------------------------------------------
def num(v):
    """Sheet cells arrive as text; turn them into floats, or None if blank."""
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def fmt_level(v, d=None):
    v = num(v)
    if v is None:
        return "&ndash;"
    if d is None:
        d = 0 if abs(v) >= 10000 else 2 if abs(v) >= 1 else 4
    return f"{v:,.{d}f}"


def chg(v, unit="%", d=2):
    """A coloured change: green up, red down, grey flat or missing."""
    v = num(v)
    if v is None:
        return '<span class="flat">&ndash;</span>'
    cls = "up" if v > 0 else "dn" if v < 0 else "flat"
    sign = "+" if v > 0 else ""
    return f'<span class="{cls}">{sign}{v:.{d}f}{unit}</span>'


def safe_url(u: str) -> str:
    u = str(u or "")
    return E(u) if u.startswith("https://") else ""


def src_link(item: dict) -> str:
    url, name = safe_url(item.get("url")), E(item.get("source", ""))
    if not name:
        return ""
    return f' <a class="src" href="{url}" target="_blank" rel="noopener">{name}</a>' if url else f' <span class="src">{name}</span>'


def tab(x: dict, name: str) -> pd.DataFrame:
    d = x.get(name)
    return d if d is not None else pd.DataFrame()


# ---- page sections ----------------------------------------------------------
def news_panel(title: str, code: str, items: list[dict]) -> str:
    if not items:
        return ""
    lis = "".join(
        f'<li><span class="tag">{E(i.get("tag", ""))}</span><span>{E(i.get("text", ""))}{src_link(i)}</span></li>'
        for i in items)
    return f'<section class="panel"><h2><span class="fn">{code}</span>{E(title)}</h2><ul class="news">{lis}</ul></section>'


def tape(mk: pd.DataFrame, yl: pd.DataFrame) -> str:
    """The ticker strip under the header: the eight numbers that set the tone."""
    picks = [("ES=F", "S&P FUT"), ("NQ=F", "NDX FUT"), ("BZ=F", "BRENT"), ("CL=F", "WTI"),
             ("DX-Y.NYB", "DXY"), ("^VIX", "VIX"), ("GC=F", "GOLD")]
    cells = []
    for sym, label in picks:
        row = mk[mk["symbol"] == sym]
        if len(row):
            r = row.iloc[0]
            cells.append(f'<div class="tk"><b>{label}</b><span>{fmt_level(r["last"])}</span>{chg(r["chg_1d_pct"])}</div>')
    us10 = yl[(yl["country"] == "United States") & (yl["tenor"] == "10Y")] if len(yl) else yl
    if len(us10):
        r = us10.iloc[0]
        cells.insert(4, f'<div class="tk"><b>UST 10Y</b><span>{fmt_level(r["yield_pct"], 3)}</span>{chg(r["chg_1d_bp"], "bp", 1)}</div>')
    return f'<div class="tape">{"".join(cells)}</div>'


def markets_table(mk: pd.DataFrame, groups: list[str], title: str, code: str) -> str:
    sub = mk[mk["group"].isin(groups)] if len(mk) else mk
    if not len(sub):
        return ""
    rows = []
    for g in groups:
        part = sub[sub["group"] == g]
        if not len(part):
            continue
        rows.append(f'<tr class="grp"><td colspan="6">{E(g.upper())}</td></tr>')
        for r in part.itertuples():
            flag = ' <span class="flag">UNUSUAL</span>' if str(r.unusual) == "UNUSUAL" else ""
            rows.append(f"<tr><td>{E(r.name)}{flag}</td><td class='n'>{fmt_level(r.last)}</td>"
                        f"<td class='n'>{chg(r.chg_1d_pct)}</td><td class='n'>{chg(r.chg_5d_pct)}</td>"
                        f"<td class='n'>{chg(r.chg_1m_pct)}</td><td class='n d'>{E(str(r.asof))[5:]}</td></tr>")
    return (f'<section class="panel"><h2><span class="fn">{code}</span>{E(title)}</h2><div class="scroll"><table>'
            f'<thead><tr><th>Name</th><th>Last</th><th>1D</th><th>5D</th><th>1M</th><th>As of</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def watch_table(wl: pd.DataFrame, notes: dict) -> str:
    if not len(wl):
        return ""
    rows = []
    for r in wl.itertuples():
        pair = r.kind == "pair"
        z = num(getattr(r, "z20", None))
        zcell = "&ndash;"
        if pair and z is not None:
            hot = " hot" if abs(z) >= 2 else ""
            arrow = {"converging": "conv", "diverging": "div"}.get(str(getattr(r, "z_direction", "")), "")
            zcell = f'<span class="z{hot}">{z:+.2f}</span> <span class="d">{arrow}</span>'
        earn = str(getattr(r, "next_earnings", "") or "")
        earn = "" if earn in ("nan", "None") else earn
        flag = ' <span class="flag">UNUSUAL</span>' if str(getattr(r, "unusual", "")) == "UNUSUAL" else ""
        note = notes.get(str(r.item), {})
        news = (f'<tr class="wn"><td colspan="7" class="wnote">{E(note.get("text", ""))}{src_link(note)}</td></tr>'
                if note.get("text") else "")
        rows.append(
            f"<tr class='{'hasn' if news else ''}'><td><b>{E(str(r.item))}</b>{flag}</td><td class='n'>{fmt_level(r.last, 4 if pair else 2)}</td>"
            f"<td class='n'>{chg(r.chg_1d_pct)}</td><td class='n'>{chg(r.chg_5d_pct)}</td>"
            f"<td class='n'>{chg(getattr(r, 'avg_daily_5d_pct', None), '%', 3)}</td>"
            f"<td class='n'>{zcell}</td><td class='n d'>{E(earn[5:] if earn else '')}</td></tr>" + news)
    return ('<section class="panel"><h2><span class="fn">WL</span>Watchlist</h2><div class="scroll"><table>'
            '<thead><tr><th>Item</th><th>Last</th><th>1D</th><th>5D</th><th title="Average daily return over 5 days">Avg/day</th>'
            '<th title="20-day Z-score of the price ratio">Z20</th><th>Earns</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="foot">Edit the Watchlist tab of the Oil Monitor sheet: one ticker per row, or a pair as A/B. '
            'Pairs show the price ratio as Last and its 20-day Z-score as Z20 (conv = moving back toward 0). Avg/day is the mean daily return over 5 sessions.</p></section>')


def yields_table(yl: pd.DataFrame, fills: list[dict]) -> str:
    rows = []
    for r in (yl.itertuples() if len(yl) else []):
        bp = "bp" if "bp" not in str(r.tenor) else ""
        level = fmt_level(r.yield_pct, 1 if "bp" in str(r.tenor) else 3) + ("" if bp == "" else "%")
        country = "US" if str(r.country) == "United States" else str(r.country)
        rows.append(f"<tr><td>{E(country)}</td><td>{E(str(r.tenor))}</td><td class='n'>{level}</td>"
                    f"<td class='n'>{chg(r.chg_1d_bp, 'bp', 1)}</td><td class='n'>{chg(getattr(r, 'chg_5d_bp', None), 'bp', 1)}</td>"
                    f"<td class='n d'>{E(str(r.asof))[5:10]}</td></tr>")
    for f in fills:
        asof = str(f.get('asof', ''))
        asof = asof[5:10] + ' web' if asof[:4].isdigit() else 'n/a'
        rows.append(f"<tr class='web'><td>{E(str(f.get('country', '')))}{src_link(f)}</td><td>{E(str(f.get('tenor', '')))}</td>"
                    f"<td class='n'>{fmt_level(f.get('yield_pct'), 3) + ('%' if num(f.get('yield_pct')) is not None else '')}</td>"
                    f"<td class='n'>{chg(f.get('chg_1d_bp'), 'bp', 1)}</td>"
                    f"<td class='n'>&ndash;</td><td class='n d'>{E(asof)}</td></tr>")
    if not rows:
        return ""
    return ('<section class="panel"><h2><span class="fn">GOVT</span>Sovereign yields</h2><div class="scroll"><table>'
            '<thead><tr><th>Country</th><th style="text-align:left">Tenor</th><th>Yield</th><th>1D</th><th>5D</th><th>As of</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="foot">US from the Treasury\'s official close; others from CNBC quotes. Rows marked web come '
            'from a news or data site that morning; n/a means no free daily quote exists (the UAE has no local benchmark).</p></section>')


def weather_table(wx: pd.DataFrame) -> str:
    if not len(wx):
        return ""
    rows = []
    for i, r in enumerate(wx.itertuples()):
        us = i < 3  # home, Midtown and Washington read better in Fahrenheit
        hi_lo = (f"{fmt_level(r.high_f, 0)}&deg;/{fmt_level(r.low_f, 0)}&deg;F" if us
                 else f"{fmt_level(r.high_c, 0)}&deg;/{fmt_level(r.low_c, 0)}&deg;C")
        rain = num(r.rain_pct)
        rows.append(f"<tr{' class=home' if i < 2 else ''}><td>{E(str(r.city))}</td><td class='n'>{hi_lo}</td>"
                    f"<td>{E(str(r.conditions))}</td><td class='n d'>{'' if rain is None else f'{rain:.0f}%'}</td></tr>")
    return ('<section class="panel"><h2><span class="fn">WX</span>Weather today</h2><div class="scroll"><table>'
            '<thead><tr><th>City</th><th>High / low</th><th>Conditions</th><th>Rain</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def calendar_panel(cal: list[dict]) -> str:
    if not cal:
        return ""
    rows = "".join(f"<tr><td class='when'>{E(c.get('when', ''))}</td><td><b>{E(c.get('event', ''))}</b>"
                   f"<div class='d'>{E(c.get('detail', ''))}</div></td></tr>" for c in cal)
    return (f'<section class="panel"><h2><span class="fn">ECO</span>Coming up</h2>'
            f'<table class="cal"><tbody>{rows}</tbody></table></section>')


def oil_panel(om: dict) -> str:
    if not om:
        return ""
    rows = "".join(f"<tr><td>{E(str(a))}</td><td class='n'>{E(str(b))}</td><td class='n'>"
                   f"{'<span class=flag>' + E(str(c)) + '</span>' if c and str(c) not in ('Normal', '') else E(str(c or ''))}</td></tr>"
                   for a, b, c in om.get("rows", []))
    link = safe_url(om.get("url"))
    more = f' <a class="src" href="{link}" target="_blank" rel="noopener">Oil Monitor dashboard</a>' if link else ""
    return (f'<section class="panel"><h2><span class="fn">OIL</span>Oil Monitor</h2>'
            f'<p class="prose">{E(om.get("summary", ""))}{more}</p>'
            + (f'<div class="scroll"><table><tbody>{rows}</tbody></table></div>' if rows else "") + '</section>')


def status_line(st: pd.DataFrame) -> str:
    if not len(st):
        return "Data job status unavailable."
    run = str(st["run_utc"].iloc[0])
    probs = st[st["status"].isin(["error", "warning"])]
    bits = [f"{E(str(r.step))}: {E(str(r.detail))}" for r in probs.itertuples()]
    return f"Data job ran {E(run)} UTC" + (f". Notes: {'; '.join(bits)}." if bits else ", all sources ok.")


# ---- the whole page -----------------------------------------------------------
CSS = """
:root{
  /* Layout: terminal header bar, ticker tape, lead brief, then a two-column grid of panels */
  --bg:#050505; --panel:#0e0e0d; --line:#2a2722; --fg:#e9e6df; --dim:#8f8a80;
  --amber:#ff9e1b; --amber-ink:#0a0700; --up:#2bd47d; --dn:#ff5a4e; --link:#7ab8ff;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  color-scheme:dark;
}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px;line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:0 32px}
.bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;background:var(--amber);color:var(--amber-ink);font-family:var(--mono);font-size:12px;padding:6px 10px;margin-top:12px}
.bar .go{font-weight:700;background:var(--amber-ink);color:var(--amber);padding:1px 6px}
.bar .sp{flex:1}
.tape{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin-top:8px}
.tk{background:var(--panel);padding:6px 8px;font-family:var(--mono);font-size:12px;display:flex;flex-direction:column;gap:1px;font-variant-numeric:tabular-nums}
.tk b{color:var(--amber);font-weight:500;letter-spacing:.04em}
.tk span:first-of-type{font-size:15px;color:var(--fg)}
.lead{border:1px solid var(--amber);margin-top:12px;padding:14px 16px;background:var(--panel)}
.lead .k{font-family:var(--mono);color:var(--amber);font-size:11px;letter-spacing:.1em}
.lead h1{font-size:22px;line-height:1.25;margin:4px 0 8px;text-wrap:balance;font-weight:600}
.lead ul{margin:0;padding-left:18px;display:grid;gap:4px;max-width:90ch}
.grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:12px;margin-top:12px;align-items:start}
.col{display:grid;gap:12px;min-width:0}
.panel{background:var(--panel);border-top:2px solid var(--amber);padding:10px 12px;min-width:0}
.panel h2{font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--amber);margin:0 0 8px;display:flex;gap:8px;align-items:center}
.fn{background:var(--amber);color:var(--amber-ink);padding:0 5px;font-size:11px}
.news{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.news li{display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:baseline}
.tag{font-family:var(--mono);font-size:10.5px;color:var(--amber);border:1px solid var(--line);padding:0 4px;white-space:nowrap}
a{color:var(--link);text-decoration:none} a:hover,a:focus-visible{text-decoration:underline}
.src{font-family:var(--mono);font-size:11px;color:var(--dim)} a.src{color:var(--link)}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12.5px;font-variant-numeric:tabular-nums}
th{color:var(--dim);font-weight:500;text-align:left;border-bottom:1px solid var(--line);padding:4px 6px;white-space:nowrap}
td{padding:3px 6px;border-bottom:1px solid #171614;vertical-align:top}
td.n,th:not(:first-child){text-align:right;white-space:nowrap}
tr.grp td{color:var(--amber);font-size:11px;letter-spacing:.08em;padding-top:8px;border-bottom:none}
tr.home td{color:#fff}
tr.web td{color:var(--dim)}
.cal td{font-family:var(--sans);font-size:13px} .cal .when{font-family:var(--mono);color:var(--amber);white-space:nowrap;width:1%}
.up{color:var(--up)} .dn{color:var(--dn)} .flat,.d{color:var(--dim)}
.flag{font-size:10px;background:var(--dn);color:#000;padding:0 4px;margin-left:4px}
.z.hot{color:var(--amber);font-weight:700}
.wnote{font-family:var(--sans);font-size:12px;color:var(--dim);white-space:normal;padding-top:0}
tr.hasn td{border-bottom:none}
.foot{font-size:11.5px;color:var(--dim);margin:8px 0 0}
.prose{margin:0 0 8px}
.sources{margin-top:16px;font-size:12px;color:var(--dim)}
.sources ol{padding-left:18px;columns:2;column-gap:24px} .sources li{break-inside:avoid;margin-bottom:2px}
.status{font-family:var(--mono);font-size:11px;color:var(--dim);border-top:1px solid var(--line);margin-top:12px;padding-top:8px}
@media (max-width:820px){.grid{grid-template-columns:minmax(0,1fr)} .sources ol{columns:1}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def render(x: dict, notes: dict) -> str:
    """Build the page.

    notes (the JSON Claude writes each morning) has these keys, all optional:
      edition       "Tue 17 Nov 2026"         written         "6:52 AM ET"
      headline      one line                  lead            [3-6 short bullets]
      geopolitics / energy / markets   [{"tag","text","source","url"}]
      oil_monitor   {"summary", "rows": [[label, value, flag]], "url"}
      calendar      [{"when","event","detail"}]
      watch_notes   {"XOM": {"text","source","url"}}
      yield_fills   [{"country","tenor","yield_pct","chg_1d_bp","asof","source","url"}]
      sources       [{"title","url"}]
    """
    mk, yl, wx = tab(x, "brief_markets"), tab(x, "brief_yields"), tab(x, "brief_weather")
    wl, st = tab(x, "brief_watchlist"), tab(x, "brief_status")
    edition = E(notes.get("edition", datetime.now().strftime("%a %d %b %Y")))
    lead_items = "".join(f"<li>{E(b)}</li>" for b in notes.get("lead", []))
    left = (news_panel("Geopolitics", "GEO", notes.get("geopolitics", []))
            + news_panel("Energy", "NRG", notes.get("energy", []))
            + oil_panel(notes.get("oil_monitor", {}))
            + news_panel("Markets and economy", "MKT", notes.get("markets", []))
            + markets_table(mk, ["US", "Futures", "Europe", "Asia"], "World equity indices", "WEI"))
    right = (calendar_panel(notes.get("calendar", []))
             + watch_table(wl, notes.get("watch_notes", {}))
             + markets_table(mk, ["Energy", "Metals"], "Commodities", "CMDTY")
             + yields_table(yl, notes.get("yield_fills", []))
             + markets_table(mk, ["FX"], "Currencies", "FX")
             + weather_table(wx))
    srcs = "".join(f'<li><a href="{safe_url(s.get("url"))}" target="_blank" rel="noopener">{E(s.get("title", ""))}</a></li>'
                   for s in notes.get("sources", []) if safe_url(s.get("url")))
    return f"""<title>Morning Market Brief</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <div class="bar"><span class="go">BRIEF &lt;GO&gt;</span><span>MORNING MARKET BRIEF</span><span class="sp"></span><span>{edition}</span><span>{E(notes.get("written", ""))}</span></div>
  {tape(mk, yl)}
  <section class="lead"><div class="k">TOP &middot; WHAT TO EXPECT TODAY</div><h1>{E(notes.get("headline", ""))}</h1><ul>{lead_items}</ul></section>
  <div class="grid"><div class="col">{left}</div><div class="col">{right}</div></div>
  {f'<div class="sources"><b>Sources</b><ol>{srcs}</ol></div>' if srcs else ''}
  <div class="status">{status_line(st)} Prices: Yahoo Finance (indices and futures can lag up to 15 min). Context, not investment advice.</div>
</div>"""


def main(argv: list[str]) -> int:
    xlsx, notes_path, out = argv[1:4]
    x = pd.read_excel(xlsx, sheet_name=None, dtype=object)
    notes = json.loads(open(notes_path, encoding="utf-8").read())
    open(out, "w", encoding="utf-8").write(render(x, notes))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
