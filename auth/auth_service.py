"""
auth/auth_service.py
───────────────────────
Sign-up and login — the real login gate for ArthaChakra.

LAYERING:
  This is the ONLY place password hashing/verification happens. It
  constructs a fully-formed User (with password_hash + salt already
  computed) and hands it to users/user_repository.create_user() for
  persistence — exactly the separation users/user_repository.py's
  docstring calls for. The data layer (Step 1) doesn't know what a
  password is; this layer doesn't know what a MongoDB index is.

PASSWORD HASHING:
  Uses stdlib hashlib.pbkdf2_hmac with a random per-user salt — proven
  in the earlier onboarding spike, carried over unchanged. For
  production at scale, swap to bcrypt/argon2 — only this file changes.

PROJECT PATH:  auth/auth_service.py
"""

from __future__ import annotations

import hashlib
import secrets

from core.database import Database
from core.ids import new_id
from users.models import User
from users.user_repository import create_user, get_user_by_email, get_user_by_username


class AuthError(Exception):
    """Raised for signup/login failures with a user-facing message."""


# ── Password hashing ────────────────────────────────────────────────────

def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations=200_000
    ).hex()


def _make_salt() -> str:
    return secrets.token_hex(16)


# ── Public API ─────────────────────────────────────────────────────────

def signup(db: Database, username: str, email: str, password: str,
           display_name: str = "") -> User:
    """
    Create a new user. Raises AuthError if username/email already exists
    or inputs are invalid.
    """
    username = username.strip().lower()
    email    = email.strip().lower()

    if len(username) < 3:
        raise AuthError("Username must be at least 3 characters.")
    if len(password) < 6:
        raise AuthError("Password must be at least 6 characters.")
    if "@" not in email:
        raise AuthError("Please enter a valid email address.")

    if get_user_by_username(db, username):
        raise AuthError(f"Username '{username}' is already taken.")
    if get_user_by_email(db, email):
        raise AuthError(f"An account with email '{email}' already exists.")

    salt = _make_salt()
    user = User(
        user_id       = new_id("usr"),
        username      = username,
        email         = email,
        password_hash = _hash_password(password, salt),
        salt          = salt,
        display_name  = display_name or username,
    )
    create_user(db, user)
    return user


def login(db: Database, username: str, password: str) -> User:
    """
    Verify credentials and return the User. Raises AuthError on failure.
    """
    username = username.strip().lower()
    user = get_user_by_username(db, username)
    if not user:
        raise AuthError("Invalid username or password.")
    if not user.active:
        raise AuthError("This account has been deactivated.")

    expected = _hash_password(password, user.salt)
    if expected != user.password_hash:
        raise AuthError("Invalid username or password.")

    return user
