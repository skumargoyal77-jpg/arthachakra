"""
scripts/check_rule_freshness.py
─────────────────────────────────────
Diagnostic: compares the rule book CURRENTLY IN CODE (rules/seed_rules.py)
against what's ACTUALLY STORED in your real MongoDB (db.platform_rules).

WHY THIS EXISTS: changing rules/seed_rules.py only changes what's in
THIS codebase. It never automatically updates an already-running
MongoDB — that only happens when rules_service.seed_rules_into_db() is
explicitly re-run (e.g. via scripts/init_db.py). If a rule's
eval_status was flipped from NOT_YET_EVALUABLE to EVALUABLE in a later
step (as happened for S-25/M-11/M-12/S-21-S-24/ES-09 in Step 7), but
the real database was never re-seeded since, the live app will keep
showing the OLD, stale status — looking exactly like a bug, even
though the code itself is correct.

Usage:
    python scripts/check_rule_freshness.py

PROJECT PATH:  scripts/check_rule_freshness.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from rules.seed_rules import get_rule_book


def main() -> int:
    print("\n" + "=" * 70)
    print("  ArthaChakra - Rule Book Freshness Check")
    print("=" * 70)

    db = Database()
    if db.is_mock:
        print("\n  MongoDB not reachable - nothing to compare.")
        return 1

    code_book = {r["rule_id"]: r for r in get_rule_book()}
    db_book = {r["rule_id"]: r for r in db.platform_rules.find({})}

    print(f"\n  Rules in code (rules/seed_rules.py): {len(code_book)}")
    print(f"  Rules in MongoDB (platform_rules):    {len(db_book)}")

    missing_in_db = set(code_book) - set(db_book)
    extra_in_db = set(db_book) - set(code_book)
    mismatched = []

    for rule_id, code_rule in code_book.items():
        db_rule = db_book.get(rule_id)
        if db_rule is None:
            continue
        if db_rule.get("eval_status") != code_rule.get("eval_status"):
            mismatched.append((
                rule_id, db_rule.get("eval_status"), code_rule.get("eval_status"),
            ))

    if missing_in_db:
        print(f"\n  Rules in code but NOT in your database ({len(missing_in_db)}):")
        print(f"    {sorted(missing_in_db)}")

    if extra_in_db:
        print(f"\n  Rules in your database but NOT in current code ({len(extra_in_db)}):")
        print(f"    {sorted(extra_in_db)}")

    if mismatched:
        print(f"\n  STALE eval_status - database disagrees with current code ({len(mismatched)}):")
        for rule_id, db_status, code_status in mismatched:
            print(f"    {rule_id}: database says '{db_status}', code says '{code_status}'")
        print(f"\n  FIX: run 'python scripts/init_db.py' to re-seed your database")
        print(f"       with the current rule book.")
    elif not missing_in_db and not extra_in_db:
        print(f"\n  Your database is fully up to date with the current code.")
        print(f"  If a rule still shows the wrong status in the live app, the")
        print(f"  actual cause is somewhere else - not a stale rule book.")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
