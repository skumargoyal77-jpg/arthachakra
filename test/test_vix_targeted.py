"""
Same VIX test, but targets a SPECIFIC connection by label instead of
"the first valid-looking one found" - avoids picking up a stale
connection that belongs to a different ArthaChakra login.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.user_repository import list_users
from kite_oauth.connection_service import list_connections
from market_data.vix_fetcher import fetch_and_cache_vix, get_latest_vix, fetch_live_vix

TARGET_LABEL = "SandeepKIte"  # change this if your label is different

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

found = None
owner = None
for user in list_users(db, active_only=True):
    for c in list_connections(db=db, user_id=user.user_id):
        print(f"  (scanning) user={user.display_name!r}  label={c.label!r}  valid={c.is_token_valid}")
        if c.label == TARGET_LABEL:
            found = c
            owner = user

print()
if found is None:
    print(f"No connection found with label '{TARGET_LABEL}' under ANY user.")
    print("Check the scan list above for the exact label/user it actually belongs to.")
    raise SystemExit(1)

print(f"Targeting: '{found.label}' (user: {owner.display_name}, valid: {found.is_token_valid})\n")

print("Fetching live India VIX via kite.quote()...")
vix = fetch_live_vix(found)
print(f"  -> Raw fetch result: {vix}")

if vix is None:
    print("\nGot None back - check the WARNING log line above for the real exception.")
    raise SystemExit(1)

print(f"\n✅ Live VIX: {vix}")
cached = fetch_and_cache_vix(db, found)
latest = get_latest_vix(db)
print(f"Cached: {cached}")
print(f"Latest in vix_history: {latest}")
print(f"\n{'='*60}")
print("DONE")
print(f"{'='*60}")