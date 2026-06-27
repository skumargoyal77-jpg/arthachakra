"""
Deactivates the stale 'sandeep' login and its two placeholder
connections. Does NOT touch 'sandykgoyal' or 'friend1' - only the
exact user_id confirmed via list_all_users.py output.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.user_repository import deactivate_user, get_user_by_id
from kite_oauth.connection_service import list_connections, deactivate_connection

SANDEEP_USER_ID = "usr_dbfcd5e2dc"  # confirmed from list_all_users.py output

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

user = get_user_by_id(db, SANDEEP_USER_ID)
if user is None:
    print(f"No user found with user_id={SANDEEP_USER_ID} - nothing to do.")
    raise SystemExit(1)

print(f"Target: username={user.username!r}  display_name={user.display_name!r}  email={user.email!r}")
print()

conns = list_connections(db=db, user_id=SANDEEP_USER_ID, active_only=True)
print(f"Deactivating {len(conns)} connection(s):")
for c in conns:
    ok = deactivate_connection(db, SANDEEP_USER_ID, c.connection_id)
    print(f"  -> '{c.label}': {'done' if ok else 'FAILED'}")

print()
ok = deactivate_user(db, SANDEEP_USER_ID)
print(f"Deactivating user account: {'done' if ok else 'FAILED'}")

print(f"\n{'='*60}")
print("Cleanup complete. 'sandeep' login is now deactivated and won't")
print("appear in future test scripts or be usable to log into the app.")
print(f"{'='*60}")