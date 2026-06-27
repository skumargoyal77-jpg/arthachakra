"""
market_data/vix_fetcher.py
───────────────────────────
Fetches India VIX via Kite's quote API and caches timestamped readings
in vix_history. This closes Rule S-01 (VIX hard limit), S-02 (VIX
5-day trend), EP-04 (VIX spike >5pts intraday), and S-15 (high-IV
entry protocol).

WHY TIMESTAMPED HISTORY, NOT JUST A LIVE VALUE:
  S-02 needs a 5-day trend, and EP-04 needs to detect a same-SESSION
  spike (today's reading vs an earlier reading from the same day) —
  neither is possible from a single live point value. Every fetch
  appends a new reading rather than overwriting; callers that just
  want "right now" use get_latest_vix().

VIX IS SHARED MARKET DATA, NOT PER-USER:
  Any one valid Kite connection can fetch it — it's the same number
  for everyone. fetch_and_cache_vix() takes a single BrokerConnection,
  not a list of all users' connections.

PROJECT PATH:  market_data/vix_fetcher.py
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from brokers.kite_client import get_kite_client
from core.database import Database
from core.logging_config import setup_logging
from users.models import BrokerConnection

logger = setup_logging(__name__)

VIX_INSTRUMENT = "NSE:INDIA VIX"


def fetch_live_vix(conn: BrokerConnection) -> Optional[float]:
    """
    Fetch the current India VIX value via one Kite connection. Returns
    None for mock connections or on any failure — never a guessed value.
    """
    kite = get_kite_client(conn)
    if kite is None:
        return None
    try:
        quote = kite.quote([VIX_INSTRUMENT])
        return quote.get(VIX_INSTRUMENT, {}).get("last_price")
    except Exception as e:
        logger.warning("Failed to fetch India VIX: %s", e)
        return None


def cache_vix_reading(db: Database, value: float, as_of: Optional[datetime] = None) -> None:
    """Append one timestamped VIX reading to vix_history."""
    ts = as_of or datetime.now()
    db.vix_history.insert_one({
        "value": value,
        "captured_at": ts,
        "date": ts.date().isoformat(),
    })


def fetch_and_cache_vix(db: Database, conn: BrokerConnection) -> Optional[float]:
    """Fetch live VIX and cache it in one call. Returns the value, or None on failure."""
    value = fetch_live_vix(conn)
    if value is not None:
        cache_vix_reading(db, value)
    return value


def get_latest_vix(db: Database) -> Optional[dict]:
    """Most recent cached VIX reading, or None if vix_history is empty."""
    readings = db.vix_history.find({})
    if not readings:
        return None
    return max(readings, key=lambda r: r["captured_at"])


def get_vix_history(db: Database, days: int = 5) -> list[dict]:
    """
    All cached VIX readings from the last `days` calendar days,
    oldest first — what S-02's trend check needs.
    """
    cutoff = datetime.now() - timedelta(days=days)
    readings = [r for r in db.vix_history.find({}) if r["captured_at"] >= cutoff]
    return sorted(readings, key=lambda r: r["captured_at"])


def get_todays_vix_readings(db: Database) -> list[dict]:
    """
    Every reading captured today, oldest first — what EP-04's
    intraday-spike check needs (compare earliest vs latest today).
    """
    today = datetime.now().date().isoformat()
    readings = [r for r in db.vix_history.find({"date": today})]
    return sorted(readings, key=lambda r: r["captured_at"])
