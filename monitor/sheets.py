"""
sheets.py: writes tables to a Google Sheet, or to local CSVs if no Sheet is set up.

HOW THE GOOGLE PART WORKS
    A "service account" is a robot Google user with its own email address
    (something@project.iam.gserviceaccount.com). You share your Sheet with
    that email as Editor, and the program logs in as the robot using a JSON
    key file. On GitHub the key lives in an encrypted secret.

LESSON: "upsert" = update + insert. We read what's already in a tab, stack
the new rows under it, and drop duplicates on the key columns, keeping the
newest. Running the program twice therefore never creates double rows.
That property (same input, same result, however many times you run it) is
called idempotence, and it's what makes scheduled jobs safe to re-run.

LESSON: a class bundles data (the connection) with functions that use it
(methods). `self` is the particular object a method is working on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

LOCAL_DIR = Path(__file__).resolve().parent.parent / "output" / "sheet"


def _clean_for_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Sheets can't hold NaN or datetime objects, so convert them to text."""
    out = df.copy()
    for col in out.columns:
        # A missing value turns a whole integer column into floats, so 12
        # would be written as "12.0" in one run and "12" in the next, and the
        # two would no longer match as keys. Whole-number floats go back to ints.
        if out[col].dtype.kind == "f":
            vals = out[col].dropna()
            if len(vals) and (vals == vals.round()).all():
                out[col] = out[col].astype("Int64")
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S").str.replace(" 00:00:00", "", regex=False)
    out = out.astype(object).where(pd.notna(out), "")
    return out


class SheetWriter:
    def __init__(self, sheet_id: str | None = None, creds_json: str | None = None):
        sheet_id = sheet_id or os.environ.get("SHEET_ID")
        creds_json = creds_json or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        self.book = None
        if sheet_id and creds_json:
            import gspread  # imported here so local mode doesn't need it installed

            client = gspread.service_account_from_dict(json.loads(creds_json))
            self.book = client.open_by_key(sheet_id)
        self.mode = "google" if self.book else "local"

    # ---- reading ---------------------------------------------------------
    def read(self, tab: str) -> pd.DataFrame:
        if self.book is None:
            path = LOCAL_DIR / f"{tab}.csv"
            return pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()
        import gspread

        try:
            ws = self.book.worksheet(tab)
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        values = ws.get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=values[0] if values else [])
        return pd.DataFrame(values[1:], columns=values[0], dtype=str)

    # ---- writing ---------------------------------------------------------
    def replace(self, tab: str, df: pd.DataFrame) -> None:
        """Overwrite a whole tab with df."""
        df = _clean_for_sheet(df)
        if self.book is None:
            LOCAL_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(LOCAL_DIR / f"{tab}.csv", index=False)
            return
        import gspread

        try:
            ws = self.book.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(tab, rows=max(len(df) + 10, 100), cols=max(len(df.columns), 5))
        rows = [list(df.columns)] + df.values.tolist()
        # RAW keeps text exactly as written. USER_ENTERED would let Google
        # reinterpret "2026-10-20 04:48" as a date and hand it back in a
        # different format, which breaks de-duplication on the next run.
        ws.update(rows, value_input_option="RAW")
        # Write first, then clear leftovers below/right, so a failed write
        # never leaves an empty tab.
        from gspread.utils import rowcol_to_a1
        if ws.row_count > len(rows):
            ws.batch_clear([f"A{len(rows) + 1}:{rowcol_to_a1(ws.row_count, max(ws.col_count, 1))}"])
        if ws.col_count > len(df.columns):
            ws.batch_clear([f"{rowcol_to_a1(1, len(df.columns) + 1)}:{rowcol_to_a1(len(rows), ws.col_count)}"])
        ws.freeze(rows=1)

    def upsert(self, tab: str, new: pd.DataFrame, keys: list[str], sort_by: list[str] | None = None) -> int:
        """Merge new rows into a tab, de-duplicating on `keys`. Returns row count."""
        if new is None or new.empty:
            return len(self.read(tab))
        new = _clean_for_sheet(new).astype(str)
        old = self.read(tab)
        combined = pd.concat([old, new], ignore_index=True) if not old.empty else new
        combined = combined.drop_duplicates(subset=keys, keep="last")
        if sort_by:
            combined = combined.sort_values(sort_by, kind="stable")
        combined = combined.reset_index(drop=True)
        self.replace(tab, combined)
        return len(combined)

    def append(self, tab: str, new: pd.DataFrame) -> None:
        """Add rows to the bottom of a tab (used for the run log)."""
        old = self.read(tab)
        combined = pd.concat([old, _clean_for_sheet(new).astype(str)], ignore_index=True)
        self.replace(tab, combined.tail(2000))  # keep the log from growing forever
