"""
pages/1_Live_Dashboard.py
───────────────────────────
ArthaChakra — Live Positions Dashboard

Streamlit multipage: this file is automatically picked up as a
separate page titled "Live Dashboard" in the sidebar nav.

Shows all open option positions across ALL connected Kite accounts
for the logged-in user, grouped into strangles — PE legs first (sorted
by strike), then CE legs, with spot price, premium, P&L, and
Black-Scholes / Dhan HQ delta per leg.
"""

from __future__ import annotations

import streamlit as st

from brokers.kite_client import fetch_ltp, fetch_positions
from config import settings
from core.database import Database
from dashboard.dhan_greeks import fetch_greeks_for_strangles
from dashboard.strangle_grouper import (
    KITE_SPOT_MAP, MOCK_POSITIONS, MOCK_SPOTS,
    group_positions_into_strangles, parse_option_symbol,
)
from kite_oauth.connection_service import list_connections

st.set_page_config(page_title="Live Dashboard", page_icon="📊", layout="wide")


# ── Auth guard ───────────────────────────────────────────────────────────

user_id = st.session_state.get("user_id")
if not user_id:
    st.warning("Please log in from the main page first.")
    st.stop()


# ── Database ─────────────────────────────────────────────────────────────

@st.cache_resource
def get_db() -> Database:
    return Database()

db = get_db()


# ── Helpers ───────────────────────────────────────────────────────────────

def _fmt_inr(v: float) -> str:
    if abs(v) >= 1_00_00_000: return f"₹{v / 1_00_00_000:+.2f} Cr"
    if abs(v) >= 1_00_000:    return f"₹{v / 1_00_000:+.2f} L"
    return f"₹{v:+,.0f}"


def _render_leg_row(leg) -> None:
    info = leg.delta_info
    delta_str    = f"{info['delta']:+.3f}" if info.get("delta") is not None else "N/A"
    pos_delta_str = (
        f"{info['delta'] * leg.quantity:+.1f}" if info.get("delta") is not None else "N/A"
    )
    iv_str    = f"{info['implied_vol_pct']:.1f}%" if info.get("implied_vol_pct") is not None else "N/A"
    src_badge = f" <span style='font-size:10px;color:#aaa;'>({info.get('source','?')})</span>"
    pnl_color = "#1a9850" if leg.pnl >= 0 else "#d73027"

    cols = st.columns([1.3, 1, 1.1, 1.1, 1.3, 1.3, 1.2])
    strike_str = f"₹{leg.strike:,.1f}" if leg.strike % 1 else f"₹{int(leg.strike):,}"
    cols[0].markdown(f"**{strike_str}**")
    cols[1].markdown(f"Qty {leg.quantity}")
    cols[2].markdown(f"LTP ₹{leg.ltp:.2f}")
    cols[3].markdown(f"Entry ₹{leg.avg_price:.2f}")
    cols[4].markdown(
        f"<span style='color:{pnl_color};font-weight:600;'>P&L {leg.pnl:+,.0f}</span>",
        unsafe_allow_html=True,
    )
    cols[5].markdown(
        f"Δ {delta_str}{src_badge}<br><span style='font-size:11px;color:#888;'>IV {iv_str}</span>",
        unsafe_allow_html=True,
    )
    cols[6].markdown(f"Pos-Δ **{pos_delta_str}**")


def render_strangle_card(s) -> None:
    pnl_str  = _fmt_inr(s.total_pnl)
    spot_str = f"₹{s.spot:,.0f}" if s.spot else "N/A"
    title = (
        f"{s.status_icon} **{s.underlying}** · {s.expiry} · 🏦 {s.connection_label}  "
        f"|  Spot: {spot_str}  |  Premium: ₹{s.combined_ltp:.2f}"
        f"  |  P&L: {pnl_str}  |  **{s.status}**"
    )
    with st.expander(title, expanded=(s.status in ("WARNING", "CRITICAL"))):
        st.markdown("###### 📉 PE Leg(s) — sorted by strike")
        if s.pe_legs_sorted:
            for leg in s.pe_legs_sorted:
                _render_leg_row(leg)
        else:
            st.caption("No PE leg.")

        st.markdown("###### 📈 CE Leg(s) — sorted by strike")
        if s.ce_legs_sorted:
            for leg in s.ce_legs_sorted:
                _render_leg_row(leg)
        else:
            st.caption("No CE leg.")

        st.divider()
        m1, m2, m3 = st.columns(3)
        m1.metric("Combined Premium", f"₹{s.combined_ltp:.2f}")
        m2.metric("Total P&L", pnl_str)
        nd = s.net_position_delta
        m3.metric("Net Position Delta", f"{nd:+.1f}" if nd is not None else "N/A")

        st.markdown(
            f"{s.delta_status_icon} **Delta Status: {s.delta_status}**  "
            f"<span style='color:#888;font-size:12px;'>(net delta within ≈1 lot = neutral)</span>",
            unsafe_allow_html=True,
        )


