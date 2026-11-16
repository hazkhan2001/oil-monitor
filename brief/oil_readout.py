"""Oil Monitor readout (same script as the oil-monitor-brief skill).

    python3 -I brief/oil_readout.py SHEET.xlsx

Reading rules: PortWatch counts ships with transponders on, not barrels;
prefer the same-season z; EXTREME needs |z| >= 3 and a 20% move.
"""
import sys, numpy as np, pandas as pd
x = pd.read_excel(sys.argv[1], sheet_name=None, dtype=object)
num = lambda s: pd.to_numeric(s, errors="coerce")
def tab(n):
    d = x.get(n)
    return d if d is not None and len(d) else pd.DataFrame()
pct = lambda a, b, floor=0: None if b is None or pd.isna(b) or b < floor or pd.isna(a) else (a / b - 1) * 100
f = lambda v, d=1: "n/a" if v is None or pd.isna(v) else f"{v:+.{d}f}"
c = tab("Chokepoints_Daily"); c["date"] = pd.to_datetime(c["date"])
FLOOR = {"tankers": 1, "total": 2, "capacity": 50000}
rows = []
for port, g in c.groupby("portname"):
    g = g.sort_values("date").set_index("date")
    for m, col in {"tankers": "n_tanker", "total": "n_total", "capacity": "capacity_tanker"}.items():
        a = num(g[col]).asfreq("D").rolling(7, min_periods=5).mean()
        cur = a.iloc[-1]
        if pd.isna(cur): continue
        d0 = a.index[-1]; ly = d0 - pd.Timedelta(days=364)
        hist = a.iloc[-366:-1].dropna()
        z = (cur - hist.mean()) / hist.std() if len(hist) >= 90 and hist.std() > 0 else None
        win = a[ly - pd.Timedelta(days=45): ly + pd.Timedelta(days=45)].dropna()
        zly = (cur - win.mean()) / win.std() if len(win) >= 60 and win.std() > 0 else None
        yoy = pct(cur, a.get(ly), FLOOR[m]); vs1y = pct(cur, hist.mean(), FLOOR[m])
        worst = max(abs(z or 0), abs(zly or 0)); size = max(abs(yoy or 0), abs(vs1y or 0))
        sig = "Not enough history" if z is None and zly is None else "EXTREME" if worst >= 3 and size >= 20 else "Unusual" if worst >= 2 and size >= 10 else "Normal"
        rows.append(dict(port=port, metric=m, asof=d0.date(), now=round(cur, 1), wow=f(pct(cur, a.iloc[-8], FLOOR[m])), w4=f(pct(cur, a.iloc[-29], FLOOR[m])),
                         yoy=f(yoy), vs1y=f(vs1y), z=f(z, 2), z_same_season=f(zly, 2), signal=sig))
print("## Chokepoints (PortWatch, 7-day average per day; changes in %)")
print(pd.DataFrame(rows).to_string(index=False))
e = tab("EIA_Cushing")
if len(e):
    e["date"] = pd.to_datetime(e["date"]); e["v"] = num(e["cushing_kbbl"]); e = e.sort_values("date").reset_index(drop=True)
    cur = e.iloc[-1]
    prior = [e[(e.date - (cur.date - pd.Timedelta(days=364 * k))).abs() <= pd.Timedelta(days=7)].v.mean() for k in range(1, 5)]
    prior = [p for p in prior if not pd.isna(p)]
    z = (cur.v - np.mean(prior)) / np.std(prior, ddof=1) if len(prior) >= 3 else None
    print(f"\n## Cushing (EIA week of {cur.date.date()}): {cur.v:,.0f} kbbl | wow {f(pct(cur.v, e.v.iloc[-2]))}% ({cur.v - e.v.iloc[-2]:+,.0f} kbbl) | 4w {f(pct(cur.v, e.v.iloc[-5]))}% | "
          f"yoy {f(pct(cur.v, e.iloc[(e.date - (cur.date - pd.Timedelta(days=364))).abs().argsort().iloc[0]].v))}% | vs earlier-year mean {f(pct(cur.v, np.mean(prior) if prior else None))}% | z {f(z, 2)}")
p = tab("Prices_Daily")
if len(p):
    p["date"] = pd.to_datetime(p["date"]); p = p.sort_values("date")
    for k in ("brent", "wti"): p[k] = num(p[k])
    l = p.dropna(subset=["brent", "wti"]).iloc[-1]; at = lambda days, k: p[p.date <= l.date - pd.Timedelta(days=days)][k].dropna().iloc[-1]
    print(f"\n## Prices (EIA spot, {l.date.date()}): Brent {l.brent:.2f} (wow {f(pct(l.brent, at(7, 'brent')))}%, yoy {f(pct(l.brent, at(364, 'brent')))}%) | WTI {l.wti:.2f} | Brent-WTI {l.brent - l.wti:.2f}")
s = tab("SAR_Detections")
if len(s):
    s = s.sort_values("scene_time").groupby("aoi").tail(1)
    pw = {r.port: r.now for r in pd.DataFrame(rows).itertuples() if r.metric == "total"}
    names = {"hormuz": "Strait of Hormuz", "bab": "Bab el-Mandeb Strait", "suez": "Suez Canal", "cape": "Cape of Good Hope", "malacca": "Malacca Strait"}
    print("\n## Radar snapshot (latest Sentinel-1 scene per area; ships in frame, not a daily count)")
    for r in s.itertuples():
        w = pw.get(names.get(r.aoi)); n = num(pd.Series([r.n_vessels])).iloc[0]
        print(f"- {r.aoi}: {n:.0f} ships ({r.scene_time}), over 150 m: {r.n_large_150m}; PortWatch all-vessel avg {w if w is not None else 'n/a'}/day" + (f"; ratio {n / w:.1f}x" if w else ""))
r = tab("Run_Log")
if len(r):
    last = r[r.run_utc == r.run_utc.max()]
    bad = last[last.status.isin(["error", "warning"])]
    print(f"\n## Pipeline: last run {r.run_utc.max()} UTC, {len(last)} steps, {len(bad)} errors/warnings")
    for q in bad.itertuples(): print(f"- {q.step} [{q.status}]: {str(q.detail)[:160]}")
    print("- PortWatch through", c.date.max().date(), "| EIA week", e.date.max().date() if len(e) else "n/a")
