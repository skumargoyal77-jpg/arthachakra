"""
scripts/promote_admin.py
────────────────────────────
One-time CLI to promote a user to admin. Run this locally against your
real MongoDB to bootstrap the first admin — there's deliberately no way
to self-promote via signup (security: a new account can never grant
itself admin rights).

Once at least one admin exists, that admin can promote/demote other
users from within the app's Admin Panel — this script is really only
needed to create the very first one.

Run:
    python scripts/promote_admin.py --username sandeep
    python scripts/promote_admin.py --username sandeep --revoke   (to demote)

PROJECT PATH:  scripts/promote_admin.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from users.user_repository import get_user_by_username


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote/demote an admin user")
    parser.add_argument("--username", required=True, help="Username to promote")
    parser.add_argument("--revoke", action="store_true", help="Remove admin rights instead")
    args = parser.parse_args()

    db = Database()
    if db.is_mock:
        print("⚠️  MongoDB not reachable — nothing to update. Check ARTHACHAKRA_MONGO_URI.")
        return 1

    username = args.username.strip().lower()
    user = get_user_by_username(db, username)
    if not user:
        print(f"❌ No user found with username '{username}'.")
        return 1

    new_status = not args.revoke
    db.users.update_one(
        {"user_id": user.user_id},
        {"$set": {"is_admin": new_status}},
    )

    action = "promoted to admin" if new_status else "demoted from admin"
    print(f"✅ '{username}' ({user.display_name}) has been {action}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
