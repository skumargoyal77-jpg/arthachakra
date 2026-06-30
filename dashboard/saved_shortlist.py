"""
dashboard/saved_shortlist.py
─────────────────────────────────
The manual, checkbox-curated shortlist — POC-13's original "Action
Plan" concept (a human selects stocks from the table and saves them),
kept as a DISTINCT, separate workflow alongside dashboard/action_plan.py's
automatic rule-engine verdicts. Both exist side by side; this file does
NOT replace the rule-engine Action Plan.

MULTIPLE NAMED SHORTLISTS PER MONTH (added after initial Step 9 build):
  Originally one shortlist per user per month (unique on user_id +
  month_key). Changed to allow MULTIPLE per month, distinguished by a
  user-given shortlist_name (e.g. "Conservative", "High IV Plays") —
  unique key is now (user_id, month_key, shortlist_name). Saving with
  the same name+month again overwrites that specific shortlist; a
  different name creates a new, separate one.

NAMED DIFFERENTLY ON PURPOSE — "saved_shortlist", not "action_plan" —
to avoid any confusion with dashboard/action_plan.py's ActionPlan
dataclass, even though both ultimately mean "stocks I'm planning to
trade this month." The underlying Mongo collection is still called
action_plans (defined back in Step 1's schema).

PROJECT PATH:  dashboard/saved_shortlist.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.database import Database

DEFAULT_SHORTLIST_NAME = "Default"


def save_shortlist(
    db: Database, user_id: str, month_key: str, symbols: list[str],
    shortlist_name: str = DEFAULT_SHORTLIST_NAME,
    filters: Optional[dict] = None, notes: str = "",
) -> None:
    shortlist_name = shortlist_name.strip() or DEFAULT_SHORTLIST_NAME
    db.action_plans.update_one(
        {"user_id": user_id, "month_key": month_key, "shortlist_name": shortlist_name},
        {
            "$set": {
                "user_id": user_id, "month_key": month_key, "shortlist_name": shortlist_name,
                "symbols": symbols, "filters": filters or {}, "notes": notes,
                "updated_at": datetime.now(tz=timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(tz=timezone.utc)},
        },
        upsert=True,
    )


def get_shortlist(
    db: Database, user_id: str, month_key: str, shortlist_name: str = DEFAULT_SHORTLIST_NAME,
) -> Optional[dict]:
    """
    Looks up one named shortlist. Also finds a pre-migration document
    (saved before shortlist_name existed) when shortlist_name is the
    default — filtering by {"shortlist_name": "Default"} alone would
    NOT match such a document at all (it has no such field), silently
    returning None even though a real shortlist exists.

    Uses a plain Python scan rather than Mongo's $exists operator,
    since the mock in-memory collection (core/database.py) only
    supports simple equality filters, not Mongo query operators —
    this keeps mock and real Mongo behaving identically.
    """
    doc = db.action_plans.find_one(
        {"user_id": user_id, "month_key": month_key, "shortlist_name": shortlist_name}
    )
    if doc is None and shortlist_name == DEFAULT_SHORTLIST_NAME:
        for d in db.action_plans.find({"user_id": user_id, "month_key": month_key}):
            if "shortlist_name" not in d:
                d.setdefault("shortlist_name", DEFAULT_SHORTLIST_NAME)
                doc = d
                break
    return doc


def get_shortlists_for_month(db: Database, user_id: str, month_key: str) -> list[dict]:
    """
    All of this user's named shortlists for one month — what the
    Action Plan tab's dropdown needs to populate its choices.

    Defensively backfills shortlist_name for any document saved
    BEFORE this field existed (the original Step 9 build only had one
    shortlist per user per month, no name at all) — without this,
    those old documents would KeyError downstream instead of just
    showing up labeled "Default", same as if they'd been saved with
    the new code from the start.
    """
    docs = db.action_plans.find({"user_id": user_id, "month_key": month_key})
    for d in docs:
        d.setdefault("shortlist_name", DEFAULT_SHORTLIST_NAME)
    return sorted(docs, key=lambda d: d.get("shortlist_name", ""))


def get_all_shortlists(db: Database, user_id: str) -> list[dict]:
    docs = db.action_plans.find({"user_id": user_id})
    for d in docs:
        d.setdefault("shortlist_name", DEFAULT_SHORTLIST_NAME)
    return sorted(docs, key=lambda d: (d["month_key"], d.get("shortlist_name", "")), reverse=True)


def delete_shortlist(db: Database, user_id: str, month_key: str, shortlist_name: str) -> bool:
    return db.action_plans.delete_one(
        {"user_id": user_id, "month_key": month_key, "shortlist_name": shortlist_name}
    )
