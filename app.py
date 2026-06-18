"""
app.py
────────
ArthaChakra — Step 2 + Step 2.1: Real Login Gate + Per-User Kite
Connect + Live Strangle Dashboard

Flow:
  1. Sign up / log in (auth/auth_service.py)
  2. Add one or more Kite connections — each using the LOGGED-IN USER'S
     OWN Kite Connect api_key/api_secret (their own personal Zerodha
     developer subscription, not a shared platform credential — see
     kite_oauth/kite_connect_flow.py docstring for why).
  3. See live positions across ALL connected accounts, grouped into
     strangles, with per-leg Black-Scholes delta and a net
     delta-neutrality status (Step 2.1 — dashboard/strangle_grouper.py,
     dashboard/greeks.py, brokers/kite_client.py).
  4. See the assembled UserSession (users/session_builder.py, built in
     Step 1) — proves login → real broker connection → session works
     end to end.

WHY THIS USES A MANUAL COPY-PASTE STEP INSTEAD OF AN AUTOMATIC REDIRECT:
  Streamlit's session_state is commonly lost when the browser fully
  navigates away to an external site and back — a well-documented
  community limitation, not specific to this app. Opening Zerodha's
  login in a NEW TAB instead means this tab never navigates away at
  all, so nothing here is ever at risk of being lost. The user logs
  into Zerodha in that new tab, copies the request_token (or the whole
  redirected URL — either works, see extract_request_token) from its
  address bar, and pastes it back here to finish connecting. This
  mirrors the same manual pattern already used for POC1's local Kite
  session refresh.

Run:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from auth.auth_service import AuthError, login, signup
from config import settings
from core.database import Database
from kite_oauth.connection_service import (
    add_mock_connection, connect_real_account, deactivate_connection,
    list_connections, update_connection,
)
from kite_oauth.kite_connect_flow import build_login_url
from users.session_builder import build_user_session

st.set_page_config(page_title="ArthaChakra", page_icon="🔆", layout="wide")


# ── Database (once per session) ─────────────────────────────────────────

@st.cache_resource
def get_db() -> Database:
    return Database()


db = get_db()

if db.is_mock:
    st.info(
        "🔵 Running against in-memory mock — MongoDB not reachable. "
        "Set ARTHACHAKRA_MONGO_URI in .env to persist data.",
        icon="🔵",
    )


# ── Session state ──────────────────────────────────────────────────────

for key, default in [
    ("user_id", None), ("display_name", None),
    ("just_connected_id", None), ("kite_setup", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def logout() -> None:
    st.session_state["user_id"] = None
    st.session_state["display_name"] = None
    st.session_state["just_connected_id"] = None
    st.session_state["kite_setup"] = None


# ═══════════════════════════════════════════════════════════════════════
#  LOGIN / SIGNUP
# ═══════════════════════════════════════════════════════════════════════

def render_login_signup() -> None:
    st.title("🔆 ArthaChakra")
    st.caption("Sign in to manage your strangle trading")

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In", type="primary"):
                try:
                    user = login(db, username, password)
                    st.session_state["user_id"] = user.user_id
                    st.session_state["display_name"] = user.display_name
                    st.rerun()
                except AuthError as e:
                    st.error(str(e))

    with tab_signup:
        with st.form("signup_form"):
            new_username = st.text_input("Choose a username")
            new_email    = st.text_input("Email")
            new_display  = st.text_input("Display name (optional)")
            new_password = st.text_input("Choose a password", type="password")
            if st.form_submit_button("Create Account", type="primary"):
                try:
                    user = signup(db, new_username, new_email, new_password, new_display)
                    st.session_state["user_id"] = user.user_id
                    st.session_state["display_name"] = user.display_name
                    st.success(f"Welcome, {user.display_name}!")
                    st.rerun()
                except AuthError as e:
                    st.error(str(e))


# ═══════════════════════════════════════════════════════════════════════
#  CONNECT KITE
# ═══════════════════════════════════════════════════════════════════════

def render_existing_connections(user_id: str) -> None:
    connections = list_connections(db, user_id)
    just_connected = st.session_state.get("just_connected_id")

    if not connections:
        st.caption("No Kite accounts connected yet.")
        return

    for c in connections:
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 2, 1])
            account_suffix = f" · Zerodha: {c.broker_account_name}" if c.broker_account_name and c.broker_account_name != "Mock User" else ""
            col1.markdown(f"**{c.label}**{account_suffix}")
            kind = "🔵 mock" if c.access_token.startswith("mock_tok_") else "✅ live"
            col2.markdown(f"`{c.broker.upper()}` · {c.account_type} · {kind}")
            if col3.button("Remove", key=f"rm_{c.connection_id}"):
                deactivate_connection(db, user_id, c.connection_id)
                st.rerun()

            show_edit = st.toggle(
                "✏️ Rename / change type", key=f"edit_toggle_{c.connection_id}",
                value=(c.connection_id == just_connected),
            )
            if show_edit:
                e1, e2, e3 = st.columns([3, 2, 1])
                new_label = e1.text_input(
                    "Label", value=c.label, key=f"edit_label_{c.connection_id}",
                    label_visibility="collapsed",
                )
                new_type = e2.selectbox(
                    "Type", ["index", "equity", "both"],
                    index=["index", "equity", "both"].index(c.account_type),
                    key=f"edit_type_{c.connection_id}", label_visibility="collapsed",
                )
                if e3.button("Save", key=f"save_{c.connection_id}"):
                    update_connection(db, user_id, c.connection_id,
                                      label=new_label, account_type=new_type)
                    if just_connected == c.connection_id:
                        st.session_state["just_connected_id"] = None
                    st.rerun()


def render_add_real_connection(user_id: str) -> None:
    st.markdown("**Add a real Kite account**")
    st.caption(
        "Uses YOUR OWN Kite Connect api_key/api_secret — each person registers "
        "their own personal app at https://developers.kite.trade. Saved here "
        "as part of your profile only; never shared with other users."
    )

    setup = st.session_state.get("kite_setup")

    if not setup:
        with st.form("kite_setup_form"):
            label = st.text_input("Label", placeholder="e.g. My Index Account")
            api_key = st.text_input("Your Kite API Key")
            api_secret = st.text_input("Your Kite API Secret", type="password")
            account_type = st.selectbox("Account type", ["index", "equity", "both"])

            if st.form_submit_button("1. Get Login URL", type="primary"):
                if not (label and api_key and api_secret):
                    st.error("Please fill in label, API key, and API secret.")
                else:
                    st.session_state["kite_setup"] = {
                        "label": label, "api_key": api_key,
                        "api_secret": api_secret, "account_type": account_type,
                    }
                    st.rerun()
    else:
        login_url = build_login_url(setup["api_key"])
        st.markdown(
            f'<a href="{login_url}" target="_blank" style="display:inline-block;'
            f'padding:8px 16px;background:#FF4B4B;color:white;border-radius:6px;'
            f'text-decoration:none;font-weight:600;">🔗 Open Zerodha Login (new tab)</a>',
            unsafe_allow_html=True,
        )
        st.caption(
            "After logging in on that new tab, copy the FULL redirected URL "
            "(or just the request_token value) from its address bar — even if "
            "the page shows an error, the URL bar still has what you need."
        )

        pasted = st.text_input(
            "2. Paste request_token or the redirected URL here", key="pasted_token",
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("3. Connect & Verify", type="primary", disabled=not pasted,
                         use_container_width=True):
                try:
                    conn, profile = connect_real_account(
                        db, user_id, label=setup["label"], api_key=setup["api_key"],
                        api_secret=setup["api_secret"], pasted_token=pasted,
                        account_type=setup["account_type"],
                    )
                    st.session_state["kite_setup"] = None
                    st.session_state["just_connected_id"] = conn.connection_id
                    name = profile.get("user_name") or profile.get("user_id") or "?"
                    st.success(f"✅ Connected as {name} — {conn.label}")
                    st.rerun()
                except RuntimeError as e:
                    st.error(str(e))
        with col2:
            if st.button("Cancel", use_container_width=True):
                st.session_state["kite_setup"] = None
                st.rerun()


def render_add_mock_connection(user_id: str) -> None:
    st.markdown("**Mock connection (testing, no real Kite needed)**")
    mock_label = st.text_input("Label", placeholder="e.g. Test Account",
                               key="mock_conn_label", label_visibility="collapsed")
    if st.button("🔵 Add Mock Connection", use_container_width=True, disabled=not mock_label):
        conn = add_mock_connection(db, user_id, label=mock_label, account_type="equity")
        st.session_state["just_connected_id"] = conn.connection_id
        st.success(f"Mock-connected: {conn.label}")
        st.rerun()


def render_connect_kite(user_id: str) -> None:
    st.subheader("🔗 Your Kite Connections")
    render_existing_connections(user_id)

    st.markdown("---")
    render_add_real_connection(user_id)
    st.markdown("---")
    render_add_mock_connection(user_id)


# ═══════════════════════════════════════════════════════════════════════
#  SESSION PREVIEW
# ═══════════════════════════════════════════════════════════════════════

def render_session_preview(user_id: str, display_name: str) -> None:
    st.subheader("📦 Your Session")
    st.caption(
        "The exact object every future component (agent, dashboard, "
        "strangle scanner) will read from — proof that login → real "
        "broker connection → session works end to end."
    )

    session = build_user_session(db, user_id, display_name)

    with st.container(border=True):
        st.markdown(f"**User:** {session.display_name} · `{session.user_id}`")

        st.markdown("**Broker Connections:**")
        if session.active_connections:
            for c in session.active_connections:
                kind = "🔵 mock" if c.access_token.startswith("mock_tok_") else "✅ live"
                st.markdown(f"&nbsp;&nbsp;• {c.label} ({c.broker.upper()}, {c.account_type}) — {kind}")
        else:
            st.markdown("&nbsp;&nbsp;_none connected yet_")

        st.markdown(
            f"**Rules:** {session.mandatory_rule_count} mandatory + "
            f"{session.optional_enabled_count} optional ON + {session.custom_rule_count} custom  "
            f"<span style='color:#888;font-size:13px;'>(rule seeding arrives in Step 3 — "
            f"0/0/0 is expected for now)</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "**Telegram:** "
            + (f"✅ chat `{session.telegram_chat_id}`" if session.telegram_verified
               else "⚠️ not connected <span style='color:#888;font-size:13px;'>"
                    "(arrives in Step 10)</span>"),
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    user_id = st.session_state.get("user_id")

    if not user_id:
        render_login_signup()
        return

    display_name = st.session_state.get("display_name", user_id)

    with st.sidebar:
        st.markdown(f"### 👤 {display_name}")
        st.caption(f"`{user_id}`")
        if st.button("Log out"):
            logout()
            st.rerun()
        st.divider()
        st.page_link("pages/1_Live_Dashboard.py", label="📊 Live Dashboard", icon="📊")
        st.divider()
        st.caption(
            "Step 2 validates: real signup/login, per-user Kite Connect "
            "credentials, manual token verification, and the assembled "
            "UserSession reflecting real broker connections."
        )

    st.title("🔆 ArthaChakra")
    render_connect_kite(user_id)
    st.markdown("---")
    render_session_preview(user_id, display_name)


main()
