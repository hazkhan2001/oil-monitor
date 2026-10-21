"""
run_ais_window.py: record AIS around the next predicted satellite pass.

GitHub runs this every hour. Most runs find no pass coming up and exit in
seconds. When a pass is due within the lookahead, it waits until ~6 minutes
before, records every ship broadcasting in the relevant boxes until ~4
minutes after, and saves a compressed CSV to state/ais/ for the next
morning's run to match against the radar.

Why 6 minutes before? Moving ships report every 2-10 seconds, but anchored
ships only every 3 minutes, and shore receivers miss some messages. Ten
minutes in total catches nearly every ship that's broadcasting at all.
"""

from __future__ import annotations

import gzip
import os
import sys
import time
from datetime import datetime, timezone

import config
from monitor import ais, passes, state

LOOKAHEAD_MIN = 75   # hourly runs + 75 min lookahead = overlapping coverage


def main() -> int:
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        print("AISSTREAM_API_KEY not set; nothing to do")
        return 0
    preds = state.load("passes", [])
    now = datetime.now(timezone.utc)
    groups = passes.windows(preds, now, LOOKAHEAD_MIN)
    if not groups:
        print(f"{now:%H:%M} UTC: no pass in the next {LOOKAHEAD_MIN} min")
        return 0
    out_dir = state.STATE_DIR / "ais"
    out_dir.mkdir(parents=True, exist_ok=True)
    for g in groups:
        now = datetime.now(timezone.utc)
        if g["end"] <= now:
            continue
        wait = (g["start"] - now).total_seconds()
        if wait > 0:
            print(f"waiting {wait / 60:.0f} min for pass over {', '.join(g['aois'])}")
            time.sleep(wait)
        begin = datetime.now(timezone.utc)
        path = out_dir / f"{begin:%Y%m%dT%H%M}_{'-'.join(g['aois'])}.csv.gz"
        seconds = (g["end"] - begin).total_seconds()
        aois = [config.AOI_BY_KEY[k] for k in g["aois"]]
        print(f"recording AIS for {seconds / 60:.1f} min: {', '.join(g['aois'])}")
        df = ais.record(key, aois, seconds)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            df.to_csv(fh, index=False)
        print(f"saved {len(df)} position reports from {df['mmsi'].nunique() if len(df) else 0} ships -> {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
