"""
state.py: the program's memory between runs.

Each GitHub Actions run starts on a brand-new machine with nothing on it.
Anything we must remember (which scenes we already processed, how many
processing units we spent this month, which bright spots never move) is saved
as small JSON files in state/, and the workflow commits them back to the repo.

LESSON: JSON is the text format for dicts and lists. json.dumps turns a
Python dict into text; json.loads turns it back. Path objects (pathlib) are
the modern way to handle file paths: Path("state") / "x.json" joins them
correctly on Windows and Linux alike.
"""

from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def load(name: str, default):
    """Read state/<name>.json, or return `default` if it doesn't exist yet."""
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save(name: str, data) -> None:
    """Write data to state/<name>.json (creating the folder if needed)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    # indent=1 keeps the file readable in git diffs; default=str turns
    # datetimes and other odd types into text instead of crashing.
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
