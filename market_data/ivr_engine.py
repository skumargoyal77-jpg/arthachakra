"""
market_data/ivr_engine.py
─────────────────────────────
Computes IV Rank (IVR) — where today's implied volatility sits within
its trailing range. This is what Rule S-08 ("only sell premium when
IVR > 40") actually checks.

Formula (unchanged from the earlier POC-06 spike, which validated this
against real Sensibull reference values within a few percentage points):

    IVR = (current_IV - min_IV_period) / (max_IV_period - min_IV_period) × 100

PROJECT PATH:  market_data/ivr_engine.py
"""

from __future__ import annotations

from typing import Optional

from core.logging_config import setup_logging

logger = setup_logging(__name__)


def compute_ivr(
    iv_series: list[float], current_iv: Optional[float] = None, lookback_days: int = 252,
) -> Optional[float]:
    """
    Compute IV Rank from a historical IV series.

    Args:
        iv_series:     Historical daily IV values (ideally 252 trading
                       days = ~1 year; works with less, less reliably).
        current_iv:    Today's IV to rank. Defaults to the series' last value.
        lookback_days: Trading days to use for the min/max window.

    Returns None if there's insufficient history (<5 points) rather
    than guessing a number from too little data.
    """
    series = [v for v in iv_series if v is not None]
    if len(series) < 5:
        logger.debug("IV series too short (%d values, need >=5) — IVR unavailable.", len(series))
        return None

    window = series[-lookback_days:]
    if current_iv is None:
        current_iv = series[-1]

    min_iv, max_iv = min(window), max(window)
    if abs(max_iv - min_iv) < 1e-6:
        logger.debug("IV range near-zero (min=%.4f max=%.4f) — returning 50 (mid-range).", min_iv, max_iv)
        return 50.0

    ivr = (current_iv - min_iv) / (max_iv - min_iv) * 100.0
    return max(0.0, min(100.0, round(ivr, 1)))


def ivr_signal(ivr: float) -> str:
    """Trading signal per Rule S-08's thresholds."""
    if ivr >= 40:
        return "SELL — IVR >= 40, premium is expensive relative to its range"
    if ivr >= 30:
        return "REDUCE — IVR 30-40, consider reduced size"
    return "SKIP — IVR < 30, premium is cheap relative to its range"
