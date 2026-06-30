"""
Sanity check: run the real win-rate analysis against the just-backfilled
real data, before opening the actual Streamlit page - confirms the
tiers make sense with a clear script output first.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.stock_analysis import compute_stock_analysis, records_from_dicts, rank_and_filter
from dashboard.stock_universe import get_all_symbols, get_ohlc_for_analysis, get_universe_stats

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

stats = get_universe_stats(db)
print(f"Universe stats: {stats}\n")

symbols = get_all_symbols(db)
print(f"Symbols in universe: {symbols}\n")

analyses = []
for sym in symbols:
    raw = get_ohlc_for_analysis(db, sym)
    records = records_from_dicts(raw)
    if records:
        a = compute_stock_analysis(sym, records, target=10)
        analyses.append(a)
        print(f"  {sym:<14} {a.total_months} months  Win%={a.target_win_pct:>5.1f}%  Tier={a.tier}")

print()
ranked = rank_and_filter(analyses, min_win_pct=60, include_red=False)
print(f"Qualified (>=60% win rate, excluding Red): {len(ranked)}")
for a in ranked:
    print(f"  {a.symbol:<14} {a.tier:<8} {a.target_win_pct:.0f}%")