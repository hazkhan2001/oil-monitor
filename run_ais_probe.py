"""
run_ais_probe.py: a 90-second diagnostic for the AIS feed (run by hand from Actions).

Opens the same AISstream connection the recorder uses, but prints what it sees:
whether the connection opens, whether the server sends an error, how many
messages arrive, of which type, per test box. A busy control box (the Dover
Strait, one of the busiest lanes on earth) tells us whether silence elsewhere
means "no receivers there" or "our subscription is not working at all".

LESSON: when something produces nothing, don't guess. Add a probe that
measures each step (connect, subscribe, receive) so the failing step is
obvious. Never print the API key.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter

import websockets

import config
from monitor.ais import WS_URL

SECONDS = 90
CONTROL = ("dover_control", (-0.5, 50.6, 2.0, 51.3))        # lon0, lat0, lon1, lat1
BOXES = [(a.key, a.bbox) for a in config.AOIS if a.key in ("suez", "bab", "hormuz", "gulfoman", "malacca")] + [CONTROL]


def lat_lon_box(b):                                           # AISstream wants [lat, lon] corners
    return [[b[1], b[0]], [b[3], b[2]]]


async def main() -> int:
    key = os.environ.get("AISSTREAM_API_KEY", "")
    print(f"key present: {bool(key)}, length {len(key)}")
    if not key:
        return 1
    sub = {"APIKey": key, "BoundingBoxes": [lat_lon_box(b) for _, b in BOXES],
           "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"]}
    kinds, per_box, errors, first = Counter(), Counter(), [], []
    t0 = time.monotonic()
    try:
        async with websockets.connect(WS_URL, max_size=None, open_timeout=20) as ws:
            print(f"connected after {time.monotonic() - t0:.1f}s")
            await ws.send(json.dumps(sub))
            deadline = t0 + SECONDS
            while time.monotonic() < deadline:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(frame.decode() if isinstance(frame, bytes) else frame)
                if "error" in msg:
                    errors.append(str(msg["error"])[:200])
                    continue
                kinds[msg.get("MessageType")] += 1
                meta = msg.get("MetaData", {})
                lat, lon = meta.get("latitude", meta.get("Latitude")), meta.get("longitude", meta.get("Longitude"))
                if lat is not None and lon is not None:
                    for name, (x0, y0, x1, y1) in BOXES:
                        if y0 - 0.2 <= lat <= y1 + 0.2 and x0 - 0.2 <= lon <= x1 + 0.2:
                            per_box[name] += 1
                if len(first) < 2:
                    first.append({k: msg.get("MetaData", {}).get(k) for k in ("MMSI", "latitude", "longitude", "time_utc")} | {"type": msg.get("MessageType")})
    except Exception as exc:   # report whatever happened rather than crash silently
        print(f"CONNECTION PROBLEM: {exc!r}")
    print(f"messages by type: {dict(kinds)}")
    print(f"messages per box: {dict(per_box)}")
    print(f"server errors: {errors[:3]}")
    print(f"first messages: {first}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
