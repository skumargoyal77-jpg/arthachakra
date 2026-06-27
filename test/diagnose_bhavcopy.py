"""
Diagnostic script — shows the EXACT URL being requested and the real
HTTP response, instead of the swallowed log warning.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from datetime import date
from market_data.bhavcopy import BhavcopyScraper, URL_FO, HEADERS
import requests

target = date(2026, 6, 25)  # Thursday, known trading day
url = URL_FO.format(date=target.strftime("%Y%m%d"))

print(f"Target date : {target} ({target.strftime('%A')})")
print(f"URL         : {url}")
print()
print("Step 1: priming session (visiting nseindia.com for cookies)...")

session = requests.Session()
session.headers.update(HEADERS)
try:
    priming_resp = session.get("https://www.nseindia.com", timeout=10)
    print(f"  -> Priming response: HTTP {priming_resp.status_code}")
except Exception as e:
    print(f"  -> Priming FAILED: {e}")

print()
print("Step 2: requesting the actual Bhavcopy file...")
try:
    resp = session.get(url, timeout=30)
    print(f"  -> HTTP {resp.status_code}")
    print(f"  -> Content-Type: {resp.headers.get('Content-Type')}")
    print(f"  -> Content-Length: {resp.headers.get('Content-Length')}")
    if resp.status_code != 200:
        print(f"  -> Response body (first 500 chars):")
        print(f"     {resp.text[:500]}")
except Exception as e:
    print(f"  -> Request FAILED: {type(e).__name__}: {e}")

print()
print("=" * 70)
print("If Step 2 shows HTTP 404: the URL pattern itself may be wrong for")
print("this date - try opening the URL above directly in your browser to")
print("confirm whether NSE actually has a file at that exact address.")
print("If Step 2 shows HTTP 403: NSE is blocking the request despite")
print("priming - paste this whole output back and we'll adjust headers.")
