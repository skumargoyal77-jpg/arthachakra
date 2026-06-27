"""
scripts/embed_rulebook.py
─────────────────────────────
Embeds the rule book from MongoDB (db.platform_rules) into ChromaDB.

Run this:
  • Once after first seeding the rule book (python scripts/init_db.py)
  • Again after editing rules/seed_rules.py and re-seeding (use --overwrite)

Usage:
    python scripts/embed_rulebook.py              # embed (skip if already done)
    python scripts/embed_rulebook.py --overwrite   # re-embed all (after rule edits)
    python scripts/embed_rulebook.py --verify       # embed + run test queries

PROJECT PATH:  scripts/embed_rulebook.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from rag.rule_store import RuleStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed the rule book from MongoDB into ChromaDB")
    parser.add_argument("--overwrite", action="store_true", help="Delete existing embeddings and re-embed all")
    parser.add_argument("--verify", action="store_true", help="Run quick test queries after embedding")
    parser.add_argument("--model", default="all-MiniLM-L6-v2", help="Sentence-transformers model")
    args = parser.parse_args()

    print("\n" + "═" * 70)
    print("  ArthaChakra — Rule Book Embedder (Step 4)")
    print("═" * 70)

    db = Database()
    if db.is_mock:
        print("\n  ⚠️  MongoDB not reachable — using in-memory mock for this run.")

    mongo_count = db.platform_rules.count_documents({})
    if mongo_count == 0:
        print("\n  platform_rules is empty — seeding the rule book first...")
        from rules.rules_service import seed_rules_into_db
        report = seed_rules_into_db(db)
        mongo_count = db.platform_rules.count_documents({})
        print(f"  ✅ Seeded {report['inserted']} rules.")

    print(f"\n  Rules in MongoDB platform_rules: {mongo_count}")
    if mongo_count == 0:
        print("  ❌ Still nothing to embed after seeding attempt — check rules/seed_rules.py.")
        return 1

    store = RuleStore(model_name=args.model)
    already = store.count()
    if already > 0 and not args.overwrite:
        print(f"  ℹ️  {already} rules already embedded. Use --overwrite to re-embed.")
    else:
        n = store.embed_from_mongo(db, overwrite=args.overwrite)
        print(f"  ✅ Embedded {n} rules from MongoDB into ChromaDB.")

    print(f"  📚 Total in store: {store.count()}")
    print(f"  📁 Saved to: data/chroma_db/")

    if args.verify:
        print("\n  Running quick verification queries...")
        test_queries = [
            ("VIX limit before entering a new trade", ["S-01"]),
            ("what happens when a strike is breached", ["ES-01"]),
            ("max ratio between CE and PE legs", ["A-10", "L-03"]),
            ("margin cap for single stock", ["C-01", "C-04"]),
            ("going naked on one side", ["A-11"]),
        ]
        for query, expected in test_queries:
            results = store.query(query, n_results=4)
            retrieved = [r["rule_id"] for r in results]
            hits = [e for e in expected if e in retrieved]
            icon = "✅" if hits else "⚠️ "
            print(f"\n    Query : '{query}'")
            print(f"    Got   : {retrieved}")
            print(f"    Expect: {expected}  {icon} {len(hits)}/{len(expected)} found")

    print("\n  ✅ Done.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
