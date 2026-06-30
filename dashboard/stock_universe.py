"""
dashboard/stock_universe.py
─────────────────────────────
Shared stock universe + historical OHLC access for the stock scanner.

ADAPTED FROM POC-13's stock_analysis/db_manager.py — the original used
its own separate MongoDB connection and its own monthly_ohlc collection
in a different database entirely. This version uses our project's
existing core.database.Database and the SAME nse_stocks /
monthly_ohlc collections that market_data/ohlc_updater.py already
populates (Step 5) — one shared OHLC pipeline, not two parallel ones.

BOTH COLLECTIONS ARE SHARED (not per-user) — stock universe and
historical price data are objective market facts, not personal to any
one user. Per-user differentiation happens one layer up, in
dashboard/action_plan.py, which filters this shared data through each
user's own effective rules.

PROJECT PATH:  dashboard/stock_universe.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.database import Database
from core.logging_config import setup_logging

logger = setup_logging(__name__)


# ── Stock universe ──────────────────────────────────────────────────────────

def upsert_stock(
    db: Database, symbol: str, full_name: str = "", sector: str = "", active: bool = True,
    is_index: bool = False, lot_size: int | None = None,
) -> None:
    """
    index_memberships (Nifty50/Nifty100/BankNifty/etc) is intentionally
    NOT a parameter here — that's set separately by
    market_data/nse_index_constituents.py via set_index_memberships(),
    since it comes from a different data source entirely and shouldn't
    be silently overwritten to [] every time this function is called
    for an unrelated reason (e.g. re-importing the F&O universe).
    """
    update: dict = {
        "symbol": symbol, "full_name": full_name, "sector": sector,
        "active": active, "is_index": is_index,
        "updated_at": datetime.now(tz=timezone.utc),
    }
    if lot_size is not None:
        update["lot_size"] = lot_size
    db.nse_stocks.update_one(
        {"symbol": symbol},
        {"$set": update, "$setOnInsert": {"index_memberships": []}},
        upsert=True,
    )


def set_index_memberships(db: Database, symbol: str, memberships: list[str]) -> None:
    """Sets which indices (NIFTY50, NIFTY100, BANKNIFTY, etc) this symbol belongs to."""
    db.nse_stocks.update_one(
        {"symbol": symbol},
        {"$set": {"index_memberships": memberships}},
        upsert=True,
    )


def get_all_stocks(db: Database, active_only: bool = True) -> list[dict]:
    filt = {"active": True} if active_only else {}
    docs = db.nse_stocks.find(filt)
    return sorted(docs, key=lambda d: d["symbol"])


def get_stock(db: Database, symbol: str) -> Optional[dict]:
    return db.nse_stocks.find_one({"symbol": symbol})


def get_all_symbols(db: Database, active_only: bool = True) -> list[str]:
    return [s["symbol"] for s in get_all_stocks(db, active_only)]


# ── OHLC access (reads what market_data/ohlc_updater.py already writes) ────

def get_ohlc_for_analysis(db: Database, symbol: str) -> list[dict]:
    """
    Returns [{month_key, open, high, low}, ...] — exactly the shape
    dashboard.stock_analysis.records_from_dicts() expects. Reads the
    SAME monthly_ohlc collection Step 5's ohlc_updater.py populates.
    """
    docs = db.monthly_ohlc.find({"symbol": symbol})
    return sorted(
        [{"month_key": d["month_key"], "open": d["open"], "high": d["high"], "low": d["low"]}
         for d in docs],
        key=lambda d: d["month_key"],
    )


def get_latest_month(db: Database, symbol: str) -> Optional[str]:
    docs = db.monthly_ohlc.find({"symbol": symbol})
    if not docs:
        return None
    return max(d["month_key"] for d in docs)


def get_month_count(db: Database, symbol: str) -> int:
    return db.monthly_ohlc.count_documents({"symbol": symbol})


def manual_upsert_ohlc(db: Database, symbol: str, month_key: str, open_: float, high: float, low: float) -> None:
    """
    Manual entry path for the Monthly Update tab — a fallback/override
    alongside the two automated paths (daily Bhavcopy in
    market_data/ohlc_updater.py, bulk yfinance backfill in
    market_data/yfinance_backfill.py) for whenever a stock is missing
    from both of those.
    """
    db.monthly_ohlc.update_one(
        {"symbol": symbol, "month_key": month_key},
        {"$set": {
            "symbol": symbol, "month_key": month_key,
            "open": open_, "high": high, "low": low, "close": open_,
            "source": "manual",
        }},
        upsert=True,
    )


def get_universe_stats(db: Database) -> dict:
    return {
        "stocks": db.nse_stocks.count_documents({}),
        "active_stocks": db.nse_stocks.count_documents({"active": True}),
        "ohlc_records": db.monthly_ohlc.count_documents({}),
    }
