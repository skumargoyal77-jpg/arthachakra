"""
scripts/init_db.py
─────────────────────
One-time setup: connect to the real MongoDB and create every collection
+ index defined in users/schema.py.

Run this ONCE when pointing ArthaChakra at a real MongoDB instance for
the first time (or after adding new collections in a later step).
Safe to re-run — index creation is idempotent.

Run:
    python scripts/init_db.py

PROJECT PATH:  scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from scripts/ subfolder
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from rules.rules_service import seed_rules_into_db, remove_rules_not_in_book
from users.schema import COLLECTION_SCHEMA, print_schema_report


def main() -> int:
    print("\n" + "═" * 70)
    print("  ArthaChakra — Database Initialisation")
    print("═" * 70)

    db = Database()

    if db.is_mock:
        print("\n  ⚠️  MongoDB not reachable — nothing to initialise.")
        print("      Set ARTHACHAKRA_MONGO_URI in .env and try again.")
        return 1

    created = db.ensure_indexes(COLLECTION_SCHEMA)

    print_schema_report()
    print(f"\n  ✅ {created} indexes verified/created across "
          f"{len(COLLECTION_SCHEMA)} collections.")

    print("\n  Seeding rule book...")
    report = seed_rules_into_db(db)
    removed = remove_rules_not_in_book(db)
    print(f"  ✅ Rules: {report['inserted']} inserted, {report['updated']} updated"
          f"{f', {removed} stale removed' if removed else ''}.")

    print("═" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
