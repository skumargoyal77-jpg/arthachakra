"""
scripts/daily_pnl_snapshot.py
───────────────────────────────────
Daily job: snapshots every active user's P&L into daily_pnl.

Run once daily, after market close (positions/P&L are most meaningful
once the trading day is done) — via cron/Task Scheduler, same pattern
as scripts/daily_token_refresh.py.

Usage:
    python scripts/daily_pnl_snapshot.py
    python scripts/daily_pnl_snapshot.py --date 2026-06-29   # backfill a specific date

PROJECT PATH:  scripts/daily_pnl_snapshot.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from pnl.snapshot import run_daily_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily P&L snapshot for every active user")
    parser.add_argument("--date", help="Specific date to snapshot (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()

    as_of = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    print("\n" + "═" * 70)
    print(f"  ArthaChakra — Daily P&L Snapshot ({as_of})")
    print("═" * 70)

    db = Database()
    if db.is_mock:
        print("\n  ⚠️  MongoDB not reachable — snapshot would run, but nothing persists.")

    results = run_daily_snapshot(db, as_of=as_of)

    print()
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    for r in succeeded:
        print(f"  ✅ {r.display_name:20s} {r.strangle_count} strangle(s)  P&L: {r.total_pnl:+,.2f}")
    for r in failed:
        print(f"  ❌ {r.display_name:20s} FAILED: {r.error}")

    print(f"\n  {len(succeeded)}/{len(results)} users snapshotted successfully.")
    print("═" * 70)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
