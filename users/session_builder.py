"""
users/session_builder.py
────────────────────────────
Assembles the full UserSession for one user — read-side only.

WHY THIS FILE STAYS SIMPLE IN STEP 1:
  The WRITE-side logic for these three domains belongs to later steps:
    - Adding/removing broker connections      → Step 2 / Step 5
    - Toggling rules, adding custom rules      → Step 3
    - Generating/confirming Telegram codes     → Step 10
  Step 1 only needs to prove that READING these collections, filtered
  by user_id, returns correctly isolated data — which is the actual
  multi-tenant risk being validated here. The rule-merge logic below
  is intentionally written once, correctly, because it's pure and
  doesn't depend on which step adds the data: it returns "0 mandatory +
  0 optional" today (platform_rules/default_rules are still empty) and
  will return the real counts the moment Step 3 seeds them — with zero
  code changes required here.

PROJECT PATH:  users/session_builder.py
"""

from __future__ import annotations

from core.database import Database
from users.models import BrokerConnection, UserSession


def build_user_session(db: Database, user_id: str, display_name: str = "") -> UserSession:
    """
    Build the complete runtime session for one user. No part of this
    function reaches outside the given user_id for per-user data.
    """
    connections = _load_broker_connections(db, user_id)
    rules       = _load_effective_rules(db, user_id)
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


def _load_effective_rules(db: Database, user_id: str) -> list[dict]:
    """
    Merge precedence:
      1. Platform rules    — always included, always enabled=True
      2. Default rules     — enabled per user_rules override, else default_on
      3. Custom rules       — always belong to exactly one user
    """
    effective: list[dict] = []

    for rule in db.platform_rules.find({}):
        effective.append({**rule, "source": "platform", "enabled": True})

    overrides = {
        r["rule_id"]: r
        for r in db.user_rules.find({"user_id": user_id, "source": "default"})
    }
    for rule in db.default_rules.find({}):
        override = overrides.get(rule["rule_id"])
        enabled  = override["enabled"] if override else rule.get("default_on", True)
        effective.append({**rule, "source": "default", "enabled": enabled})

    custom_docs = db.user_rules.find({"user_id": user_id, "source": "custom"})
    for doc in custom_docs:
        cd = doc.get("custom_def") or {}
        effective.append({
            "rule_id":     doc["rule_id"],
            "name":        cd.get("name", "Custom Rule"),
            "description": (
                f"If {cd.get('metric')} {cd.get('operator')} {cd.get('value')} "
                f"→ {cd.get('action')}"
            ),
            "category":    "OPTIONAL",
            "group":       "Custom",
            "source":      "custom",
            "enabled":     doc.get("enabled", True),
            "custom_def":  cd,
        })

    return effective


def _load_telegram(db: Database, user_id: str) -> tuple[str | None, bool]:
    doc = db.telegram_config.find_one({"user_id": user_id})
    if not doc:
        return None, False
    return doc.get("chat_id"), doc.get("verified", False)
