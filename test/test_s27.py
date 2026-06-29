"""
Tests S-27 (no entry if results fall before monthly expiry) against
REAL NSE data. Checks results against a TARGET series' expiry, while
always using REAL today as the lookback start (so we never miss
something that's already happened, or check a stale window).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from datetime import date
from core.database import Database
from corporate_events.event_calendar import EventCalendar
from rules.series_calendar import last_tuesday_of_month
from rules.engine import RuleEngine
from rules.seed_rules import get_rule_book

# Change these to check a different month or symbol list
TARGET_YEAR = 2026
TARGET_MONTH = 7   # July
SYMBOLS_TO_CHECK = ["TCS", "HDFCBANK", "SBILIFE", "RELIANCE", "INFY"]

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

today = date.today()
target_expiry = last_tuesday_of_month(TARGET_YEAR, TARGET_MONTH)

print(f"Today: {today}")
print(f"Checking against: {TARGET_YEAR}-{TARGET_MONTH:02d} series, expiry {target_expiry}  "
      f"({(target_expiry - today).days} days away)\n")
print("=" * 70)

cal = EventCalendar(db)
engine = RuleEngine()
book = {r["rule_id"]: r for r in get_rule_book()}
s27 = book["S-27"]

for symbol in SYMBOLS_TO_CHECK:
    print(f"\n--- {symbol} ---")
    # today stays REAL today (the lookback start) - only the expiry
    # target moves to whichever month/year you set above.
    found, event = cal.has_results_before_expiry(symbol, target_expiry, today=today)

    if found:
        print(f"  Results event: {event.description}  on {event.event_date}")
    else:
        print(f"  No results scheduled before {target_expiry}")

    result = engine.evaluate_rule(s27, None, {"results_before_expiry": event.to_dict() if event else None})
    icon = "🔴" if result.status == "FAIL" else "✅"
    print(f"  S-27: {icon} {result.status} — {result.message}")

print("\n" + "=" * 70)
print("DONE")