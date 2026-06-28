"""
kite_oauth/kite_connect_flow.py
──────────────────────────────────
Real Kite Connect OAuth mechanics — per-user credentials, manual
copy-paste token flow.

ARCHITECTURE — EACH USER BRINGS THEIR OWN KITE CONNECT APP:
  ArthaChakra serves a small group of individual traders, not the
  public — each person realistically has (or will register) their OWN
  personal Kite Connect subscription at developers.kite.trade, with
  their OWN api_key/api_secret tied to their OWN Zerodha account. This
  is the common, affordable setup for individual algo traders, and it
  means one person's credentials can never be used to authenticate a
  different person's account. There is no platform-wide api_key here —
  every call in this module takes api_key/api_secret as parameters,
  supplied by whichever user is connecting their own account.

WHY MANUAL COPY-PASTE INSTEAD OF AN AUTOMATIC REDIRECT CAPTURE:
  Streamlit's session_state is commonly lost when the browser fully
  navigates away to an external site and back (a well-documented
  community limitation). Opening Zerodha's login in a NEW TAB instead
  means the original ArthaChakra tab never navigates away at all — its
  session_state is never at risk. The user logs in on the new tab,
  copies the request_token (or the whole redirected URL — see
  extract_request_token below) from its address bar, and pastes it
  back into the original tab to complete the connection. This is the
  same manual pattern used for POC1's local Kite session refresh.

PROJECT PATH:  kite_oauth/kite_connect_flow.py
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from core.logging_config import setup_logging

logger = setup_logging(__name__)

KITE_LOGIN_BASE = "https://kite.zerodha.com/connect/login"


def build_login_url(api_key: str) -> str:
    """
    Build the URL a user visits to log into THEIR OWN Zerodha account
    through THEIR OWN registered Kite Connect app. Meant to be opened
    in a new browser tab — see module docstring.
    """
    return f"{KITE_LOGIN_BASE}?api_key={api_key}&v=3"


def extract_request_token(pasted: str) -> str:
    """
    Accepts either a bare request_token string OR the full redirected
    URL (whatever the user actually copied from the address bar) and
    returns just the token. Makes the paste box forgiving about exactly
    what gets copied.
    """
    pasted = pasted.strip()
    if "request_token=" in pasted:
        parsed = urlparse(pasted)
        qs = parse_qs(parsed.query)
        if "request_token" in qs and qs["request_token"]:
            return qs["request_token"][0]
    return pasted  # assume they pasted just the bare token


def exchange_request_token_strict(api_key: str, api_secret: str, request_token: str) -> dict:
    """
    Exchange a request_token for a real access_token. Deliberately does
    NOT fall back to a mock token on failure — raises instead. This is
    used by the real per-user "Connect & Verify" flow, where silently
    mocking would hide a genuine problem (wrong API key/secret, an
    expired or already-used request_token) from the person trying to
    fix their own input.

    Returns: {"access_token": str, "expiry": "YYYY-MM-DD"}
    Raises: any exception from the kiteconnect SDK, or ImportError if
            it isn't installed — callers should catch and surface this.
    """
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    logger.info("Kite OAuth: live token exchange succeeded")
    return {
        "access_token": session_data["access_token"],
        # Kite tokens expire ~6am the day AFTER login, not at the end of
        # the login day itself - store that actual expiry date, not the
        # login date. (Found via a real-world case where is_token_valid
        # said "valid" a day later than Zerodha actually honored it.)
        "expiry": (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"),
    }


def verify_connection(api_key: str, access_token: str) -> dict:
    """
    Confirms an access_token actually works by calling Kite's
    lightweight profile() endpoint. Returns the Zerodha profile dict
    (user_name, user_id, email, etc.) on success.

    Mock tokens (starting with "mock_tok_") skip the real call and
    return a placeholder — consistent with this project's mock-mode
    philosophy elsewhere. Real tokens that fail verification raise,
    they do NOT fall back to mock — verification should tell the truth.
    """
    if access_token.startswith("mock_tok_"):
        return {"user_name": "Mock User", "user_id": "MOCK", "mock": True}

    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    profile = kite.profile()
    profile["mock"] = False
    logger.info("Kite OAuth: connection verified for %s", profile.get("user_name", "?"))
    return profile
