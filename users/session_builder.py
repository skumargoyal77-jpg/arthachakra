"""
users/session_builder.py
────────────────────────────
Assembles the full UserSession for one user — read-side only.

WHY THIS FILE STAYS SIMPLE:
  The WRITE-side logic for these three domains belongs to later steps:
    - Adding/removing broker connections      → Step 2 / Step 5
    - Toggling rules, adding custom rules      → Step 3 (rules_service)
    - Generating/confirming Telegram codes     → Step 10
  The rule-merge logic itself now lives in rules/rules_service.py
  (get_effective_rules) — this file just calls it, rather than keeping
  its own duplicate copy of the same merge precedence.

PROJECT PATH:  users/session_builder.py
"""

from __future__ import annotations

from core.database import Database
from rules.rules_service import get_effective_rules
from users.models import BrokerConnection, UserSession


def build_user_session(db: Database, user_id: str, display_name: str = "") -> UserSession:
    """
    Build the complete runtime session for one user. No part of this
    function reaches outside the given user_id for per-user data.
    """
    connections = _load_broker_connections(db, user_id)
    rules       = get_effective_rules(db, user_id)
    chat_id, verified = _load_telegram(db, user_id)

    return UserSession(
        user_id            = user_id,
        display_name       = display_name or user_id,
        broker_connections = connections,
        effective_rules    = rules,
        telegram_chat_id   = chat_id,
        telegram_verified  = verified,
    )


# ── Private loaders ──────────────────────────────────────────────────────

def _load_broker_connections(db: Database, user_id: str) -> list[BrokerConnection]:
    docs = db.broker_connections.find({"user_id": user_id, "active": True})
    return [BrokerConnection.from_dict(d) for d in docs]


def _load_telegram(db: Database, user_id: str) -> tuple[str | None, bool]:
    doc = db.telegram_config.find_one({"user_id": user_id})
    if not doc:
        return None, False
    return doc.get("chat_id"), doc.get("verified", False)
