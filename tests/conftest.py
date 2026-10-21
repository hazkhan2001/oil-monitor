"""Shared test helpers. pytest loads this file automatically.

LESSON: a "fixture" is a reusable setup step. Any test that names a fixture
as a parameter gets it handed in. tmp_path is built into pytest: a fresh
empty folder for every test, so tests never touch your real files.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from monitor.copernicus import Raster  # noqa: E402

# Open Atlantic, far from any land, so the land mask doesn't interfere.
OCEAN_BBOX = (-30.0, 30.0, -29.8, 30.18)


def make_scene(ships, shape=(1000, 1000), sea_db=-20.0, ship_db=5.0, seed=0, bbox=OCEAN_BBOX):
    """Speckled sea with bright rectangular 'ships' at the given (row, col, length_px)."""
    rng = np.random.default_rng(seed)
    # Radar speckle: sea power follows an exponential (gamma, shape 1) distribution.
    power = rng.gamma(shape=4.0, scale=10 ** (sea_db / 10) / 4.0, size=shape)
    for r, c, length in ships:
        power[r:r + 2, c:c + length] = 10 ** (ship_db / 10)
    db = (10 * np.log10(power)).astype(np.float32)
    return Raster(db=db, valid=np.ones(shape, bool), bbox=bbox, pu_spent=1.0)


@pytest.fixture
def local_sheet(tmp_path, monkeypatch):
    from monitor import sheets

    monkeypatch.setattr(sheets, "LOCAL_DIR", tmp_path / "sheet")
    monkeypatch.delenv("SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    return sheets.SheetWriter()
