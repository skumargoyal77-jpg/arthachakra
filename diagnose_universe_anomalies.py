"""
Investigates two things from the full-universe load test:
1. Why nse_stocks has 505 total docs but only 216 active
2. Whether the 74% Red-tier rate is from genuine volatility or the
   recency-filter override firing too aggressively
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from dashboard.stock_analysis import compute_stock_analysis, records_from_dicts
from dashboard.stock_universe import get_ohlc_for_analysis

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

# ── Part 1: the inactive 289 ──
print("=" * 60)
print("INACTIVE STOCKS (active=False)")
print("=" * 60)
inactive = list(db.nse_stocks.find({"active": False}))
print(f"Count: {len(inactive)}")
if inactive:
    print("Sample of 10:")
    for d in inactive[:10]:
        print(f"  {d.get('symbol')}  full_name={d.get('full_name','')!r}  "
              f"is_index={d.get('is_index')}  updated_at={d.get('updated_at')}")

# ── Part 2: Red-tier breakdown ──
print("\n" + "=" * 60)
print("RED-TIER BREAKDOWN (recency override vs genuine low win-rate)")
print("=" * 60)
active_symbols = [d["symbol"] for d in db.nse_stocks.find({"active": True})]

recency_triggered = 0
genuinely_low = 0
red_examples = []

for sym in active_symbols:
    raw = get_ohlc_for_analysis(db, sym)
    records = records_from_dicts(raw)
    if not records:
        continue
    a = compute_stock_analysis(sym, records, target=10)
    if a.tier == "Red":
        if a.recent_2_both_loose and a.target_win_pct >= 60:
            recency_triggered += 1
            if len(red_examples) < 5:
                red_examples.append((sym, "recency-override", a.target_win_pct))
        else:
            genuinely_low += 1
            if len(red_examples) < 10:
                red_examples.append((sym, "genuinely low win%", a.target_win_pct))

print(f"Red via recency-override (good history, bad last 2 months): {recency_triggered}")
print(f"Red via genuinely low win-rate (<60%):                       {genuinely_low}")
print(f"\nExamples:")
for sym, reason, win_pct in red_examples:
    print(f"  {sym:<14} {reason:<22} win%={win_pct:.1f}")