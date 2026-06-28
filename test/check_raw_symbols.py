"""Shows the RAW Kite tradingsymbol for every open position - need
this to confirm whether equity stock options genuinely expire on a
different day than indices, or if something else is going on."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.user_repository import get_user_by_username
from users.session_builder import build_user_session
from brokers.kite_client import fetch_positions

db = Database()
user = get_user_by_username(db, "sandykgoyal")
session = build_user_session(db, user.user_id, user.display_name)

for conn in session.active_connections:
    print(f"\nConnection: {conn.label}")
    positions = fetch_positions(conn)
    for p in positions:
        print(f"  {p.get('tradingsymbol')!r}  qty={p.get('quantity')}  avg={p.get('average_price')}")