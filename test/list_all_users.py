"""
Read-only - lists every user account and every connection they own.
Run this FIRST so we can see the full picture before deleting anything.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.user_repository import list_users
from kite_oauth.connection_service import list_connections

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")
print("=" * 70)

all_users = list_users(db, active_only=False)  # show even deactivated ones
for user in all_users:
    print(f"\nuser_id      : {user.user_id}")
    print(f"username     : {user.username}")
    print(f"display_name : {user.display_name}")
    print(f"email        : {user.email}")
    print(f"active       : {user.active}")

    conns = list_connections(db=db, user_id=user.user_id, active_only=False)
    if not conns:
        print("  (no broker connections)")
    for c in conns:
        print(f"  -> connection: '{c.label}'  broker={c.broker}  "
              f"active={c.active}  token_expiry={c.token_expiry}  "
              f"is_token_valid={c.is_token_valid}")
    print("-" * 70)

print(f"\nTotal users: {len(all_users)}")