"""
dashboard/greeks.py
──────────────────────
Black-Scholes implied volatility + delta calculator — stdlib only, no
numpy/scipy dependency.

WHY THIS EXISTS:
  Kite Connect's positions API does not return option Greeks. To show
  per-leg delta, we back out implied volatility from the option's
  observable market premium via Black-Scholes, then compute delta from
  that solved volatility — the standard approach when no dedicated
  options-analytics feed is available.

APPROXIMATIONS (documented, not hidden):
  - Treats Indian options as European-style (a standard simplification;
    index options ARE European, stock options are technically American
    but commonly approximated this way for a quick delta estimate).
  - Time to expiry = calendar days / 365, not trading days / 252.
  - Risk-free rate defaults to a constant (~India's short-term rate)
    rather than a live rate — delta isn't very sensitive to small
    changes in r, so this doesn't meaningfully affect accuracy.
  - If the IV solver doesn't converge (degenerate inputs, zero premium,
    etc.), delta is reported as unavailable (None) rather than guessed —
    consistent with this project's "don't overclaim" approach elsewhere.

PROJECT PATH:  dashboard/greeks.py
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

DEFAULT_RISK_FREE_RATE = 0.065   # ~6.5%, approx India short-term rate
MAX_NEWTON_ITERATIONS  = 50
IV_TOLERANCE           = 1e-4
IV_MIN, IV_MAX         = 0.01, 5.0
INITIAL_IV_GUESS       = 0.30


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, using math.erf — no scipy/numpy needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """Black-Scholes price for a European call ("CE") or put ("PE")."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if option_type == "CE" else max(K - S, 0.0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """Black-Scholes delta for a European call ("CE") or put ("PE")."""
    if T <= 0 or sigma <= 0:
        if option_type == "CE":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if option_type == "CE" else _norm_cdf(d1) - 1.0


def implied_volatility(
    S: float, K: float, T: float, r: float, market_price: float, option_type: str,
) -> tuple[Optional[float], bool]:
    """
    Solve for implied volatility via Newton-Raphson, given an observed
    market price. Returns (sigma, converged). On failure or degenerate
    inputs, returns (None, False) — callers should treat this as
    "delta unavailable", not guess a fallback number.
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None, False

    sigma = INITIAL_IV_GUESS
    for _ in range(MAX_NEWTON_ITERATIONS):
        price = bs_price(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < IV_TOLERANCE:
            return sigma, True

        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        vega = S * math.sqrt(T) * _norm_pdf(d1)

        if vega < 1e-8:
            break  # vega too small — Newton's method would diverge

        sigma = sigma - diff / vega
        sigma = max(IV_MIN, min(IV_MAX, sigma))

    return None, False


def parse_expiry_to_date(expiry_str: str) -> Optional[date]:
    """
    Parse a Kite-style expiry string into a date.
    Handles both:
      - DDMMMYY  e.g. '26JUN26' (index options with year)
      - DDMMM    e.g. '26JUN'   (equity options, no year — infers nearest future)
    """
    expiry_str = expiry_str.strip().upper()
    try:
        return datetime.strptime(expiry_str, "%d%b%y").date()
    except ValueError:
        pass
    try:
        partial = datetime.strptime(expiry_str, "%d%b")
        today = date.today()
        candidate = date(today.year, partial.month, partial.day)
        if candidate < today - timedelta(days=7):
            candidate = date(today.year + 1, partial.month, partial.day)
        return candidate
    except ValueError:
        return None


def compute_leg_delta(
    spot: float, strike: float, expiry_str: str, premium: float, option_type: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    as_of: Optional[date] = None,
) -> dict:
    """
    Full orchestration: parse expiry, compute time-to-expiry, solve
    implied vol from the observed premium, compute delta from that vol.

    Returns:
        {"delta": float|None, "implied_vol_pct": float|None,
         "days_to_expiry": int|None, "converged": bool}
    """
    as_of = as_of or date.today()
    expiry_date = parse_expiry_to_date(expiry_str)

    if expiry_date is None or spot <= 0 or strike <= 0:
        return {"delta": None, "implied_vol_pct": None, "days_to_expiry": None, "converged": False}

    days_to_expiry = (expiry_date - as_of).days
    T = max(days_to_expiry, 0) / 365.0
    if days_to_expiry <= 0:
        T = 1.0 / 365.0 / 24.0  # ~1 hour — avoids div-by-zero on expiry day

    sigma, converged = implied_volatility(spot, strike, T, risk_free_rate, premium, option_type)

    if not converged or sigma is None:
        return {
            "delta": None, "implied_vol_pct": None,
            "days_to_expiry": days_to_expiry, "converged": False,
        }

    delta = bs_delta(spot, strike, T, risk_free_rate, sigma, option_type)
    return {
        "delta": delta, "implied_vol_pct": sigma * 100,
        "days_to_expiry": days_to_expiry, "converged": True,
    }
