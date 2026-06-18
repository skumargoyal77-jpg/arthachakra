"""users/__init__.py — public exports for the users package."""

from users.models import (
    User, BrokerConnection, RuleDefinition, UserRuleState,
    TelegramConfig, UserSession,
)
from users.schema import COLLECTION_SCHEMA, shared_collections, per_user_collections
from users.user_repository import (
    create_user, get_user_by_id, get_user_by_username,
    get_user_by_email, list_users, deactivate_user,
)
from users.session_builder import build_user_session

__all__ = [
    "User", "BrokerConnection", "RuleDefinition", "UserRuleState",
    "TelegramConfig", "UserSession",
    "COLLECTION_SCHEMA", "shared_collections", "per_user_collections",
    "create_user", "get_user_by_id", "get_user_by_username",
    "get_user_by_email", "list_users", "deactivate_user",
    "build_user_session",
]
