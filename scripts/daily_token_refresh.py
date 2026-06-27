"""
scripts/daily_token_refresh.py
───────────────────────────────────
Daily job: checks every user's broker connection tokens and reports
who needs to manually reconnect.

NAME KEPT FROM THE ORIGINAL STEP 5 PLAN, BEHAVIOR CORRECTED: Kite
Connect has no programmatic refresh — see brokers/session_manager.py's
docstring for why. This script checks and reports; it cannot silently
renew anything. Once Step 10's Telegram alerts exist, this is the
natural place to push "your token expired, please reconnect" — for
now it logs and prints clearly.

Run:
    python scripts/daily_token_refresh.py

Intended to run once daily, early morning, via cron/Task Scheduler —
after ~6:30 AM IST when Zerodha invalidates the previous day's tokens.

PROJECT PATH:  scripts/daily_token_refresh.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from brokers.session_manager import check_all_user_tokens
from core.database import Database


def main() -> int:
    print("\n" + "═" * 70)
    print("  ArthaChakra — Daily Token Check")
    print("═" * 70)

    db = Database()
    report = check_all_user_tokens(db)

    print()
    print(report.summary())
    print()

    if report.expired:
        print(f"  ⚠️  {len(report.expired)} connection(s) need manual reconnection.")
        print(f"      (Telegram alerts for this land in Step 10 — for now, check")
        print(f"      the dashboard's '🔄 Reconnect' button for each one.)")
    else:
        print(f"  ✅ All {report.valid_count} connections have valid tokens.")

    print("═" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