# ── Main ──────────────────────────────────────────────────────────────────

st.title("📊 Live Positions — Grouped by Strangle")
st.caption(
    "Positions across all your connected Kite accounts. "
    "PE legs shown first, CE legs second, both sorted by strike. "
    "Delta source shown per leg: **dhan** = Dhan HQ option chain, **bs** = Black-Scholes fallback."
)

col_refresh, col_spacer = st.columns([1, 8])
if col_refresh.button("🔄 Refresh"):
    st.rerun()

connections = list_connections(db=db, user_id=user_id)
if not connections:
    st.info("No Kite accounts connected yet. Go to the main page to add one.")
    st.stop()

# ── Fetch raw positions from all connections ─────────────────────────────
all_raw: list[dict] = []
for conn in connections:
    if conn.access_token.startswith("mock_tok_"):
        for p in MOCK_POSITIONS:
            tagged = dict(p)
            tagged["_connection_id"]    = conn.connection_id
            tagged["_connection_label"] = f"{conn.label} (mock)"
            all_raw.append(tagged)
    else:
        raw = fetch_positions(conn)
        for p in raw:
            p["_connection_id"]    = conn.connection_id
            p["_connection_label"] = conn.label
        all_raw.extend(raw)

if not all_raw:
    st.info("No open positions found across your connected accounts.")
    st.stop()

# ── Debug: show raw symbols from Kite (helps diagnose any parsing issues) ─
with st.expander("🔍 Raw positions from Kite (click to debug symbol parsing)"):
    for p in all_raw:
        st.text(
            f"{p.get('tradingsymbol','?'):35s}  "
            f"qty={p.get('quantity',0):6d}  "
            f"ltp={p.get('last_price', p.get('ltp',0)):8.2f}  "
            f"avg={p.get('average_price',0):8.2f}  "
            f"account={p.get('_connection_label','?')}"
        )

# ── Spot prices ───────────────────────────────────────────────────────────
needed_underlyings: set[str] = set()
for p in all_raw:
    parsed = parse_option_symbol(p.get("tradingsymbol", ""))
    if parsed:
        needed_underlyings.add(parsed.underlying)

spot_prices: dict[str, float] = {}
real_conn = next((c for c in connections if not c.access_token.startswith("mock_tok_")), None)

if real_conn and needed_underlyings:
    # Indices have special Kite instrument strings; equities use NSE:SYMBOL
    underlying_to_instr: dict[str, str] = {}
    for u in needed_underlyings:
        underlying_to_instr[u] = KITE_SPOT_MAP.get(u, f"NSE:{u}")

    ltp_data = fetch_ltp(real_conn, list(underlying_to_instr.values()))
    for u, instr in underlying_to_instr.items():
        spot_prices[u] = ltp_data.get(instr) or MOCK_SPOTS.get(u, 0.0)
else:
    for u in needed_underlyings:
        spot_prices[u] = MOCK_SPOTS.get(u, 0.0)

# ── Group + Greeks ────────────────────────────────────────────────────────
strangles, unmatched = group_positions_into_strangles(all_raw, spot_prices)

if not strangles:
    st.info("Positions found but none matched the strangle pattern (CE+PE pair).")
    if unmatched:
        with st.expander(f"⚠️ {len(unmatched)} unmatched position(s)"):
            for p in unmatched:
                st.text(f"  {p.get('tradingsymbol','?')}  qty={p.get('quantity',0)}"
                        f"  account={p.get('_connection_label','?')}")
    st.stop()

greeks_map = fetch_greeks_for_strangles(strangles, settings)
for s in strangles:
    s.compute_deltas(greeks_map)

# ── Render ────────────────────────────────────────────────────────────────
st.markdown(f"**{len(strangles)} strangle(s)** across "
            f"**{len(connections)} account(s)** · "
            f"underlyings: {', '.join(sorted(needed_underlyings))}")
st.markdown("---")

for s in strangles:
    render_strangle_card(s)

if unmatched:
    with st.expander(f"⚠️ {len(unmatched)} unmatched / non-strangle position(s)"):
        for p in unmatched:
            st.text(f"  {p.get('tradingsymbol','?')}  qty={p.get('quantity',0)}"
                    f"  account={p.get('_connection_label','?')}")
