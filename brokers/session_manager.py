"""
brokers/session_manager.py
───────────────────────────────
Checks token validity across every user's broker connections.

IMPORTANT — THIS DOES NOT "REFRESH" ANYTHING:
  Kite Connect has no programmatic token refresh. Every access token
  expires daily (~6:30 AM IST) and re-authentication is ALWAYS
  interactive — a human has to actually log into Zerodha (password +
  TOTP/PIN) in a browser. There is no API call, cron job, or service
  account that can silently extend a token. This is the same
  constraint that already shaped kite_oauth/ (Step 2's manual
  copy-paste flow) and connection_service.reconnect_connection()
  (Step 2.1's reconnect button) — neither pretends otherwise, and
  this module doesn't either.

  What this module actually does: loop over every user's broker
  connections, check is_token_valid (already on BrokerConnection from
  Step 1), and produce a clear report of who's expired and needs to
  manually reconnect. "Refresh" in the original Step 5 checkpoint
  wording meant this check-and-report job, not a silent renewal.

PROJECT PATH:  brokers/session_manager.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.database import Database
from core.logging_config import setup_logging
from users.models import BrokerConnection
from users.user_repository import list_users

logger = setup_logging(__name__)


@dataclass
class ConnectionStatus:
    user_id: str
    display_name: str
    connection_id: str
    label: str
    broker: str
    valid: bool
    token_expiry: str | None


@dataclass
class TokenCheckReport:
    statuses: list[ConnectionStatus] = field(default_factory=list)

    @property
    def expired(self) -> list[ConnectionStatus]:
        return [s for s in self.statuses if not s.valid]

    @property
    def valid_count(self) -> int:
        return sum(1 for s in self.statuses if s.valid)

    def summary(self) -> str:
        lines = [f"Token check: {self.valid_count}/{len(self.statuses)} connections valid."]
        for s in self.expired:
            lines.append(f"  ⚠️  EXPIRED — {s.display_name} / '{s.label}' ({s.broker}), "
                        f"was valid until {s.token_expiry}")
        return "\n".join(lines)


def check_all_user_tokens(db: Database) -> TokenCheckReport:
    """
    Checks every active user's active broker connections. Never
    touches one user's connections while checking another's — each
    user's connections are looked up independently by user_id.
    """
    report = TokenCheckReport()

    for user in list_users(db, active_only=True):
        docs = db.broker_connections.find({"user_id": user.user_id, "active": True})
        for doc in docs:
            conn = BrokerConnection.from_dict(doc)
            # Mock connections never expire — nothing real to check.
            if conn.access_token and conn.access_token.startswith("mock_tok_"):
                continue
            report.statuses.append(ConnectionStatus(
                user_id=user.user_id,
                display_name=user.display_name,
                connection_id=conn.connection_id,
                label=conn.label,
                broker=conn.broker,
                valid=conn.is_token_valid,
                token_expiry=conn.token_expiry,
            ))

    return report
