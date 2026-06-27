"""
market_data/ohlc_updater.py
───────────────────────────────
Builds monthly OHLC history per symbol — feeds S-06 (range-bound
confirmation) and S-07 (beta vs Nifty).

NO SEPARATE EQUITY DOWNLOAD NEEDED:
  The F&O Bhavcopy's SPOT column (UndrlygPric) already has the daily
  underlying price — see market_data/bhavcopy.py's docstring. One
  daily download serves option IV (iv_updater.py) AND price history
  (this file) — no need to also scrape NSE's equity/CM Bhavcopy.

WHAT THIS BUILDS:
  Each call appends one day's (symbol, spot) observation to a running
  per-month aggregate, then upserts the month's open/high/low/close
  into monthly_ohlc once the month is complete (or on every call for
  the in-progress month — close just reflects "as of today" until
  the month ends).

PROJECT PATH:  market_data/ohlc_updater.py
"""

from __future__ import annotations

from datetime import date

from core.database import Database
from core.logging_config import setup_logging
from market_data.bhavcopy import BhavcopyScraper

logger = setup_logging(__name__)


def _month_key(dt: date) -> str:
    return dt.strftime("%Y-%m")


def update_ohlc_for_symbol(db: Database, scraper: BhavcopyScraper, symbol: str, dt: date) -> dict | None:
    """
    Records one day's spot price into the symbol's current month's
    OHLC aggregate. Returns the updated month document, or None if no
    spot price was available for this date.
    """
    spot = scraper.get_spot_price(symbol, dt)
    if spot is None:
        logger.debug("No spot price for %s on %s — skipping OHLC update.", symbol, dt)
        return None

    month_key = _month_key(dt)
    existing = db.monthly_ohlc.find_one({"symbol": symbol, "month_key": month_key})

    if existing is None:
        doc = {
            "symbol": symbol, "month_key": month_key,
            "open": spot, "high": spot, "low": spot, "close": spot,
            "first_date": dt.isoformat(), "last_date": dt.isoformat(),
        }
    else:
        doc = {
            "symbol": symbol, "month_key": month_key,
            "open": existing["open"],  # open never changes once set
            "high": max(existing["high"], spot),
            "low": min(existing["low"], spot),
            "close": spot,  # close is always "most recent observation"
            "first_date": existing["first_date"],
            "last_date": dt.isoformat(),
        }

    db.monthly_ohlc.update_one(
        {"symbol": symbol, "month_key": month_key}, {"$set": doc}, upsert=True,
    )
    return doc


def update_ohlc_for_watchlist(db: Database, symbols: list[str], dt: date) -> list[dict]:
    """Runs update_ohlc_for_symbol for every symbol. One bad symbol never stops the rest."""
    scraper = BhavcopyScraper()
    if not scraper.download_date(dt):
        logger.warning("Bhavcopy not available for %s — OHLC update skipped.", dt)
        return []

    results = []
    for symbol in symbols:
        try:
            doc = update_ohlc_for_symbol(db, scraper, symbol, dt)
            if doc:
                results.append(doc)
        except Exception as e:
            logger.warning("OHLC update failed for %s on %s: %s", symbol, dt, e)
    return results


def get_monthly_range_pct(db: Database, symbol: str, months: int = 3) -> float | None:
    """
    Range as a % of average close, over the last `months` months —
    what Rule S-06's "range-bound on a 3-month chart" actually checks
    numerically (still reported as ADVISORY by the rule engine, not a
    strict pass/fail — see rules/engine.py for why).
    """
    docs = db.monthly_ohlc.find({"symbol": symbol})
    if not docs:
        return None
    recent = sorted(docs, key=lambda d: d["month_key"])[-months:]
    if not recent:
        return None
    highs = [d["high"] for d in recent]
    lows = [d["low"] for d in recent]
    closes = [d["close"] for d in recent]
    avg_close = sum(closes) / len(closes)
    if avg_close <= 0:
        return None
    return (max(highs) - min(lows)) / avg_close * 100


def compute_beta(db: Database, symbol: str, index_symbol: str = "NIFTY", months: int = 12) -> float | None:
    """
    Beta vs the index, from monthly close-to-close returns — what
    Rule S-07's "beta < 1.2" checks. Returns None if there isn't
    enough overlapping history for both series (need at least 3
    return pairs to compute a meaningful covariance/variance).
    """
    stock_docs = sorted(db.monthly_ohlc.find({"symbol": symbol}), key=lambda d: d["month_key"])
    index_docs = sorted(db.monthly_ohlc.find({"symbol": index_symbol}), key=lambda d: d["month_key"])

    stock_by_month = {d["month_key"]: d["close"] for d in stock_docs}
    index_by_month = {d["month_key"]: d["close"] for d in index_docs}
    common_months = sorted(set(stock_by_month) & set(index_by_month))[-months:]

    if len(common_months) < 4:  # need at least 3 returns
        return None

    stock_closes = [stock_by_month[m] for m in common_months]
    index_closes = [index_by_month[m] for m in common_months]

    stock_returns = [(stock_closes[i] - stock_closes[i-1]) / stock_closes[i-1] for i in range(1, len(stock_closes))]
    index_returns = [(index_closes[i] - index_closes[i-1]) / index_closes[i-1] for i in range(1, len(index_closes))]

    n = len(stock_returns)
    mean_s = sum(stock_returns) / n
    mean_i = sum(index_returns) / n
    covariance = sum((stock_returns[i] - mean_s) * (index_returns[i] - mean_i) for i in range(n)) / n
    variance_i = sum((r - mean_i) ** 2 for r in index_returns) / n

    if variance_i < 1e-12:
        return None
    return round(covariance / variance_i, 2)
