"""
brokers/kite_client.py
─────────────────────────
Minimal real Kite Connect client builder — pulled forward from the
officially-planned Step 5 ("brokers/") to support live positions in
Step 2.1. Step 5 will expand this folder with margin/order-placement
wrappers and a Dhan client; this file's contract (get_kite_client,
fetch_positions, fetch_ltp) is designed to stay stable when that happens.

PROJECT PATH:  brokers/kite_client.py
"""

from __future__ import annotations

from typing import Optional

from core.logging_config import setup_logging
from users.models import BrokerConnection

logger = setup_logging(__name__)


def get_kite_client(conn: BrokerConnection):
    """
    Build a live, authenticated KiteConnect client from a stored
    BrokerConnection. Returns None for mock connections (access_token
    starting with "mock_tok_") — there's nothing real to connect to.
    """
    if conn.access_token and conn.access_token.startswith("mock_tok_"):
        return None
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=conn.api_key)
        kite.set_access_token(conn.access_token)
        return kite
    except Exception as e:
        logger.warning("Could not build Kite client for '%s': %s", conn.label, e)
        return None


def fetch_positions(conn: BrokerConnection) -> list[dict]:
    """
    Fetch live net positions for one connection. Returns an empty list
    (not mock data) on failure or for mock connections — the caller
    (dashboard) decides what sample data to show for mock accounts,
    keeping this function's contract simple: real data, or nothing.
    """
    kite = get_kite_client(conn)
    if kite is None:
        return []
    try:
        return kite.positions().get("net", [])
    except Exception as e:
        logger.warning("Failed to fetch positions for '%s': %s", conn.label, e)
        return []


def fetch_ltp(conn: BrokerConnection, instruments: list[str]) -> dict[str, float]:
    """
    Fetch live LTP for a list of Kite instrument strings
    (e.g. "NSE:NIFTY BANK"). Returns {} on failure or for mock connections.
    """
    kite = get_kite_client(conn)
    if kite is None or not instruments:
        return {}
    try:
        raw = kite.ltp(instruments)
        return {k: v.get("last_price", 0.0) for k, v in raw.items()}
    except Exception as e:
        logger.warning("Failed to fetch LTP for '%s': %s", conn.label, e)
        return {}


def fetch_position_margin(conn: BrokerConnection, legs: list) -> Optional[float]:
    """
    Real margin required for a set of option legs, via Kite's basket
    order margin calculator (SPAN + exposure, with netting benefit
    across legs of the same strangle) — NOT an approximation.

    NOTE — UNVERIFIED AGAINST A LIVE ACCOUNT: kiteconnect's
    basket_order_margins() payload structure is implemented per Kite
    Connect's documented contract, but this exact call hasn't been
    tested against a real account from this environment. If it returns
    None unexpectedly, check the logged warning for the real exception
    — that's usually a field-name or structure mismatch we can fix in
    one round-trip, the same way the Dhan integration was debugged.

    Returns None for mock connections, on any failure, or if `legs` is
    empty — never a guessed/approximated number.
    """
    kite = get_kite_client(conn)
    if kite is None or not legs:
        return None
    try:
        orders = [
            {
                "exchange": "NFO",
                "tradingsymbol": leg.tradingsymbol,
                "transaction_type": "SELL" if leg.is_short else "BUY",
                "variety": "regular",
                "product": "NRML",
                "order_type": "MARKET",
                "quantity": leg.abs_qty,
            }
            for leg in legs
        ]
        result = kite.basket_order_margins(orders, consider_positions=True, mode="compact")
        return result.get("final", {}).get("total")
    except Exception as e:
        logger.warning(
            "Failed to fetch margin for '%s' basket (%d legs): %s — "
            "if this persists, the kiteconnect SDK call signature or "
            "response structure may need adjusting; this is the exact "
            "exception to report back.",
            conn.label, len(legs), e,
        )
        return None
