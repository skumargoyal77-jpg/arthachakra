"""
Tests real VIX fetching — finds your first user with a real (non-mock)
Kite connection, fetches live India VIX through it, and caches it.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from core.database import Database
from users.user_repository import list_users
from kite_oauth.connection_service import list_connections
from market_data.vix_fetcher import fetch_and_cache_vix, get_latest_vix, fetch_live_vix

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

real_conn = None
owner = None

for user in list_users(db, active_only=True):
    conns = list_connections(db=db, user_id=user.user_id)
    for c in conns:
        if c.access_token and not c.access_token.startswith("mock_tok_"):
            if c.is_token_valid:
                real_conn = c
                owner = user
                break
    if real_conn:
        break

if real_conn is None:
    print("No user has a real, currently-valid Kite connection.")
    print("Either your token has expired (reconnect via the dashboard's")
    print("'Reconnect' button) or you only have mock connections set up.")
    raise SystemExit(1)

print(f"Using connection: '{real_conn.label}' (user: {owner.display_name})\n")

print("Fetching live India VIX via kite.quote()...")
vix = fetch_live_vix(real_conn)
print(f"  -> Raw fetch result: {vix}")

if vix is None:
    print("\nGot None back - check the console output above this line for")
    print("a WARNING log with the actual exception (e.g. invalid instrument")
    print("string, API rate limit, or a token issue despite is_token_valid).")
    raise SystemExit(1)

print(f"\n✅ Live VIX: {vix}")

cached = fetch_and_cache_vix(db, real_conn)
print(f"Cached via fetch_and_cache_vix: {cached}")

latest = get_latest_vix(db)
print(f"\nLatest reading in vix_history: {latest}")
print(f"\n{'='*60}")
print("DONE - VIX fetching confirmed working against live Kite data.")
print(f"{'='*60}")
