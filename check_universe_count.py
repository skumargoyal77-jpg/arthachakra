"""Shows exactly which functions exist in your LOCAL stock_universe.py
right now - to see precisely how stale it is vs what should be there."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dashboard.stock_universe as su

expected_functions = [
    "upsert_stock", "set_index_memberships", "get_all_stocks", "get_stock",
    "get_all_symbols", "get_ohlc_for_analysis", "get_latest_month",
    "get_month_count", "manual_upsert_ohlc", "get_universe_stats",
]

print("Functions found in YOUR local dashboard/stock_universe.py:\n")
present = [name for name in dir(su) if not name.startswith("_")]
for name in expected_functions:
    found = hasattr(su, name)
    print(f"  {'✅' if found else '❌ MISSING'}  {name}")

print(f"\nFile location: {su.__file__}")