"""
market_data/iv_updater.py
─────────────────────────────
Orchestrates the daily IV/IVR update: Bhavcopy -> ATM implied vol (via
the same Black-Scholes solver dashboard/greeks.py already uses for
live Greeks) -> store in iv_history -> compute IVR from history.

This is what closes Rule S-08 ("only sell when IVR > 40") and feeds
S-06/S-07's underlying price needs (see ohlc_updater.py).

PROJECT PATH:  market_data/iv_updater.py
"""

from __future__ import annotations

from datetime import date

from core.database import Database
from core.logging_config import setup_logging
from dashboard.greeks import DEFAULT_RISK_FREE_RATE, implied_volatility
from market_data.bhavcopy import BhavcopyScraper
from market_data.ivr_engine import compute_ivr

logger = setup_logging(__name__)

DAYS_TO_EXPIRY_MIN = 1  # avoid T=0 degenerate cases


def _nearest_atm_row(df, spot: float):
    """Pick the option closest to ATM (smallest |strike - spot|) for the nearest expiry."""
    if df is None or df.empty:
        return None
    nearest_expiry = df["EXPIRY_DT"].min()
    near = df[df["EXPIRY_DT"] == nearest_expiry].copy()
    if near.empty:
        return None
    near["abs_diff"] = (near["STRIKE_PR"] - spot).abs()
    return near.sort_values("abs_diff").iloc[0]


def compute_atm_iv(scraper: BhavcopyScraper, symbol: str, dt: date) -> float | None:
    """
    Find the ATM option for `symbol` on `dt`, solve its implied vol via
    Black-Scholes. Returns None if data is missing or the solver
    doesn't converge — never a guessed fallback number.
    """
    spot = scraper.get_spot_price(symbol, dt)
    if not spot:
        logger.debug("No spot price for %s on %s — skipping IV.", symbol, dt)
        return None

    df = scraper.load_for_symbol(symbol, dt, option_type="CE")
    row = _nearest_atm_row(df, spot)
    if row is None:
        return None

    expiry = row["EXPIRY_DT"].date()
    days_to_expiry = (expiry - dt).days
    if days_to_expiry < DAYS_TO_EXPIRY_MIN:
        return None
    T = days_to_expiry / 365.0

    sigma, converged = implied_volatility(
        S=spot, K=float(row["STRIKE_PR"]), T=T, r=DEFAULT_RISK_FREE_RATE,
        market_price=float(row["CLOSE"]), option_type="CE",
    )
    return sigma if converged else None


def update_iv_for_symbol(db: Database, scraper: BhavcopyScraper, symbol: str, dt: date) -> dict:
    """
    Computes today's ATM IV for one symbol, stores it in iv_history,
    and computes the resulting IVR from accumulated history.

    Returns {"symbol", "date", "iv_atm", "ivr"} — any field may be
    None if that step's data/computation wasn't available.
    """
    iv = compute_atm_iv(scraper, symbol, dt)

    if iv is not None:
        db.iv_history.update_one(
            {"symbol": symbol, "date": dt.isoformat()},
            {"$set": {"symbol": symbol, "date": dt.isoformat(), "iv_atm": iv}},
            upsert=True,
        )

    history = db.iv_history.find({"symbol": symbol})
    series = [h["iv_atm"] for h in sorted(history, key=lambda h: h["date"]) if h.get("iv_atm") is not None]
    ivr = compute_ivr(series, current_iv=iv) if series else None

    return {"symbol": symbol, "date": dt.isoformat(), "iv_atm": iv, "ivr": ivr}


def get_latest_ivr(db: Database, symbol: str) -> float | None:
    """
    Read-only IVR from already-stored iv_history — no live Bhavcopy
    fetch needed. For callers (like dashboard/action_plan.py) that
    just want "what's the IVR right now" without triggering a fresh
    NSE download every time.
    """
    history = db.iv_history.find({"symbol": symbol})
    series = [h["iv_atm"] for h in sorted(history, key=lambda h: h["date"]) if h.get("iv_atm") is not None]
    if not series:
        return None
    return compute_ivr(series)


def update_iv_for_watchlist(db: Database, symbols: list[str], dt: date) -> list[dict]:
    """Runs update_iv_for_symbol for every symbol in the watchlist. One bad symbol never stops the rest."""
    scraper = BhavcopyScraper()
    if not scraper.download_date(dt):
        logger.warning("Bhavcopy not available for %s (weekend/holiday/not-yet-published).", dt)
        return []

    results = []
    for symbol in symbols:
        try:
            results.append(update_iv_for_symbol(db, scraper, symbol, dt))
        except Exception as e:
            logger.warning("IV update failed for %s on %s: %s", symbol, dt, e)
            results.append({"symbol": symbol, "date": dt.isoformat(), "iv_atm": None, "ivr": None, "error": str(e)})
    return results
