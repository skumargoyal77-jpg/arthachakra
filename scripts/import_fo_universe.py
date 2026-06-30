"""
scripts/import_fo_universe.py
───────────────────────────────────
Imports the full NSE F&O universe (~210 stocks + 5 indices) from
fo_mktlots.csv, then tags index memberships (Nifty50/100/BankNifty/etc).

CANNOT BE FULLY TESTED FROM THIS SANDBOX — the index-membership fetch
needs nsearchives.nseindia.com, not reachable here. The CSV import
itself needs no network at all (just the local file).

Usage:
    python scripts/import_fo_universe.py /path/to/fo_mktlots.csv
    python scripts/import_fo_universe.py /path/to/fo_mktlots.csv --skip-index-membership

PROJECT PATH:  scripts/import_fo_universe.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.fo_universe_importer import import_fo_universe


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the full NSE F&O universe")
    parser.add_argument("csv_path", help="Path to fo_mktlots.csv")
    parser.add_argument("--skip-index-membership", action="store_true",
                        help="Skip the Nifty50/100/BankNifty membership fetch")
    args = parser.parse_args()

    if not Path(args.csv_path).exists():
        print(f"❌ File not found: {args.csv_path}")
        return 1

    print("\n" + "═" * 70)
    print("  ArthaChakra — F&O Universe Import")
    print("═" * 70)

    db = Database()
    if db.is_mock:
        print("\n  ⚠️  MongoDB not reachable — nothing will persist.\n")

    summary = import_fo_universe(db, args.csv_path)
    print(f"\n  ✅ Imported {summary['stocks']} stocks + {summary['indices']} indices "
         f"({summary['total']} total)")

    if not args.skip_index_membership:
        print("\n  Fetching index memberships (Nifty50/100/BankNifty/etc)...")
        from market_data.nse_index_constituents import update_index_memberships
        membership_summary = update_index_memberships(db)
        for idx, count in membership_summary.items():
            icon = "✅" if count > 0 else "⚠️ "
            print(f"    {icon} {idx:<14} {count} constituent(s)")

    print("═" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
