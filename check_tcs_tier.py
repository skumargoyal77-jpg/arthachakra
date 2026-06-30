"""Checks WHY TCS shows Red despite an 80.3% win rate - confirms
whether this is the recency-filter override (intended behavior) or
a real bug."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.stock_analysis import compute_stock_analysis, records_from_dicts
from dashboard.stock_universe import get_ohlc_for_analysis

db = Database()
raw = get_ohlc_for_analysis(db, "TCS")
records = records_from_dicts(raw)
a = compute_stock_analysis("TCS", records, target=10)

print(f"TCS: {a.total_months} months, Win%={a.target_win_pct}, Tier={a.tier}")
print(f"\nLast 3 months (most recent first):")
for m in a.last_3_months:
    print(f"  {m['month_key']}: {m['status']}  (OH%={m['oh_pct']}, OL%={m['ol_pct']})")

print(f"\nrecent_2_both_loose = {a.recent_2_both_loose}")
print("\nIf both of the last 2 months show 'Loose', that's the recency")
print("override firing (deliberate: recent deterioration overrides a")
print("good long-term average) - not a bug.")