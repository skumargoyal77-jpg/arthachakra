"""
Sanity check before opening the UI: runs the EXACT same load_all_analyses
logic the Stock Scanner page uses, against the real full 216-symbol
universe, and times it - the page does this synchronously on load, so
slow performance here means a slow/laggy page experience.
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.stock_analysis import compute_stock_analysis, rank_and_filter, records_from_dicts
from dashboard.stock_universe import get_all_symbols, get_ohlc_for_analysis, get_universe_stats

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

stats = get_universe_stats(db)
print(f"Universe stats: {stats}\n")

symbols = get_all_symbols(db)
print(f"Total symbols: {len(symbols)}")

start = time.perf_counter()
analyses = []
skipped = []
for sym in symbols:
    raw = get_ohlc_for_analysis(db, sym)
    records = records_from_dicts(raw)
    if records:
        analyses.append(compute_stock_analysis(sym, records, target=10))
    else:
        skipped.append(sym)
elapsed = time.perf_counter() - start

print(f"\nLoaded {len(analyses)} analyses in {elapsed:.2f}s")
if skipped:
    print(f"⚠️  {len(skipped)} symbol(s) had NO usable OHLC data at all: {skipped}")

ranked = rank_and_filter(analyses, min_win_pct=60, include_red=False)
print(f"\nQualified (>=60% win, excluding Red): {len(ranked)}")

from collections import Counter
tier_counts = Counter(a.tier for a in analyses)
print(f"\nFull tier distribution across all {len(analyses)} analyzed symbols:")
for tier in ["Violet", "Green", "Yellow", "Red"]:
    print(f"  {tier}: {tier_counts.get(tier, 0)}")

print(f"\n{'='*60}")
if elapsed > 5:
    print(f"⚠️  {elapsed:.1f}s is on the slower side - the Selector tab page")
    print(f"    load may feel sluggish (it's cached 5 min, so only the")
    print(f"    FIRST load per session/setting-change feels this slow).")
else:
    print(f"✅ {elapsed:.2f}s is fast - the Selector tab should load quickly.")