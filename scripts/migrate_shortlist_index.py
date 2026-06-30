"""
scripts/migrate_shortlist_index.py
─────────────────────────────────────
ONE-TIME MIGRATION — fixes a real production bug found via live
testing: changing users/schema.py's index definition for action_plans
from (user_id, month_key) to (user_id, month_key, shortlist_name)
only changes what FUTURE index-creation calls will build. It does
NOT drop the OLD 2-field unique index that already physically exists
in a real, already-running MongoDB database — Mongo doesn't replace
indexes automatically just because the application code's schema
definition changed.

The practical symptom this caused: saving a second named shortlist for
a month that already had one raised DuplicateKeyError on the OLD
2-field index (user_id, month_key), even though the NEW, correct
3-field index (which WOULD allow it) may also already exist alongside
it — Mongo just enforces whichever unique indexes are actually present,
old and new together.

This script:
  1. Lists the action_plans collection's current indexes (so you can
     see the actual state before/after, not just trust this worked).
  2. Drops the stale "user_id_1_month_key_1" index if present.
  3. Re-runs ensure_indexes() so the correct 3-field index gets created.

SAFE TO RUN MULTIPLE TIMES — dropping a non-existent index is a no-op
(caught and reported, not an error), and ensure_indexes() is already
idempotent (used throughout this project's own scripts/init_db.py).

Usage:
    python scripts/migrate_shortlist_index.py

PROJECT PATH:  scripts/migrate_shortlist_index.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.schema import COLLECTION_SCHEMA

STALE_INDEX_NAME = "user_id_1_month_key_1"


def main() -> int:
    print("\n" + "=" * 70)
    print("  ArthaChakra - Migrate action_plans index (allow multiple shortlists/month)")
    print("=" * 70)

    db = Database()
    if db.is_mock:
        print("\n  MongoDB not reachable - nothing to migrate (mock mode has no real indexes).")
        return 1

    coll = db.get_collection("action_plans")

    print("\n  Current indexes on action_plans:")
    existing = list(coll.list_indexes())
    for idx in existing:
        print(f"    {idx['name']}: {dict(idx['key'])}"
             f"{' (unique)' if idx.get('unique') else ''}")

    stale_present = any(idx["name"] == STALE_INDEX_NAME for idx in existing)

    if stale_present:
        print(f"\n  Dropping stale index '{STALE_INDEX_NAME}'...")
        try:
            coll.drop_index(STALE_INDEX_NAME)
            print(f"  Dropped.")
        except Exception as e:
            print(f"  Failed to drop: {e}")
            return 1
    else:
        print(f"\n  Stale index '{STALE_INDEX_NAME}' not present - nothing to drop.")

    print("\n  Re-creating indexes from the current schema (users/schema.py)...")
    action_plans_schema = {"action_plans": COLLECTION_SCHEMA["action_plans"]}
    created = db.ensure_indexes(action_plans_schema)
    print(f"  {created} index(es) ensured.")

    print("\n  Final indexes on action_plans:")
    for idx in coll.list_indexes():
        print(f"    {idx['name']}: {dict(idx['key'])}"
             f"{' (unique)' if idx.get('unique') else ''}")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
