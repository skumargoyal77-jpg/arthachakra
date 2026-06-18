"""
users/user_repository.py
────────────────────────────
Pure persistence for the `users` collection — create, read, deactivate.

DELIBERATELY NO PASSWORD HASHING OR LOGIN LOGIC HERE.
That belongs to the auth layer (Step 2), which will construct a fully
-formed User object (with password_hash + salt already computed) and
hand it to create_user() below. This keeps the data layer and the
auth/security layer cleanly separated.

PROJECT PATH:  users/user_repository.py
"""

from __future__ import annotations

from typing import Optional

from core.database import Database
from users.models import User


def create_user(db: Database, user: User) -> None:
    """Persist a new user. Raises if username/email unique index is violated."""
    db.users.insert_one(user.to_dict())


def get_user_by_id(db: Database, user_id: str) -> Optional[User]:
    doc = db.users.find_one({"user_id": user_id})
    return User.from_dict(doc) if doc else None


def get_user_by_username(db: Database, username: str) -> Optional[User]:
    doc = db.users.find_one({"username": username.strip().lower()})
    return User.from_dict(doc) if doc else None


def get_user_by_email(db: Database, email: str) -> Optional[User]:
    doc = db.users.find_one({"email": email.strip().lower()})
    return User.from_dict(doc) if doc else None


def list_users(db: Database, active_only: bool = True) -> list[User]:
    filt = {"active": True} if active_only else {}
    return [User.from_dict(d) for d in db.users.find(filt)]


def deactivate_user(db: Database, user_id: str) -> bool:
    existing = db.users.find_one({"user_id": user_id})
    if not existing:
        return False
    db.users.update_one({"user_id": user_id}, {"$set": {"active": False}})
    return True
