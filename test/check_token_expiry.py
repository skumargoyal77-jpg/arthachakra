"""Shows the RAW stored token_expiry for every connection, and what
is_token_valid actually computes from it - to see why a week-old
token was treated as valid."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from datetime import datetime
from core.database import Database
from users.user_repository import list_users
from kite_oauth.connection_service import list_connections

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")
print(f"Today's date (as Python sees it): {datetime.now().date()}\n")

for user in list_users(db, active_only=True):
    for c in list_connections(db=db, user_id=user.user_id):
        if c.access_token and c.access_token.startswith("mock_tok_"):
            continue
        print(f"User: {user.display_name}  Connection: '{c.label}'")
        print(f"  stored token_expiry (raw)  : {c.token_expiry!r}")
        print(f"  is_token_valid computes to : {c.is_token_valid}")
        print()
