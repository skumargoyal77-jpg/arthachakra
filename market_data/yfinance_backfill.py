"""
market_data/yfinance_backfill.py
───────────────────────────────────
Fast historical OHLC backfill via yfinance — ported from POC-13's
data_fetcher.py, adapted to write directly into this project's shared
db.monthly_ohlc (the same collection market_data/ohlc_updater.py's
daily Bhavcopy job writes to) instead of POC-13's own separate
db_manager/database.

WHY THIS EXISTS ALONGSIDE THE DAILY BHAVCOPY JOB:
  ohlc_updater.py builds real history one trading day at a time —
  accurate, but slow to accumulate (you only get one real month's
  worth of data after running it daily for a month). This backfills
  YEARS of history in one call per symbol, immediately making the
  Stock Selector's win-rate tiers meaningful instead of empty.

WHY DAILY DATA AGGREGATED TO MONTHLY, NOT interval="1mo" DIRECTLY:
  yfinance's native monthly interval uses calendar-month Open, which
  doesn't necessarily match the first trading day's actual open price.
  Aggregating daily data ourselves guarantees:
    Open = first trading day's open price
    High = max of all daily highs that month
    Low  = min of all daily lows that month

SOURCE TAGGING: records written here are tagged source="yfinance",
distinct from ohlc_updater.py's source="bhavcopy" (implicit, no
explicit source field there currently) — worth knowing which pipeline
populated a given month if the two ever disagree.

CANNOT BE TESTED LIVE IN THIS SANDBOX — query1.finance.yahoo.com isn't
reachable from this environment (same constraint as NSE/Hugging Face
elsewhere in this project). The symbol-mapping and monthly-aggregation
logic ARE tested here with synthetic data; the actual live yfinance
call needs to be run on a machine with real internet access.

PROJECT PATH:  market_data/yfinance_backfill.py
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional

from core.database import Database
from core.logging_config import setup_logging

logger = setup_logging(__name__)

# NSE symbol -> yfinance ticker overrides. Most symbols work as
# "<SYMBOL>.NS" automatically; only exceptions need listing here.
YFINANCE_SYMBOL_MAP: dict[str, str] = {
    "BANKNIFTY": "^NSEBANK",
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    # Confirmed against real Yahoo Finance pages (not guessed) after
    # backfill_ohlc.py returned 0 months for these 3 on a real run:
    "FINNIFTY":   "NIFTY_FIN_SERVICE.NS",   # Yahoo's literal symbol for "NIFTY FIN SERVICE"
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",    # Yahoo's literal symbol for "NIFTY MID SELECT"
    # Yahoo's ticker for Nifty Next 50 is genuinely "^NSMIDCP" despite
    # the misleading "MIDCP" in the symbol name — the actual quote page
    # at this ticker is labeled "NIFTY NEXT 50", confirmed directly.
    "NIFTYNXT50": "^NSMIDCP",
}

# KNOWN TECHNICAL DEBT — MIDCPNIFTY:
# NIFTY_MID_SELECT.NS is a real, confirmed-correct ticker, but Yahoo's
# own historical data for it only goes back to ~March 2025 (the index
# itself launched on NSE in late 2023, but Yahoo's coverage is
# shallower). --from-date 2023-11-01 was tried and still failed
# ("possibly delisted; no price data found"), so even the index's own
# launch date isn't enough for Yahoo's data range. Left unresolved —
# MIDCPNIFTY will have 0 months of yfinance-backfilled history until
# this is revisited (e.g. trying an even later from-date, or relying
# solely on the daily Bhavcopy job to slowly accumulate real history
# instead of backfilling).


def get_yf_ticker(nse_symbol: str) -> str:
    """NSE symbol -> yfinance ticker. Override map first, else '<SYMBOL>.NS'."""
    sym = nse_symbol.strip().upper()
    return YFINANCE_SYMBOL_MAP.get(sym, f"{sym}.NS")


def aggregate_daily_to_monthly(daily_rows: list[dict]) -> list[dict]:
    """
    Pure aggregation logic, separated from the actual yfinance download
    so it's directly testable without a live network call.

    Args:
        daily_rows: [{"date": "YYYY-MM-DD", "open": float, "high": float,
                      "low": float, "close": float}, ...]

    Returns:
        [{"month_key": "YYYY-MM", "open": float, "high": float, "low": float,
          "close": float}, ...] sorted oldest first.
        Open = FIRST trading day's open that month (not the calendar-
        month open). Close = LAST trading day's close that month (the
        real value, not a placeholder — needed since compute_beta()
        and other code expect a genuine close field).
        High/Low = max/min across all days that month.
    """
    by_month: dict[str, list[dict]] = {}
    for row in sorted(daily_rows, key=lambda r: r["date"]):
        month_key = row["date"][:7]   # "YYYY-MM-DD"[:7] = "YYYY-MM"
        by_month.setdefault(month_key, []).append(row)

    results = []
    for month_key in sorted(by_month):
        days = by_month[month_key]
        o = float(days[0]["open"])       # first trading day's open
        c = float(days[-1]["close"])     # last trading day's close (real value)
        h = max(float(d["high"]) for d in days)
        l = min(float(d["low"]) for d in days)
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        results.append({"month_key": month_key, "open": round(o, 4), "high": round(h, 4),
                        "low": round(l, 4), "close": round(c, 4)})
    return results


def fetch_monthly_ohlc(nse_symbol: str, from_date: str, to_date: Optional[str] = None) -> list[dict]:
    """
    Fetches daily data from yfinance and aggregates to monthly. Returns
    [] on any failure (missing yfinance package, network error, symbol
    not found) — never raises, same graceful-degradation pattern as
    every other external fetcher in this project.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return []

    ticker = get_yf_ticker(nse_symbol)
    end_str = to_date or date.today().strftime("%Y-%m-%d")
    logger.info("Fetching %s (%s) from %s to %s", nse_symbol, ticker, from_date, end_str)

    try:
        df = yf.download(ticker, start=from_date, end=end_str, interval="1d",
                         auto_adjust=True, progress=False, threads=False)
    except Exception as e:
        logger.warning("yfinance download failed for %s: %s", ticker, e)
        return []

    if df is None or df.empty:
        logger.warning("No data returned for %s (%s)", nse_symbol, ticker)
        return []

    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)

    daily_rows = [
        {"date": idx.strftime("%Y-%m-%d"), "open": row["Open"], "high": row["High"],
         "low": row["Low"], "close": row["Close"]}
        for idx, row in df.iterrows()
    ]
    return aggregate_daily_to_monthly(daily_rows)


def backfill_symbol(db: Database, nse_symbol: str, from_date: str, to_date: Optional[str] = None) -> int:
    """Fetches and writes one symbol's history into db.monthly_ohlc. Returns months written."""
    months = fetch_monthly_ohlc(nse_symbol, from_date, to_date)
    for m in months:
        db.monthly_ohlc.update_one(
            {"symbol": nse_symbol, "month_key": m["month_key"]},
            {"$set": {
                "symbol": nse_symbol, "month_key": m["month_key"],
                "open": m["open"], "high": m["high"], "low": m["low"], "close": m["close"],
                "source": "yfinance",
            }},
            upsert=True,
        )
    logger.info("Backfilled %s: %d months written", nse_symbol, len(months))
    return len(months)


def backfill_watchlist(
    db: Database, symbols: list[str], from_date: str, to_date: Optional[str] = None, delay_secs: float = 1.2,
) -> dict[str, int]:
    """Backfills multiple symbols sequentially, with a polite delay between requests."""
    results: dict[str, int] = {}
    for i, sym in enumerate(symbols):
        results[sym] = backfill_symbol(db, sym, from_date, to_date)
        if i < len(symbols) - 1:
            time.sleep(delay_secs)
    return results
