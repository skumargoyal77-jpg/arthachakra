"""
dashboard/dhan_instruments.py
─────────────────────────────────
Dynamic security_id lookup using Dhan's published instrument master
CSV, instead of hand-maintaining a small, perpetually-incomplete
hardcoded map.

WHY THIS EXISTS:
  DHAN_SCRIP_MAP in dhan_greeks.py only covers a handful of names
  (indices + a few large banks). Any stock not in it had NO Greeks
  fetched at all, with the failure logged at a level too quiet to
  notice (or not logged at all). Hand-adding security IDs one stock at
  a time is also risky — a wrong ID silently fetches data for the
  WRONG instrument, which is worse than no data. This module instead
  downloads Dhan's full instrument list (public, no auth needed,
  refreshed daily by Dhan) and looks up ANY NSE equity or index by
  symbol name dynamically.

CSV SOURCE:
  https://images.dhan.co/api-data/api-scrip-master-detailed.csv

DEFENSIVE COLUMN HANDLING:
  Dhan's documented column names have changed across versions of their
  own docs, and this code can't be tested against the live file from
  this environment (network egress to dhan.co is blocked here). So
  rather than hardcode one column-name assumption and fail silently if
  wrong, this tries several known candidate names and — if NONE match —
  logs the actual columns found in the downloaded file. That single
  log line is what makes a mismatch diagnosable in one round-trip
  instead of more guessing.

PROJECT PATH:  dashboard/dhan_instruments.py
"""

from __future__ import annotations

import csv
import io
import time
from typing import Optional

from core.logging_config import setup_logging

logger = setup_logging(__name__)

SCRIP_MASTER_URL  = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
CACHE_TTL_SECONDS = 12 * 60 * 60  # refresh twice a day — Dhan updates this daily

_cache: dict = {"rows": None, "fetched_at": 0.0}

# Candidate column names, tried in order — see module docstring.
SYMBOL_COLUMNS      = [
    "SEM_TRADING_SYMBOL", "SEM_CUSTOM_SYMBOL", "SM_SYMBOL_NAME",
    "UNDERLYING_SYMBOL", "TRADING_SYMBOL", "SYMBOL_NAME",
]
SECURITY_ID_COLUMNS = ["SEM_SMST_SECURITY_ID", "SECURITY_ID", "SecurityId"]
EXCHANGE_COLUMNS    = ["SEM_EXM_EXCH_ID", "EXCH_ID", "EXCHANGE"]
INSTRUMENT_COLUMNS  = ["SEM_INSTRUMENT_NAME", "INSTRUMENT_TYPE", "SEM_EXCH_INSTRUMENT_TYPE"]


def _download_csv() -> Optional[list[dict]]:
    try:
        import requests
        resp = requests.get(SCRIP_MASTER_URL, timeout=30)
        if resp.status_code != 200:
            logger.warning(
                "dhan_instruments: failed to download scrip master, HTTP %d",
                resp.status_code,
            )
            return None
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        if not rows:
            logger.warning("dhan_instruments: scrip master CSV downloaded but empty")
            return None
        logger.info(
            "dhan_instruments: downloaded %d rows — columns found: %s",
            len(rows), list(rows[0].keys()),
        )
        return rows
    except Exception as e:
        logger.warning("dhan_instruments: download failed: %s", e)
        return None


def _get_rows(force_refresh: bool = False) -> Optional[list[dict]]:
    now = time.time()
    if not force_refresh and _cache["rows"] is not None and \
       (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["rows"]

    rows = _download_csv()
    if rows is not None:
        _cache["rows"] = rows
        _cache["fetched_at"] = now
    return rows


def _find_column(sample_row: dict, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in sample_row:
            return c
    return None


def lookup_security_id(underlying: str, is_index: bool = False) -> Optional[dict]:
    """
    Returns {"scrip": security_id, "segment": "IDX_I"|"NSE_EQ"} for the
    given underlying by searching Dhan's instrument master CSV, or None
    if not found, the CSV couldn't be downloaded, or the expected
    columns aren't present (logged clearly either way — see above).
    """
    rows = _get_rows()
    if not rows:
        return None

    sample = rows[0]
    symbol_col = _find_column(sample, SYMBOL_COLUMNS)
    secid_col  = _find_column(sample, SECURITY_ID_COLUMNS)
    exch_col   = _find_column(sample, EXCHANGE_COLUMNS)

    if not symbol_col or not secid_col:
        logger.warning(
            "dhan_instruments: expected columns not found in scrip master CSV — "
            "tried symbol in %s, security_id in %s — actual columns present: %s",
            SYMBOL_COLUMNS, SECURITY_ID_COLUMNS, list(sample.keys()),
        )
        return None

    target = underlying.strip().upper()
    for row in rows:
        sym = (row.get(symbol_col) or "").strip().upper()
        if sym == target:
            try:
                return {
                    "scrip": int(row[secid_col]),
                    "segment": "IDX_I" if is_index else "NSE_EQ",
                }
            except (ValueError, TypeError):
                continue

    logger.warning(
        "dhan_instruments: no entry found for '%s' in scrip master "
        "(searched %d rows by column '%s')", target, len(rows), symbol_col,
    )
    return None
