"""
market_data/nse_index_constituents.py
─────────────────────────────────────────
Fetches NSE's official index constituent lists (Nifty 50, Nifty 100,
Nifty 500, Bank Nifty, etc.) and tags each symbol in nse_stocks with
which indices it belongs to.

REUSES THE SAME PROVEN PATTERN as corporate_events/nse_client.py and
market_data/bhavcopy.py — session priming + browser-like headers, since
these are also public NSE endpoints with the same bot-detection.

NSE PUBLISHES THESE AS PUBLIC CSVs (no auth needed):
  https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv
  https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv
  https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv
  ...etc, one CSV per index, each with a "Symbol" column.

CANNOT BE TESTED LIVE IN THIS SANDBOX — nsearchives.nseindia.com isn't
reachable from this environment (same constraint as every other NSE
integration in this project). The CSV-parsing logic IS tested here
with a synthetic file shaped like NSE's real format; the live
download needs to be run on a machine with real internet access.

PROJECT PATH:  market_data/nse_index_constituents.py
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from core.database import Database
from core.logging_config import setup_logging
from dashboard.stock_universe import set_index_memberships

logger = setup_logging(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com",
    "Accept": "text/csv,*/*",
}

# Index name -> NSE's public CSV URL. Add more here as needed (e.g.
# Nifty Midcap 100, Nifty Smallcap 100) - same URL pattern.
INDEX_CSV_URLS: dict[str, str] = {
    "NIFTY50":     "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY100":    "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY500":    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "BANKNIFTY":   "https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "NIFTYNEXT50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
}


def parse_constituent_csv(csv_text: str) -> list[str]:
    """
    Parses one NSE index-constituent CSV. Pure parsing, no network —
    directly testable. NSE's real column is literally named "Symbol".
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    symbols = []
    for row in reader:
        symbol = (row.get("Symbol") or row.get("SYMBOL") or "").strip()
        if symbol:
            symbols.append(symbol)
    return symbols


class NSEIndexClient:
    """Thin HTTP client for NSE's index-constituent CSVs — same session-priming pattern as nse_client.py."""

    def __init__(self) -> None:
        self._session = None
        self._primed = False

    def _prime_session(self) -> None:
        if self._primed:
            return
        import requests
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        try:
            self._session.get("https://www.nseindia.com", timeout=10)
            self._primed = True
        except Exception as e:
            logger.warning("Could not prime NSE session (may get 403): %s", e)

    def fetch_constituents(self, index_name: str) -> list[str]:
        url = INDEX_CSV_URLS.get(index_name)
        if not url:
            logger.warning("Unknown index name: %s", index_name)
            return []

        self._prime_session()
        try:
            resp = self._session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("NSE index list HTTP %d for %s", resp.status_code, index_name)
                return []
            return parse_constituent_csv(resp.text)
        except Exception as e:
            logger.warning("NSE index list request failed for %s: %s", index_name, e)
            return []


def update_index_memberships(db: Database, index_names: Optional[list[str]] = None) -> dict:
    """
    Fetches each requested index's constituent list and tags every
    member symbol in db.nse_stocks. A symbol in multiple indices
    (e.g. HDFCBANK is in both NIFTY50 and BANKNIFTY) gets all
    applicable tags, not just the first match.
    """
    index_names = index_names or list(INDEX_CSV_URLS.keys())
    client = NSEIndexClient()

    membership_map: dict[str, list[str]] = {}   # symbol -> list of indices it belongs to
    summary = {}

    for index_name in index_names:
        symbols = client.fetch_constituents(index_name)
        summary[index_name] = len(symbols)
        for sym in symbols:
            membership_map.setdefault(sym, []).append(index_name)

    for sym, memberships in membership_map.items():
        set_index_memberships(db, sym, memberships)

    logger.info("Updated index memberships for %d symbols across %d indices",
               len(membership_map), len(index_names))
    return summary
