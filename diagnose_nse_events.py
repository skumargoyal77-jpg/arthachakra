"""
Shows the RAW NSE response for TCS - both endpoints - so we can
see exactly what fields look like, including what the literal "-"
values actually mean in context, and whether real events are being
silently dropped by our date-fallback logic.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent if (Path(__file__).parent / "test").exists() else Path(__file__).parent))

from datetime import date, timedelta
from corporate_events.nse_client import NSEClient

client = NSEClient()
today = date.today()
to_date = today + timedelta(days=14)

print("=" * 70)
print("RAW event-calendar response (results/board meetings):")
print("=" * 70)
try:
    raw_cal = client.get_event_calendar(today, to_date)
    print(f"Total items: {len(raw_cal)}")
    hdfc_items = [i for i in raw_cal if i.get("symbol", "").upper() == "TCS"]
    print(f"TCS items: {len(hdfc_items)}")
    for item in hdfc_items[:5]:
        print(item)
except Exception as e:
    print(f"FAILED: {e}")

print()
print("=" * 70)
print("RAW corporate-actions response for TCS:")
print("=" * 70)
try:
    raw_actions = client.get_corporate_actions("TCS")
    print(f"Total items: {len(raw_actions)}")
    for item in raw_actions[:10]:
        print(item)
except Exception as e:
    print(f"FAILED: {e}")