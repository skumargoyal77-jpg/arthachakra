"""auth/__init__.py — public exports for authentication."""

from auth.auth_service import signup, login, AuthError

__all__ = ["signup", "login", "AuthError"]
