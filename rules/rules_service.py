"""
rules/rules_service.py
──────────────────────────
Everything that reads or writes the rule catalogue: seeding the rule
book into Mongo, merging platform + default + custom rules into one
per-user effective list, and the write-side operations (toggle a
default rule, add/remove a custom rule).

WHY UPSERT, NOT "INSERT IF MISSING":
  Step 1's seeding pattern only ever inserted a document if it didn't
  already exist — fine for throwaway test fixtures, wrong for a real
  rule book that gets edited over time. If a rule's wording or
  threshold changes in seed_rules.py, re-running the seed needs to
  actually UPDATE the existing document, not silently skip it because
  the rule_id already exists. seed_rules_into_db() below upserts every
  field except rule_id itself.

WHY get_effective_rules() LIVES HERE, NOT IN session_builder.py:
  It used to be a private function inside session_builder.py. Moved
  here so there's exactly one implementation — session_builder now
  delegates to this module instead of duplicating the merge logic.

PROJECT PATH:  rules/rules_service.py
"""

from __future__ import annotations

from core.database import Database
from rules.seed_rules import get_rule_book


# ── Seeding (upsert-aware) ────────────────────────────────────────────────

def seed_rules_into_db(db: Database) -> dict:
    """
    Upserts every rule in RULE_BOOK into platform_rules (all 55 are
    currently MANDATORY — see seed_rules.py). default_rules is left
    untouched here; it stays empty until an OPTIONAL rule exists to
    seed there.

    Returns a small report dict: {"inserted": n, "updated": n}.
    Safe to re-run any time seed_rules.py changes — existing rule_ids
    get their fields updated in place, nothing is duplicated.
    """
    inserted = 0
    updated = 0

    for rule in get_rule_book():
        existing = db.platform_rules.find_one({"rule_id": rule["rule_id"]})
        db.platform_rules.update_one(
            {"rule_id": rule["rule_id"]},
            {"$set": rule},
            upsert=True,
        )
        if existing is None:
            inserted += 1
        else:
            updated += 1

    return {"inserted": inserted, "updated": updated}


def remove_rules_not_in_book(db: Database) -> int:
    """
    Deletes any platform_rules document whose rule_id is NOT in the
    current RULE_BOOK — the cleanup step for rules that were removed
    from seed_rules.py entirely (not just edited). Safe by construction:
    get_effective_rules() only loops over what's currently in
    platform_rules, so a stale rule_id left behind would just sit there
    unused; this removes it properly rather than leaving dead rows.

    Returns the number of rules removed.
    """
    current_ids = {r["rule_id"] for r in get_rule_book()}
    existing_ids = {r["rule_id"] for r in db.platform_rules.find({})}
    stale_ids = existing_ids - current_ids

    for rule_id in stale_ids:
        db.platform_rules.delete_one({"rule_id": rule_id})

    return len(stale_ids)


# ── Effective rules merge (the canonical implementation) ─────────────────

def get_effective_rules(db: Database, user_id: str) -> list[dict]:
    """
    Merge precedence:
      1. Platform rules  — always included, always enabled=True
      2. Default rules   — enabled per user_rules override, else default_on
      3. Custom rules     — always belong to exactly one user

    This is the single source of truth for the merge — session_builder
    delegates here rather than re-implementing it.
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
        enabled = override["enabled"] if override else rule.get("default_on", True)
        effective.append({**rule, "source": "default", "enabled": enabled})

    custom_docs = db.user_rules.find({"user_id": user_id, "source": "custom"})
    for doc in custom_docs:
        cd = doc.get("custom_def") or {}
        effective.append({
            "rule_id":     doc["rule_id"],
            "name":        cd.get("name", "Custom Rule"),
            "description": (
                f"If {cd.get('metric')} {cd.get('operator')} {cd.get('value')} "
                f"-> {cd.get('action')}"
            ),
            "category":    "OPTIONAL",
            "group":       "Custom",
            "eval_type":   "THRESHOLD",
            "eval_status": "EVALUABLE",
            "handler":     "custom_rule_check",
            "source":      "custom",
            "enabled":     doc.get("enabled", True),
            "custom_def":  cd,
        })

    return effective


# ── Write-side: toggling and custom rules ─────────────────────────────────

def toggle_default_rule(db: Database, user_id: str, rule_id: str, enabled: bool) -> None:
    """
    Set a per-user override for a default (OPTIONAL) rule. Raises
    ValueError if rule_id doesn't exist in default_rules — toggling a
    MANDATORY (platform) rule isn't a thing; toggling an unknown rule_id
    isn't either.
    """
    if not db.default_rules.find_one({"rule_id": rule_id}):
        raise ValueError(f"'{rule_id}' is not a default (optional) rule.")

    db.user_rules.update_one(
        {"user_id": user_id, "rule_id": rule_id},
        {"$set": {
            "user_id": user_id, "rule_id": rule_id,
            "enabled": enabled, "source": "default", "custom_def": None,
        }},
        upsert=True,
    )


def add_custom_rule(
    db: Database, user_id: str, rule_id: str,
    name: str, metric: str, operator: str, value: float, action: str,
) -> None:
    """Adds a personal, user-only rule — never visible to other users."""
    db.user_rules.update_one(
        {"user_id": user_id, "rule_id": rule_id},
        {"$set": {
            "user_id": user_id, "rule_id": rule_id,
            "enabled": True, "source": "custom",
            "custom_def": {
                "name": name, "metric": metric,
                "operator": operator, "value": value, "action": action,
            },
        }},
        upsert=True,
    )


def remove_custom_rule(db: Database, user_id: str, rule_id: str) -> bool:
    return db.user_rules.delete_one({"user_id": user_id, "rule_id": rule_id, "source": "custom"})
