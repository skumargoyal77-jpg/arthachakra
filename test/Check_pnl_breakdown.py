"""Shows raw per-leg pnl, m2m, average_price, last_price, quantity for
every position - to compare Kite's own numbers leg-by-leg against
both our summed total and your manually expected total."""
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
    positions = fetch_positions(conn)
    print(f"\nConnection: {conn.label}  ({len(positions)} total raw positions)\n")

    by_underlying = {}
    for p in positions:
        sym = p.get("tradingsymbol", "")
        underlying = sym.split("26JUN")[0] if "26JUN" in sym else sym
        by_underlying.setdefault(underlying, []).append(p)

    for underlying, legs in sorted(by_underlying.items()):
        print(f"=== {underlying} ({len(legs)} legs) ===")
        total = 0.0
        for p in legs:
            qty = p.get("quantity", 0)
            avg = p.get("average_price", 0.0)
            ltp = p.get("last_price", 0.0)
            pnl = p.get("pnl", 0.0)
            m2m = p.get("m2m", "N/A")
            manual_calc = (avg - ltp) * abs(qty)  # standard short-position formula
            print(f"  {p.get('tradingsymbol'):28s} qty={qty:6d}  avg={avg:7.2f}  ltp={ltp:7.2f}  "
                  f"kite_pnl={pnl:10.2f}  m2m={m2m}  manual_calc={manual_calc:10.2f}")
            total += pnl
        print(f"  --> SUM of kite_pnl across all legs: {total:.2f}\n")