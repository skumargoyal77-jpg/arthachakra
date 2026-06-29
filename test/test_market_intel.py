"""
Tests market_intel/ against REAL Tavily data - confirms TAVILY_API_KEY
is actually being picked up, shows real search results, real
sentiment classification, and S-25/M-11/M-12's real verdicts.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from market_intel.intel_scanner import IntelScanner
from market_intel.tavily_search import TavilySearch
from rules.engine import RuleEngine
from rules.seed_rules import get_rule_book

SYMBOLS_TO_CHECK = ["HDFCBANK", "TCS", "RELIANCE"]

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

searcher = TavilySearch()
print(f"Tavily mode: {'LIVE (real API key found)' if searcher.is_live else 'MOCK (no key found - check .env)'}\n")
if not searcher.is_live:
    print("Stop here - TAVILY_API_KEY isn't being picked up. Check .env is in the")
    print("project root (not the test/ folder) and the key has no extra quotes/spaces.")
    sys.exit(1)

print("=" * 70)
scanner = IntelScanner(db)
engine = RuleEngine()
book = {r["rule_id"]: r for r in get_rule_book()}

for symbol in SYMBOLS_TO_CHECK:
    print(f"\n--- {symbol} ---")
    summary = scanner.scan_symbol(symbol, force=True)   # force=True skips the 60-min cache

    print(f"  Signals found: {len(summary.signals)}  "
          f"(bullish={summary.bullish_count} bearish={summary.bearish_count} neutral={summary.neutral_count})")
    for s in summary.signals[:5]:
        print(f"    [{s.sentiment.value}] {s.title[:70]}  ({s.source})")

    print(f"  Overall action: {summary.action}")

    intel_ctx = {
        "is_blocking": summary.is_blocking,
        "bearish_count": summary.bearish_count,
        "bullish_count": summary.bullish_count,
        "action": summary.action,
    }
    for rule_id in ["S-25", "M-11"]:
        result = engine.evaluate_rule(book[rule_id], None, {"market_intel": intel_ctx})
        icon = {"FAIL": "🔴", "WARN": "🟡", "PASS": "✅"}.get(result.status, "")
        print(f"  {rule_id}: {icon} {result.status} — {result.message}")

print("\n" + "=" * 70)
print("DONE")