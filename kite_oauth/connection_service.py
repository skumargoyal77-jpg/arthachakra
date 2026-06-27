"""
kite_oauth/connection_service.py
───────────────────────────────────
Manages broker_connections — add, list, deactivate, rename. Each
connection stores the api_key/api_secret of the USER who owns it (their
own personal Kite Connect subscription — see kite_connect_flow.py
docstring), never a platform-wide credential.

LAYERING:
  add_connection() is pure persistence — no network calls, just saves
  an already-obtained, already-verified token. connect_real_account()
  is the orchestrator the UI actually calls for a real connection: it
  extracts the token from whatever was pasted, exchanges it, verifies
  it works, and only then calls add_connection(). Nothing is ever saved
  unless verification against the real Kite API actually succeeds.

PROJECT PATH:  kite_oauth/connection_service.py
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

from core.database import Database
from core.ids import new_id
from kite_oauth.kite_connect_flow import (
    exchange_request_token_strict, extract_request_token, verify_connection,
)
from users.models import BrokerConnection


def add_connection(
    db:            Database,
    user_id:       str,
    label:         str,
    api_key:       str,
    api_secret:    str,
    access_token:  str,
    token_expiry:  str,
    account_type:  str = "both",
    broker:        str = "kite",
    broker_account_name: str = "",
) -> BrokerConnection:
    """
    Persist an already-obtained, already-verified connection. Pure
    persistence — no network calls. See connect_real_account() for the
    full real-connection flow, or add_mock_connection() for testing.
    """
    conn = BrokerConnection(
        connection_id = new_id("conn"),
        user_id       = user_id,
        broker        = broker,
        label         = label,
        api_key       = api_key,
        api_secret    = api_secret,
        access_token  = access_token,
        token_expiry  = token_expiry,
        account_type  = account_type,
        broker_account_name = broker_account_name,
        active        = True,
    )
    db.broker_connections.insert_one(conn.to_dict())
    return conn


def connect_real_account(
    db:           Database,
    user_id:      str,
    label:        str,
    api_key:      str,
    api_secret:   str,
    pasted_token: str,
    account_type: str = "both",
) -> tuple[BrokerConnection, dict]:
    """
    The full real-connection flow used by the "Connect & Verify" UI:
      1. Extract the request_token from whatever was pasted (bare
         token or full redirected URL)
      2. Exchange it for an access_token using THIS user's own
         api_key/api_secret
      3. Verify the access_token actually works against the real Kite API
      4. Only on full success, persist the connection

    Raises RuntimeError with a clear, specific message at whichever
    step failed — nothing is saved on any failure.
    """
    request_token = extract_request_token(pasted_token)
    if not request_token:
        raise RuntimeError("Could not find a request_token in what you pasted.")

    try:
        token_data = exchange_request_token_strict(api_key, api_secret, request_token)
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e

    try:
        profile = verify_connection(api_key, token_data["access_token"])
    except Exception as e:
        raise RuntimeError(f"Got a token, but verification failed: {e}") from e

    conn = add_connection(
        db, user_id, label=label, api_key=api_key, api_secret=api_secret,
        access_token=token_data["access_token"], token_expiry=token_data["expiry"],
        account_type=account_type, broker_account_name=profile.get("user_name", ""),
    )
    return conn, profile


def reconnect_connection(
    db: Database, user_id: str, connection_id: str, pasted_token: str,
) -> tuple[BrokerConnection, dict]:
    """
    Re-establish an EXISTING connection whose access_token has expired
    (Kite tokens expire daily, ~6am). Reuses the api_key/api_secret
    already stored on this connection — the user doesn't re-enter them.
    Updates the SAME connection_id row; never creates a duplicate.

    Raises RuntimeError with a clear message on failure — the old
    (expired) token is left untouched if re-verification fails.
    """
    existing = db.broker_connections.find_one(
        {"user_id": user_id, "connection_id": connection_id}
    )
    if not existing:
        raise RuntimeError("Connection not found.")

    request_token = extract_request_token(pasted_token)
    if not request_token:
        raise RuntimeError("Could not find a request_token in what you pasted.")

    try:
        token_data = exchange_request_token_strict(
            existing["api_key"], existing["api_secret"], request_token,
        )
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e

    try:
        profile = verify_connection(existing["api_key"], token_data["access_token"])
    except Exception as e:
        raise RuntimeError(f"Got a token, but verification failed: {e}") from e

    db.broker_connections.update_one(
        {"user_id": user_id, "connection_id": connection_id},
        {"$set": {
            "access_token": token_data["access_token"],
            "token_expiry": token_data["expiry"],
            "broker_account_name": profile.get("user_name", existing.get("broker_account_name", "")),
            "active": True,
        }},
    )
    refreshed = db.broker_connections.find_one(
        {"user_id": user_id, "connection_id": connection_id}
    )
    return BrokerConnection.from_dict(refreshed), profile


def add_mock_connection(
    db: Database, user_id: str, label: str,
    account_type: str = "both", broker: str = "kite",
) -> BrokerConnection:
    """
    Adds a connection with a synthetic mock token — no network calls,
    no real credentials needed. Used by the "Add Mock Connection"
    testing button so the rest of the flow can be exercised without a
    real Kite Connect app.
    """
    mock_token = f"mock_tok_{secrets.token_hex(8)}"
    expiry = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return add_connection(
        db, user_id, label=label, api_key="mock_key", api_secret="mock_secret",
        access_token=mock_token, token_expiry=expiry,
        account_type=account_type, broker=broker, broker_account_name="Mock User",
    )


def list_connections(db: Database, user_id: str, active_only: bool = True) -> list[BrokerConnection]:
    """Return all broker connections for one user — never another user's."""
    filt = {"user_id": user_id}
    if active_only:
        filt["active"] = True
    docs = db.broker_connections.find(filt)
    return [BrokerConnection.from_dict(d) for d in docs]


def deactivate_connection(db: Database, user_id: str, connection_id: str) -> bool:
    """Soft-delete a connection (keeps history, stops using it)."""
    existing = db.broker_connections.find_one(
        {"user_id": user_id, "connection_id": connection_id}
    )
    if not existing:
        return False
    db.broker_connections.update_one(
        {"user_id": user_id, "connection_id": connection_id},
        {"$set": {"active": False}},
    )
    return True


def update_connection(
    db: Database, user_id: str, connection_id: str,
    label: str | None = None, account_type: str | None = None,
) -> bool:
    """Rename a connection's label and/or change its account_type."""
    existing = db.broker_connections.find_one(
        {"user_id": user_id, "connection_id": connection_id}
    )
    if not existing:
        return False

    updates: dict = {}
    if label is not None and label.strip():
        updates["label"] = label.strip()
    if account_type is not None:
        updates["account_type"] = account_type

    if updates:
        db.broker_connections.update_one(
            {"user_id": user_id, "connection_id": connection_id},
            {"$set": updates},
        )
    return True
