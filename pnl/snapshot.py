"""
pnl/snapshot.py
───────────────────
Daily end-of-day P&L snapshot — one document per user per date in
daily_pnl.

WHY THIS REUSES agent/context_builder.py RATHER THAN FETCHING
POSITIONS ITSELF:
  Position fetching + spot-price resolution + strangle grouping has
  already been built, tested, and fixed multiple times (the mock-spot
  fallback bug from Step 6, the symbol-parsing day-vs-year bug from
  Step 6, etc.) inside agent/context_builder.py's build_context().
  Re-implementing any of that here would either duplicate those fixes
  or risk silently regressing one of them. This module calls
  build_context() directly and extracts what it needs from the
  already-correct AgentContext, rather than maintaining a second copy
  of the same fetch logic.

PARALLEL ACROSS USERS, NOT JUST WITHIN ONE USER'S CONNECTIONS:
  build_context() already parallelizes one user's own fetches
  (positions + VIX). snapshot_all_users() goes one level further and
  snapshots every active user concurrently via asyncio.gather — this
  is also what proves the Step 8 checkpoint by construction: each
  snapshot_user() call only ever touches its own user_id's session,
  so two users' snapshots running at the same moment can't cross-talk,
  the same argument already used for IntegrationAgent in Step 6.

PROJECT PATH:  pnl/snapshot.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from agent.context_builder import build_context
from core.database import Database
from core.logging_config import setup_logging
from users.session_builder import build_user_session
from users.user_repository import list_users

logger = setup_logging(__name__)


@dataclass
class SnapshotResult:
    user_id: str
    display_name: str
    success: bool
    total_pnl: float = 0.0
    strangle_count: int = 0
    error: str | None = None


def _strangle_to_dict(s) -> dict:
    return {
        "underlying": s.underlying,
        "expiry": s.expiry,
        "connection_label": s.connection_label,
        "spot": s.spot,
        "pnl": s.total_pnl,
        "ce_legs": len(s.ce_legs),
        "pe_legs": len(s.pe_legs),
        "delta_status": s.delta_status,
    }


async def snapshot_user(db: Database, user_id: str, display_name: str, as_of: date | None = None) -> SnapshotResult:
    """
    Builds and writes ONE daily_pnl document for ONE user. Never reads
    or writes any other user's data — this is what the Step 8
    checkpoint actually verifies, by construction.
    """
    as_of = as_of or date.today()
    try:
        session = build_user_session(db, user_id, display_name)
        ctx = await build_context(session, db)

        total_pnl = sum(s.total_pnl for s in ctx.strangles)
        doc = {
            "user_id": user_id,
            "date": as_of.isoformat(),
            "captured_at": datetime.now(),
            "total_pnl": total_pnl,
            "strangle_count": len(ctx.strangles),
            "unmatched_position_count": len(ctx.unmatched_positions),
            "strangles": [_strangle_to_dict(s) for s in ctx.strangles],
            "fetch_errors": ctx.fetch_errors,
        }

        db.daily_pnl.update_one(
            {"user_id": user_id, "date": as_of.isoformat()},
            {"$set": doc},
            upsert=True,
        )

        logger.info(
            "Snapshot user=%s date=%s: %d strangles, total_pnl=%.2f",
            user_id, as_of, len(ctx.strangles), total_pnl,
        )
        return SnapshotResult(
            user_id=user_id, display_name=display_name, success=True,
            total_pnl=total_pnl, strangle_count=len(ctx.strangles),
        )

    except Exception as e:
        logger.exception("Snapshot failed for user=%s", user_id)
        return SnapshotResult(user_id=user_id, display_name=display_name, success=False, error=str(e))


async def snapshot_all_users(db: Database, as_of: date | None = None) -> list[SnapshotResult]:
    """
    Snapshots every active user CONCURRENTLY. One user's failure
    (broken connection, network error) never blocks or corrupts
    another's — each task is independent, errors are caught per-task.
    """
    users = list_users(db, active_only=True)
    tasks = [snapshot_user(db, u.user_id, u.display_name, as_of) for u in users]
    return await asyncio.gather(*tasks)


def run_daily_snapshot(db: Database, as_of: date | None = None) -> list[SnapshotResult]:
    """Sync entry point for scripts/CLI — wraps the async snapshot in one event loop."""
    return asyncio.run(snapshot_all_users(db, as_of))
