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
DHAN_EXPIRY_LIST_URL  = "https://api.dhan.co/v2/optionchain/expirylist"

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

INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _resolve_scrip(underlying: str) -> Optional[dict]:
    """
    Resolve {"scrip": security_id, "segment": ...} for an underlying.
    DHAN_SCRIP_MAP above is a fast path for common names (avoids a CSV
    download/parse on every call); anything not in it falls through to
    a dynamic lookup against Dhan's instrument master
    (dashboard/dhan_instruments.py) — this is what makes EVERY equity
    work automatically, not just the handful manually added above.
    """
    if underlying in DHAN_SCRIP_MAP:
        return DHAN_SCRIP_MAP[underlying]

    from dashboard.dhan_instruments import lookup_security_id
    resolved = lookup_security_id(underlying, is_index=underlying in INDEX_NAMES)
    if resolved:
        logger.info("dhan_greeks: dynamically resolved '%s' -> security_id=%s",
                   underlying, resolved["scrip"])
    else:
        logger.warning(
            "dhan_greeks: no scrip mapping for '%s' — not in static map, not found "
            "in Dhan's instrument master either. Using BS fallback for this leg.",
            underlying,
        )
    return resolved

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


def _log_error_body(resp, context: str) -> None:
    """
    Dhan's error response schema for THIS specific failure doesn't match
    the {"errorType","errorCode","errorMessage"} shape documented
    elsewhere — logging the FULL raw body instead of guessing field
    names is what actually tells us why a request was rejected.
    """
    try:
        body = resp.json()
        logger.warning("dhan_greeks: [%s] HTTP %d — full body: %s", context, resp.status_code, body)
    except Exception:
        logger.warning("dhan_greeks: [%s] HTTP %d — raw text: %s",
                       context, resp.status_code, resp.text[:500])


def fetch_dhan_expiry_list(
    scrip_info: dict, client_id: str, access_token: str, underlying: str = "",
) -> Optional[list[str]]:
    """
    Returns Dhan's own list of valid expiry dates ("YYYY-MM-DD") for an
    underlying. We MUST check our computed expiry against this before
    calling the option chain endpoint — Dhan validates the Expiry field
    strictly and returns HTTP 400 for any date that isn't an exact match
    to one of their listed series (e.g. after NSE discontinued weekly
    expiries for BANKNIFTY/FINNIFTY/MIDCPNIFTY in late 2024, only
    monthly dates are valid for those).

    Takes an ALREADY-RESOLVED scrip_info dict (see _resolve_scrip) —
    does not resolve it itself, so callers that already resolved it
    (fetch_greeks_for_strangles) don't trigger a duplicate lookup/log.
    """
    try:
        import requests
        resp = requests.post(
            DHAN_EXPIRY_LIST_URL,
            headers={"client-id": client_id, "access-token": access_token,
                    "Content-Type": "application/json"},
            json={"UnderlyingScrip": scrip_info["scrip"], "UnderlyingSeg": scrip_info["segment"]},
            timeout=10,
        )
        if resp.status_code != 200:
            _log_error_body(resp, f"expirylist:{underlying}")
            return None
        return resp.json().get("data", [])
    except Exception as e:
        logger.warning("dhan_greeks: expirylist request failed for %s: %s", underlying, e)
        return None


def _fetch_option_chain(
    scrip_info: dict, expiry_dhan: str, client_id: str, access_token: str, underlying: str = "",
) -> Optional[dict]:
    """
    Make one Dhan option chain API call. Returns raw JSON or None on
    failure. Takes an ALREADY-RESOLVED scrip_info dict — see
    fetch_dhan_expiry_list docstring for why.
    """
    try:
        import requests
        resp = requests.post(
            DHAN_OPTION_CHAIN_URL,
            headers={"client-id": client_id, "access-token": access_token,
                    "Content-Type": "application/json"},
            json={
                "UnderlyingScrip": scrip_info["scrip"],
                "UnderlyingSeg":   scrip_info["segment"],
                "Expiry":          expiry_dhan,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            _log_error_body(resp, f"optionchain:{underlying}")
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
    expiry_cache: dict[str, Optional[list[str]]] = {}
    scrip_cache: dict[str, Optional[dict]] = {}

    for underlying, expiry_kite in needed:
        expiry_dhan = _parse_kite_expiry(expiry_kite)
        if not expiry_dhan:
            logger.warning("dhan_greeks: could not parse expiry '%s'", expiry_kite)
            continue

        # Resolve the scrip ONCE per underlying (logs clearly if unresolved,
        # whether unmapped statically or not found in Dhan's instrument
        # master either) — avoids resolving (and logging) it twice below.
        if underlying not in scrip_cache:
            scrip_cache[underlying] = _resolve_scrip(underlying)
        if scrip_cache[underlying] is None:
            continue  # _resolve_scrip already logged exactly why

        scrip_info = scrip_cache[underlying]

        # Fetch (and cache) Dhan's own valid expiry list for this underlying,
        # so we know WHY a mismatch happens instead of guessing after a 400.
        if underlying not in expiry_cache:
            expiry_cache[underlying] = fetch_dhan_expiry_list(
                scrip_info, settings.dhan_client_id, settings.dhan_access_token,
                underlying=underlying,
            )
        valid_expiries = expiry_cache[underlying]

        if valid_expiries is not None and expiry_dhan not in valid_expiries:
            logger.warning(
                "dhan_greeks: computed expiry %s for %s is NOT in Dhan's valid "
                "list %s — skipping Dhan, using BS fallback for this leg. "
                "(Common cause: NSE discontinued weekly expiries for some "
                "indices in 2024 — only monthly dates remain valid.)",
                expiry_dhan, underlying, valid_expiries,
            )
            continue

        chain = _fetch_option_chain(
            scrip_info, expiry_dhan,
            settings.dhan_client_id, settings.dhan_access_token,
            underlying=underlying,
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
