"""
detect.py: Phase 2. Find ships in a radar image with CFAR.

THE IDEA
    In radar, calm-ish sea is dark (it scatters the signal away) and a steel
    hull is very bright (it bounces the signal straight back). But "bright"
    is relative: wind-roughened sea is brighter than calm sea. So instead of
    one global threshold, CFAR (Constant False Alarm Rate) asks for every
    pixel: "is this much brighter than the sea *right around it*?"

        +---------------------------+
        |   background ring         |   measure mean and spread of the sea here
        |    +---------------+      |
        |    |  guard box    |      |   skip this (a big ship would leak into it)
        |    |      [px]     |      |   the pixel under test
        |    +---------------+      |
        +---------------------------+

    pixel is a target if   value > mean + K * std   of the ring.

LESSON: doing that in a Python loop over 5 million pixels would take
minutes. Instead we use box filters (scipy.ndimage.uniform_filter), which
compute the average of every window in one vectorised pass. The ring
average = (sum over big box - sum over guard box) / (pixels in ring).
Thinking in whole-array operations instead of loops is the biggest speed
habit in numerical Python.

LESSON: connected-component labelling (scipy.ndimage.label) groups touching
target pixels into numbered blobs: one blob = one candidate ship.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage

import config


def cfar_mask(db: np.ndarray, valid: np.ndarray,
              guard: int = config.CFAR_GUARD_PX,
              background: int = config.CFAR_BACKGROUND_PX,
              k: float = config.CFAR_K,
              min_db: float = config.MIN_TARGET_DB) -> np.ndarray:
    """Boolean array: True where a pixel stands out from its local sea."""
    lin = np.where(valid, 10 ** (db / 10), 0.0).astype(np.float64)   # dB -> linear power
    w = valid.astype(np.float64)

    def box_sum(a, half):
        size = 2 * half + 1
        return ndimage.uniform_filter(a, size=size, mode="constant") * size * size

    # Sums over the big box and the guard box, counting only valid sea pixels.
    n_big, n_guard = box_sum(w, background), box_sum(w, guard)
    s_big, s_guard = box_sum(lin, background), box_sum(lin, guard)
    q_big, q_guard = box_sum(lin ** 2, background), box_sum(lin ** 2, guard)

    n_ring = n_big - n_guard
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = (s_big - s_guard) / n_ring
        var = (q_big - q_guard) / n_ring - mean ** 2
    std = np.sqrt(np.clip(var, 0, None))

    enough_sea = n_ring > 0.5 * ((2 * background + 1) ** 2 - (2 * guard + 1) ** 2)
    return valid & enough_sea & (lin > mean + k * std) & (db > min_db)


def blobs_to_table(mask: np.ndarray, db: np.ndarray, raster, pixel_m: float) -> pd.DataFrame:
    """Turn the target mask into one row per candidate ship."""
    # Close 1-pixel gaps so one ship split by speckle counts once.
    # Pad by one pixel first: closing treats outside the image as empty and
    # would otherwise erase ships touching the edge.
    joined = ndimage.binary_closing(np.pad(mask, 1), structure=np.ones((3, 3)))[1:-1, 1:-1]
    labels, n = ndimage.label(joined, structure=np.ones((3, 3)))
    rows = []
    for idx, slc in enumerate(ndimage.find_objects(labels), start=1):
        if slc is None:
            continue
        local = labels[slc] == idx
        rr, cc = np.nonzero(local)
        rr, cc = rr + slc[0].start, cc + slc[1].start
        if len(rr) < config.MIN_TARGET_PIXELS:
            continue
        weights = 10 ** (db[rr, cc] / 10)
        r0, c0 = np.average(rr, weights=weights), np.average(cc, weights=weights)
        # Length along the blob's main axis: a line of L pixels has position
        # variance (L^2 - 1) / 12, so L = sqrt(12 * variance + 1).
        if len(rr) > 2:
            cov = np.cov(np.vstack([rr, cc]), bias=True)
            length_px = np.sqrt(12 * max(np.linalg.eigvalsh(cov)[-1], 0) + 1)
        else:
            length_px = len(rr)
        length_m = float(length_px * pixel_m)
        if length_m > config.MAX_TARGET_LENGTH_M:
            continue
        lon, lat = raster.pixel_to_lonlat(r0, c0)
        rows.append({
            "lat": round(float(lat), 5), "lon": round(float(lon), 5),
            "row": int(round(r0)), "col": int(round(c0)),
            "n_pixels": int(len(rr)), "length_m": round(length_m),
            "peak_db": round(float(db[rr, cc].max()), 1),
        })
    return pd.DataFrame(rows, columns=["lat", "lon", "row", "col", "n_pixels", "length_m", "peak_db"])


def detect_ships(raster, sea: np.ndarray, pixel_m: float) -> pd.DataFrame:
    """Full pipeline: CFAR on sea pixels, then blob table."""
    mask = cfar_mask(raster.db, raster.valid & sea)
    return blobs_to_table(mask, raster.db, raster, pixel_m)


# --------------------------------------------------------------------------
# Persistent objects: oil platforms, buoys, wrecks... and floating storage.
# --------------------------------------------------------------------------
CELL_DEG = 0.003   # ~300 m grid for remembering where bright things were


def _cells(df: pd.DataFrame) -> list[str]:
    return [f"{round(la / CELL_DEG)}:{round(lo / CELL_DEG)}" for la, lo in zip(df["lat"], df["lon"])]


def flag_persistent(aoi_key: str, dets: pd.DataFrame, memory: dict, record: bool = True,
                    min_scenes: float = 5, min_share: float = 0.6, decay: float = 0.97) -> pd.DataFrame:
    """Mark detections at spots that were bright in most recent scenes.

    These are usually platforms or navigation marks, but a tanker sitting at
    anchor for weeks is persistent too, which at Qingdao or Fujairah can mean
    floating storage. So we flag rather than delete, and count both.

    `memory` is updated in place: {aoi: {"scenes": x, "hits": {cell: x}}}.
    Counts fade by `decay` each scene (an exponential moving window of
    roughly the last 30 scenes), so a ship that anchored for a month and left
    stops being "persistent", and stale cells get pruned. Pass record=False
    for scenes that only partly cover the box, so they can't dilute the share.
    """
    mem = memory.setdefault(aoi_key, {"scenes": 0.0, "hits": {}})
    dets = dets.copy()
    cells = _cells(dets) if len(dets) else []
    flags = []
    for cell in cells:
        la, lo = (int(x) for x in cell.split(":"))
        # Sum this cell and its 8 neighbours: a platform's position jitters
        # across cell borders from scene to scene. Cap at the scene count.
        hits = sum(mem["hits"].get(f"{la + a}:{lo + b}", 0) for a in (-1, 0, 1) for b in (-1, 0, 1))
        share = min(hits, mem["scenes"]) / mem["scenes"] if mem["scenes"] else 0
        flags.append(bool(mem["scenes"] >= min_scenes and share >= min_share))
    dets["persistent"] = flags
    if record:
        mem["scenes"] = mem["scenes"] * decay + 1
        mem["hits"] = {c: n * decay for c, n in mem["hits"].items() if n * decay >= 0.3}
        for cell in set(cells):
            mem["hits"][cell] = mem["hits"].get(cell, 0) + 1
        mem["scenes"] = round(mem["scenes"], 4)
        mem["hits"] = {c: round(n, 4) for c, n in mem["hits"].items()}
    return dets
