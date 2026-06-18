"""
users/models.py
──────────────────
Core dataclasses for the multi-tenant pattern. Validated previously as
a standalone spike; this is the permanent home for this logic.

  User              — login identity
  BrokerConnection  — one or more Kite/Dhan accounts per user
  UserRuleState     — which default rules are ON/OFF + custom rules per user
  TelegramConfig    — per-user Telegram chat binding
  UserSession       — the assembled runtime object every component reads from

PROJECT PATH:  users/models.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.ids import new_id, now_utc


# ── User ─────────────────────────────────────────────────────────────────

@dataclass
class User:
    user_id:       str
    username:      str
    email:         str
    password_hash: str
    salt:          str
    display_name:  str = ""
    created_at:    datetime = field(default_factory=now_utc)
    active:        bool = True

    def to_dict(self) -> dict:
        return {
            "user_id":       self.user_id,
            "username":      self.username,
            "email":         self.email,
            "password_hash": self.password_hash,
            "salt":          self.salt,
            "display_name":  self.display_name or self.username,
            "created_at":    self.created_at,
            "active":        self.active,
        }

    @staticmethod
    def from_dict(d: dict) -> "User":
        return User(
            user_id       = d["user_id"],
            username      = d["username"],
            email         = d["email"],
            password_hash = d["password_hash"],
            salt          = d["salt"],
            display_name  = d.get("display_name", ""),
            created_at    = d.get("created_at", now_utc()),
            active        = d.get("active", True),
        )


# ── Broker Connection (multiple allowed per user) ──────────────────────────

@dataclass
class BrokerConnection:
    connection_id: str
    user_id:       str
    broker:        str             # "kite" | "dhan" | "mock"
    label:         str              # e.g. "Sandeep Index Account"
    api_key:       str
    api_secret:    str = ""         # encrypt in production — plain here for Step 1/2
    access_token:  Optional[str] = None
    token_expiry:  Optional[str] = None   # "YYYY-MM-DD"
    account_type:  str = "equity"   # "equity" | "index" | "both"
    broker_account_name: str = ""   # the real name on the broker's own profile (e.g. Zerodha)
    active:        bool = True
    created_at:    datetime = field(default_factory=now_utc)

    @property
    def is_token_valid(self) -> bool:
        if not self.access_token or not self.token_expiry:
            return False
        try:
            exp = datetime.strptime(self.token_expiry, "%Y-%m-%d").date()
            return exp >= datetime.now().date()
        except ValueError:
            return False

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "user_id":       self.user_id,
            "broker":        self.broker,
            "label":         self.label,
            "api_key":       self.api_key,
            "api_secret":    self.api_secret,
            "access_token":  self.access_token,
            "token_expiry":  self.token_expiry,
            "account_type":  self.account_type,
            "broker_account_name": self.broker_account_name,
            "active":        self.active,
            "created_at":    self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "BrokerConnection":
        d = {k: v for k, v in d.items() if k != "_id"}
        d.setdefault("broker_account_name", "")
        return BrokerConnection(**d)


# ── Rule definitions (platform / default / custom) ─────────────────────────

@dataclass
class RuleDefinition:
    """A single platform-defined rule, as seeded in platform_rules/default_rules."""
    rule_id:       str
    name:          str
    description:   str
    category:      str             # "MANDATORY" | "OPTIONAL"
    group:         str = "General" # e.g. "Selection", "Exit", "Capital"
    default_on:    bool = True


@dataclass
class UserRuleState:
    """
    Per-user toggle state for one default rule, or a fully custom rule.

    For default/mandatory rules: rule_id matches a RuleDefinition, custom_def=None.
    For custom rules: rule_id is a generated id, custom_def holds the built condition.
    """
    user_id:     str
    rule_id:     str
    enabled:     bool
    source:      str               # "default" | "custom"
    custom_def:  Optional[dict] = None   # {name, metric, operator, value, action}

    def to_dict(self) -> dict:
        return {
            "user_id":    self.user_id,
            "rule_id":    self.rule_id,
            "enabled":    self.enabled,
            "source":     self.source,
            "custom_def": self.custom_def,
        }

    @staticmethod
    def from_dict(d: dict) -> "UserRuleState":
        return UserRuleState(**{k: v for k, v in d.items() if k != "_id"})


# ── Telegram config ─────────────────────────────────────────────────────

@dataclass
class TelegramConfig:
    user_id:      str
    chat_id:      Optional[str] = None
    link_code:    Optional[str] = None
    code_expiry:  Optional[datetime] = None
    verified:     bool = False

    def to_dict(self) -> dict:
        return {
            "user_id":     self.user_id,
            "chat_id":     self.chat_id,
            "link_code":   self.link_code,
            "code_expiry": self.code_expiry,
            "verified":    self.verified,
        }

    @staticmethod
    def from_dict(d: dict) -> "TelegramConfig":
        return TelegramConfig(**{k: v for k, v in d.items() if k != "_id"})


# ── The assembled runtime session ───────────────────────────────────────

@dataclass
class UserSession:
    """
    The single object every downstream component (agent, dashboard,
    strangle scanner, Telegram sender) reads from. Built fresh per
    request by session_builder.build_user_session().
    """
    user_id:            str
    display_name:       str
    broker_connections: list[BrokerConnection] = field(default_factory=list)
    effective_rules:    list[dict] = field(default_factory=list)
    telegram_chat_id:   Optional[str] = None
    telegram_verified:  bool = False

    @property
    def active_connections(self) -> list[BrokerConnection]:
        return [c for c in self.broker_connections if c.active]

    @property
    def mandatory_rule_count(self) -> int:
        return sum(1 for r in self.effective_rules if r.get("category") == "MANDATORY")

    @property
    def optional_enabled_count(self) -> int:
        return sum(1 for r in self.effective_rules
                   if r.get("category") == "OPTIONAL" and r.get("enabled"))

    @property
    def custom_rule_count(self) -> int:
        return sum(1 for r in self.effective_rules if r.get("source") == "custom")

    def summary(self) -> str:
        lines = [
            f"UserSession for {self.display_name} ({self.user_id})",
            f"  Broker connections : {len(self.active_connections)} active "
            f"({', '.join(c.label for c in self.active_connections) or 'none'})",
            f"  Rules              : {self.mandatory_rule_count} mandatory + "
            f"{self.optional_enabled_count} optional ON + {self.custom_rule_count} custom",
            f"  Telegram           : "
            f"{'✅ ' + self.telegram_chat_id if self.telegram_verified else '⚠️  not connected'}",
        ]
        return "\n".join(lines)