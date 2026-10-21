"""
visual.py: small PNG images for a human (or Claude) to sanity-check.

  quicklook  - the whole box, detections circled (blue = matched to AIS,
               red = dark candidate, grey = persistent object)
  crop       - a ~2 km square around one dark candidate

Numbers come from the code; these pictures are only for checking that the
code isn't fooling itself (wave clutter, a misplaced box, a land edge).

LESSON: images are just 2-D arrays. We stretch dB values to 0-255
(np.clip + scaling), turn the array into a Pillow Image, and draw on it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

LO_DB, HI_DB = -25.0, 5.0


def _to_image(db: np.ndarray, valid: np.ndarray) -> Image.Image:
    scaled = np.clip((db - LO_DB) / (HI_DB - LO_DB), 0, 1) * 255
    scaled[~valid] = 0
    return Image.fromarray(scaled.astype(np.uint8)).convert("RGB")


def quicklook(raster, dets, path: Path, max_side: int = 1400) -> None:
    img = _to_image(raster.db, raster.valid)
    scale = min(1.0, max_side / max(img.size))
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    draw = ImageDraw.Draw(img)
    for _, d in (dets.iterrows() if dets is not None else []):
        x, y = d["col"] * scale, d["row"] * scale
        if d.get("persistent"):
            color = (150, 150, 150)
        elif d.get("dark_candidate"):
            color = (255, 50, 50)
        else:
            color = (60, 140, 255)
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=color, width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def crop(raster, det, path: Path, half_px: int = 50) -> None:
    r, c = int(det["row"]), int(det["col"])
    h, w = raster.db.shape
    r0, r1, c0, c1 = max(0, r - half_px), min(h, r + half_px), max(0, c - half_px), min(w, c + half_px)
    img = _to_image(raster.db[r0:r1, c0:c1], raster.valid[r0:r1, c0:c1]).resize(((c1 - c0) * 3, (r1 - r0) * 3), Image.NEAREST)
    draw = ImageDraw.Draw(img)
    x, y = (c - c0) * 3, (r - r0) * 3
    draw.ellipse([x - 18, y - 18, x + 18, y + 18], outline=(255, 50, 50), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
