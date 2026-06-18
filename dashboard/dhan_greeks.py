"""
dashboard/dhan_greeks.py
─────────────────────────
Fetches option Greeks (delta, IV, gamma, theta, vega) from the Dhan HQ
option chain API and returns them in a keyed dict that Strangle.compute_deltas()
can consume directly.

WHY DHAN HQ OVER BLACK-SCHOLES:
  Dhan's option chain endpoint returns market-calculated Greeks derived
  from live, continuously updated bid-ask data — not from a single LTP
  snapshot. Their delta also accounts for the implied vol smile/skew
  (different IVs at different strikes for the same expiry), whereas
  our BS implementation assumes flat vol. The endpoint is public and
  NOT user-account-specific — one shared DHAN_CLIENT_ID/ACCESS_TOKEN
  in .env covers all users, no individual trading account needed.

BLACK-SCHOLES FALLBACK:
  If the Dhan API call fails, or a specific strike is missing from the
  option chain response, we fall back to BS-derived delta per leg (the
  code we already validated against Hull textbook values). The dashboard
  clearly marks which source each leg's delta came from ("dhan" or "bs").

DHAN OPTION CHAIN API:
  POST https://api.dhan.co/v2/optionchain
  Headers: client-id, access-token
  Body:    {"UnderlyingScrip": <scrip>, "UnderlyingSeg": "IDX_I"|"NSE_EQ",
            "Expiry": "YYYY-MM-DD"}

  Returns a JSON with "data" containing lists of CE/PE strikes, each with:
    "strikePrice", "lastTradedPrice", "delta", "gamma", "theta", "vega",
    "impliedVolatility", "openInterest", ...

PROJECT PATH:  dashboard/dhan_greeks.py
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from core.logging_config import setup_logging

logger = setup_logging(__name__)

# ── Dhan HQ API ─────────────────────────────────────────────────────────

DHAN_OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"

# Maps ArthaChakra underlying names to Dhan's scrip codes
# Index options use IDX_I segment, equity options use NSE_EQ
DHAN_SCRIP_MAP: dict[str, dict] = {
    "NIFTY":       {"scrip": 13, "segment": "IDX_I"},
    "BANKNIFTY":   {"scrip": 25, "segment": "IDX_I"},
    "MIDCPNIFTY":  {"scrip": 442, "segment": "IDX_I"},
    "FINNIFTY":    {"scrip": 27, "segment": "IDX_I"},
    "NIFTYNXT50":  {"scrip": 442, "segment": "IDX_I"},
    # Equity underlyings — add NSE symbol codes as needed
    "HDFCBANK":    {"scrip": 1333, "segment": "NSE_EQ"},
    "RELIANCE":    {"scrip": 2885, "segment": "NSE_EQ"},
    "TCS":         {"scrip": 11536, "segment": "NSE_EQ"},
    "SBIN":        {"scrip": 3045, "segment": "NSE_EQ"},
    "ICICIBANK":   {"scrip": 4963, "segment": "NSE_EQ"},
    "INFY":        {"scrip": 1594, "segment": "NSE_EQ"},
}

# Dhan expiry format is "YYYY-MM-DD"
# ArthaChakra expiry format from Kite is "DDMMMYY" (e.g. "08JUL26")


def _parse_kite_expiry(expiry_str: str) -> Optional[str]:
    """Convert Kite expiry "08JUL26" → Dhan expiry "2026-07-08"."""
    try:
        from datetime import datetime
        dt = datetime.strptime(expiry_str.upper(), "%d%b%y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _fetch_option_chain(
    underlying: str, expiry_dhan: str, client_id: str, access_token: str,
) -> Optional[dict]:
    """Make one Dhan option chain API call. Returns raw JSON or None on failure."""
    scrip_info = DHAN_SCRIP_MAP.get(underlying)
    if not scrip_info:
        logger.debug("dhan_greeks: no scrip mapping for '%s'", underlying)
        return None

    try:
        import requests
        resp = requests.post(
            DHAN_OPTION_CHAIN_URL,
            headers={"client-id": client_id, "access-token": access_token},
            json={
                "UnderlyingScrip": scrip_info["scrip"],
                "UnderlyingSeg":   scrip_info["segment"],
                "Expiry":          expiry_dhan,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("dhan_greeks: HTTP %d for %s %s", resp.status_code, underlying, expiry_dhan)
            return None
        return resp.json()
    except Exception as e:
        logger.warning("dhan_greeks: request failed for %s %s: %s", underlying, expiry_dhan, e)
        return None


def _extract_greeks_from_chain(
    chain_json: dict, underlying: str, expiry_kite: str,
) -> dict[tuple, dict]:
    """
    Parse Dhan option chain response into the greeks_map format:
        {(underlying, expiry_kite, strike, option_type): {delta, implied_vol_pct, ...}}
    """
    greeks_map: dict[tuple, dict] = {}
    data = chain_json.get("data", {})

    for option_type, key in [("CE", "callOptions"), ("PE", "putOptions")]:
        for entry in data.get(key, []):
            try:
                strike = int(float(entry.get("strikePrice", 0)))
                delta  = entry.get("delta")
                iv     = entry.get("impliedVolatility")

                if delta is None:
                    continue

                greeks_map[(underlying, expiry_kite, strike, option_type)] = {
                    "delta":           float(delta),
                    "implied_vol_pct": float(iv) if iv is not None else None,
                    "days_to_expiry":  None,   # not returned by Dhan — compute if needed
                    "converged":       True,
                    "source":          "dhan",
                }
            except (TypeError, ValueError, KeyError):
                continue

    return greeks_map


def fetch_greeks_for_strangles(strangles: list, settings) -> dict[tuple, dict]:
    """
    Main entry point called by app.py's render_live_positions().

    For each unique (underlying, expiry) across all strangles, fetches the
    full option chain from Dhan HQ and extracts Greeks into a unified map.
    Falls back to Black-Scholes per leg for any strike Dhan didn't return
    data for (e.g., if the contract isn't in Dhan's chain).

    Returns:
        {(underlying, expiry_kite, strike, option_type): {delta, implied_vol_pct,
                                                           converged, source}}
    """
    greeks_map: dict[tuple, dict] = {}

    if not getattr(settings, "dhan_client_id", "") or \
       not getattr(settings, "dhan_access_token", ""):
        logger.info("dhan_greeks: no Dhan credentials configured — using BS fallback for all legs")
        return _bs_fallback_for_strangles(strangles, greeks_map)

    # Collect unique (underlying, expiry) pairs needed
    needed: set[tuple[str, str]] = set()
    for s in strangles:
        needed.add((s.underlying, s.expiry))

    dhan_hits = 0
    for underlying, expiry_kite in needed:
        expiry_dhan = _parse_kite_expiry(expiry_kite)
        if not expiry_dhan:
            logger.warning("dhan_greeks: could not parse expiry '%s'", expiry_kite)
            continue

        chain = _fetch_option_chain(
            underlying, expiry_dhan,
            settings.dhan_client_id, settings.dhan_access_token,
        )
        if chain is None:
            continue

        extracted = _extract_greeks_from_chain(chain, underlying, expiry_kite)
        greeks_map.update(extracted)
        dhan_hits += len(extracted)
        logger.info("dhan_greeks: fetched %d Greek entries for %s %s", len(extracted), underlying, expiry_kite)

    # BS fallback for any leg not covered by Dhan response
    _bs_fallback_for_strangles(strangles, greeks_map)

    logger.info("dhan_greeks: total entries — %d from Dhan, remainder from BS fallback", dhan_hits)
    return greeks_map


def _bs_fallback_for_strangles(
    strangles: list, greeks_map: dict[tuple, dict],
) -> dict[tuple, dict]:
    """
    For every leg not already in greeks_map, compute delta via Black-Scholes.
    Mutates greeks_map in-place and returns it.
    """
    try:
        from dashboard.greeks import compute_leg_delta
    except ImportError:
        logger.warning("dhan_greeks: cannot import dashboard.greeks for BS fallback")
        return greeks_map

    today = date.today()
    for s in strangles:
        for leg in s.ce_legs + s.pe_legs:
            key = (s.underlying, s.expiry, leg.strike, leg.option_type)
            if key in greeks_map:
                continue  # already have Dhan data for this leg

            result = compute_leg_delta(
                spot=s.spot, strike=leg.strike, expiry_str=s.expiry,
                premium=leg.ltp, option_type=leg.option_type, as_of=today,
            )
            result["source"] = "bs"
            greeks_map[key] = result

    return greeks_map
