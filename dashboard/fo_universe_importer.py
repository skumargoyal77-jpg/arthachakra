"""
dashboard/fo_universe_importer.py
─────────────────────────────────────
Imports the FULL NSE F&O universe (~210 stocks + 5 indices) from
NSE's official lot-size file (fo_mktlots.csv) — replaces the old
13-symbol hardcoded starter watchlist with the real, complete list.

REAL FILE FORMAT (confirmed from an actual NSE download, not guessed):
  Row 0       : column headers (UNDERLYING, SYMBOL, JUN-26, JUL-26, ...)
  Rows 1-5    : the 5 tradeable indices (NIFTY 50, NIFTY BANK, etc.)
  Row 6       : a SECTION DIVIDER, not real data — its own SYMBOL
                column literally repeats the word "Symbol" again.
                Must be skipped explicitly, not treated as a stock.
  Rows 7+     : individual F&O-eligible stocks (full name + ticker +
                lot size per expiry month column).

LOT SIZE: takes the value from the FIRST non-empty expiry-month column
(the nearest/current month) — later columns are often blank for
less-liquid names that don't have far-dated contracts yet.

WHERE TO GET THIS FILE: NSE publishes it at
  https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
Re-download periodically — NSE adds/removes F&O-eligible stocks
roughly quarterly.

PROJECT PATH:  dashboard/fo_universe_importer.py
"""

from __future__ import annotations

import csv
from pathlib import Path

from core.database import Database
from core.logging_config import setup_logging
from dashboard.stock_universe import upsert_stock

logger = setup_logging(__name__)

# Same 5 indices already known elsewhere in the project
# (dashboard/strangle_grouper.py's INDEX_NAMES) — kept in sync manually
# since this file has no import relationship to that one.
KNOWN_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _first_nonblank_lot_size(row: dict, month_columns: list[str]) -> int | None:
    for col in month_columns:
        val = row.get(col, "").strip()
        if val:
            try:
                return int(val)
            except ValueError:
                continue
    return None


def parse_fo_mktlots(csv_path: str | Path) -> list[dict]:
    """
    Parses the real fo_mktlots.csv format. Returns a list of
    {"symbol", "full_name", "is_index", "lot_size"} dicts — pure
    parsing, no database writes, so this is directly testable without
    a live DB.
    """
    entries: list[dict] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return entries

    header = [h.strip() for h in rows[0]]
    month_columns_idx = list(range(2, len(header)))   # everything after UNDERLYING, SYMBOL

    for row in rows[1:]:
        if len(row) < 2:
            continue
        underlying = row[0].strip()
        symbol = row[1].strip()

        if not symbol:
            continue
        # The section-divider row repeats "Symbol" as its own value —
        # this is the one explicit thing to skip, not a real stock.
        if symbol.lower() == "symbol":
            continue

        row_dict = {header[i]: (row[i].strip() if i < len(row) else "") for i in month_columns_idx}
        lot_size = _first_nonblank_lot_size(row_dict, [header[i] for i in month_columns_idx])
        if lot_size is None:
            continue   # no lot size in any column - not a currently tradeable contract

        entries.append({
            "symbol": symbol,
            "full_name": underlying,
            "is_index": symbol in KNOWN_INDEX_SYMBOLS,
            "lot_size": lot_size,
        })

    return entries


def import_fo_universe(db: Database, csv_path: str | Path) -> dict:
    """Parses and writes the full F&O universe into db.nse_stocks. Returns a summary dict."""
    entries = parse_fo_mktlots(csv_path)
    stocks, indices = 0, 0
    for e in entries:
        upsert_stock(db, e["symbol"], e["full_name"], is_index=e["is_index"], lot_size=e["lot_size"])
        if e["is_index"]:
            indices += 1
        else:
            stocks += 1

    logger.info("F&O universe import: %d stocks, %d indices", stocks, indices)
    return {"total": len(entries), "stocks": stocks, "indices": indices}
