"""
scripts/backfill_ohlc.py
─────────────────────────────
One-time historical OHLC backfill via yfinance — gets the Stock
Selector usable immediately with years of history, instead of waiting
for market_data/ohlc_updater.py's daily Bhavcopy job to accumulate
real months one at a time.

DEFAULTS TO THE FULL IMPORTED UNIVERSE — pulls every symbol already in
db.nse_stocks (run scripts/import_fo_universe.py first) rather than a
small hardcoded watchlist. With the real fo_mktlots.csv imported,
that's 211 stocks + 5 indices = 216 symbols, not 13.

CANNOT BE TESTED FROM THIS SANDBOX — query1.finance.yahoo.com isn't
reachable here. Run this on your own machine with real internet access.

Usage:
    python scripts/backfill_ohlc.py                          # all symbols in the universe
    python scripts/backfill_ohlc.py --symbols HDFCBANK TCS    # just these specific ones
    python scripts/backfill_ohlc.py --from-date 2019-01-01

PROJECT PATH:  scripts/backfill_ohlc.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.stock_universe import get_all_symbols
from market_data.yfinance_backfill import backfill_watchlist


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical monthly OHLC via yfinance")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Specific NSE symbols to backfill. Omit to backfill the FULL "
                            "imported universe (run scripts/import_fo_universe.py first).")
    parser.add_argument("--from-date", default="2021-01-01",
                        help="Start date YYYY-MM-DD (default: 2021-01-01, ~5 years of history)")
    parser.add_argument("--to-date", default=None, help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    print("\n" + "═" * 70)
    print(f"  ArthaChakra — Historical OHLC Backfill (yfinance)")
    print("═" * 70)

    db = Database()
    if db.is_mock:
        print("\n  ⚠️  MongoDB not reachable — nothing will persist.\n")

    if args.symbols:
        symbols = args.symbols
        print(f"\n  Using explicitly given symbols: {len(symbols)}")
    else:
        symbols = get_all_symbols(db)
        if not symbols:
            print("\n  ❌ No symbols found in the universe, and none given via --symbols.")
            print("     Run 'python scripts/import_fo_universe.py fo_mktlots.csv' first,")
            print("     or pass specific symbols with --symbols.")
            return 1
        print(f"\n  Using the FULL imported universe: {len(symbols)} symbols")

    print(f"  From date : {args.from_date}")
    print(f"  To date   : {args.to_date or 'today'}\n")

    results = backfill_watchlist(db, symbols, args.from_date, args.to_date)

    print("\n  Results:")
    total_months = 0
    failed = []
    for sym, n in results.items():
        icon = "✅" if n > 0 else "⚠️ "
        print(f"    {icon} {sym:<14} {n} month(s)")
        total_months += n
        if n == 0:
            failed.append(sym)

    print(f"\n  {total_months} total month-records written across {len(symbols)} symbol(s).")
    if failed:
        print(f"  ⚠️  {len(failed)} symbol(s) returned no data: {', '.join(failed[:20])}"
             f"{' ...' if len(failed) > 20 else ''}")
        print(f"      (often index symbols needing a yfinance ticker override, or a")
        print(f"      genuinely delisted/renamed symbol — not necessarily a bug.)")
    print("═" * 70)

    return 0 if total_months > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
