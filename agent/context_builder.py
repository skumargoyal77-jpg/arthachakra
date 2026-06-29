"""
agent/context_builder.py
─────────────────────────────
Prefetches everything the agent's tools might need for ONE user's
session, concurrently rather than one-at-a-time, before the LLM loop
ever starts. This is the "parallel prefetch" piece from the Phase 2
plan.

WHY asyncio.to_thread, NOT NATIVE ASYNC HTTP:
  Every underlying call (Kite SDK, requests-based VIX fetch) is a
  blocking, synchronous library — none of Steps 1-5 were built async,
  and rewriting kiteconnect/requests calls to be natively async isn't
  worth it here. asyncio.to_thread() runs each blocking call in its
  own thread, so they genuinely overlap in wall-clock time even though
  the underlying code is synchronous — the standard, correct way to
  parallelize blocking I/O without rewriting it.

PER-USER, NEVER SHARED:
  AgentContext is built fresh from one UserSession every call — no
  module-level caching, no globals. Two concurrent build_context()
  calls for two different users never touch each other's data, by
  construction (each holds only its own BrokerConnection objects).

PROJECT PATH:  agent/context_builder.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

from brokers.kite_client import fetch_ltp, fetch_positions
from core.database import Database
from core.logging_config import setup_logging
from dashboard.strangle_grouper import (
    KITE_SPOT_MAP, MOCK_POSITIONS, MOCK_SPOTS,
    group_positions_into_strangles, parse_option_symbol,
)
from market_data.vix_fetcher import fetch_live_vix, get_vix_history
from users.models import UserSession

logger = setup_logging(__name__)


@dataclass
class AgentContext:
    """Everything prefetched for one user, before the LLM loop starts."""
    session: UserSession
    vix: float | None = None
    vix_5day_readings: list[float] = field(default_factory=list)
    strangles: list = field(default_factory=list)
    unmatched_positions: list[dict] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)
    as_of: date = field(default_factory=date.today)
    as_of_datetime: datetime = field(default_factory=datetime.now)


def _fetch_all_positions(session: UserSession) -> list[dict]:
    """Sync helper — runs inside asyncio.to_thread. Aggregates positions
    across every one of THIS user's connections (never another user's)."""
    all_raw: list[dict] = []
    for conn in session.active_connections:
        if conn.access_token and conn.access_token.startswith("mock_tok_"):
            for p in MOCK_POSITIONS:
                tagged = dict(p)
                tagged["_connection_id"] = conn.connection_id
                tagged["_connection_label"] = f"{conn.label} (mock)"
                all_raw.append(tagged)
        else:
            raw = fetch_positions(conn)
            for p in raw:
                p["_connection_id"] = conn.connection_id
                p["_connection_label"] = conn.label
            all_raw.extend(raw)
    return all_raw


def _fetch_vix_sync(session: UserSession, db: Database) -> float | None:
    """Sync helper — any one active real connection can fetch VIX (shared market data)."""
    real_conn = next(
        (c for c in session.active_connections
         if c.access_token and not c.access_token.startswith("mock_tok_")),
        None,
    )
    if real_conn is None:
        return None
    return fetch_live_vix(real_conn)


async def build_context(session: UserSession, db: Database) -> AgentContext:
    """
    Builds one AgentContext for one UserSession, fetching positions and
    VIX concurrently. Never reaches outside `session`'s own connections.
    """
    ctx = AgentContext(session=session)

    positions_task = asyncio.to_thread(_fetch_all_positions, session)
    vix_task = asyncio.to_thread(_fetch_vix_sync, session, db)

    positions_result, vix_result = await asyncio.gather(
        positions_task, vix_task, return_exceptions=True,
    )

    if isinstance(positions_result, Exception):
        ctx.fetch_errors.append(f"positions fetch failed: {positions_result}")
        all_raw = []
    else:
        all_raw = positions_result

    if isinstance(vix_result, Exception):
        ctx.fetch_errors.append(f"VIX fetch failed: {vix_result}")
    else:
        ctx.vix = vix_result

    ctx.vix_5day_readings = [r["value"] for r in get_vix_history(db, days=5)]

    if all_raw:
        needed_underlyings: set[str] = set()
        for p in all_raw:
            parsed = parse_option_symbol(p.get("tradingsymbol", ""))
            if parsed:
                needed_underlyings.add(parsed.underlying)

        # Real spot prices via Kite when a real connection exists — was
        # previously always defaulting to MOCK_SPOTS regardless of
        # connection type, which silently returned stale placeholder
        # values (e.g. ASIANPAINT's hardcoded mock of 2450.0) even when
        # a real account was connected. Same fix already proven correct
        # in pages/1_Live_Dashboard.py — ported here.
        spot_prices: dict[str, float] = {}
        real_conn = next(
            (c for c in session.active_connections
             if c.access_token and not c.access_token.startswith("mock_tok_")),
            None,
        )
        if real_conn and needed_underlyings:
            underlying_to_instr = {u: KITE_SPOT_MAP.get(u, f"NSE:{u}") for u in needed_underlyings}
            ltp_data = fetch_ltp(real_conn, list(underlying_to_instr.values()))
            for u, instr in underlying_to_instr.items():
                spot_prices[u] = ltp_data.get(instr) or MOCK_SPOTS.get(u, 0.0)
        else:
            spot_prices = {u: MOCK_SPOTS.get(u, 0.0) for u in needed_underlyings}

        strangles, unmatched = group_positions_into_strangles(all_raw, spot_prices)
        ctx.strangles = strangles
        ctx.unmatched_positions = unmatched

    return ctx
