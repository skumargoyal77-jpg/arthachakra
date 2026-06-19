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

from brokers.kite_client import fetch_ltp, fetch_position_margin, fetch_positions
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
    delta_str     = f"{info['delta']:+.3f}" if info.get("delta") is not None else "N/A"
    pos_delta_str = f"{leg.position_delta:+.1f}" if leg.position_delta is not None else "N/A"
    lot_eq_str    = f"{leg.lot_equivalent_delta:+.2f}" if leg.lot_equivalent_delta is not None else "N/A"
    iv_str        = f"{info['implied_vol_pct']:.1f}%" if info.get("implied_vol_pct") is not None else "N/A"
    src_badge     = f" <span style='font-size:10px;color:#aaa;'>({info.get('source','?')})</span>"
    pnl_color     = "#1a9850" if leg.pnl >= 0 else "#d73027"

    cols = st.columns([1.1, 1, 1.1, 1.1, 1.3, 1.2, 1, 1])
    strike_str = f"{leg.strike:,.1f}" if leg.strike % 1 else f"{int(leg.strike):,}"
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
    cols[6].markdown(
        f"Pos-Δ {pos_delta_str}<br><span style='font-size:10px;color:#888;'>(raw)</span>",
        unsafe_allow_html=True,
    )
    cols[7].markdown(
        f"**Δ {lot_eq_str}**<br><span style='font-size:10px;color:#888;'>(per lot)</span>",
        unsafe_allow_html=True,
    )


def render_strangle_card(s, margin_used: float | None) -> None:
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
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Combined Premium", f"₹{s.combined_ltp:.2f}")
        m2.metric("Total P&L", pnl_str)
        nd = s.net_position_delta
        m3.metric("Net Position Delta (raw)", f"{nd:+.1f}" if nd is not None else "N/A")
        nle = s.net_lot_equivalent_delta
        m4.metric(
            "Net Delta (per lot)", f"{nle:+.2f}" if nle is not None else "N/A",
            help="Comparable to Sensibull/broker apps — how many lots of "
                 "the underlying this position currently behaves like." +
                 ("" if s.lot_size else " (lot size unknown for this underlying)"),
        )

        st.markdown(
            f"{s.delta_status_icon} **Delta Status: {s.delta_status}**  "
            f"<span style='color:#888;font-size:12px;'>(net delta within "
            f"≈1 lot{f' = {s.lot_size} shares' if s.lot_size else ''} is treated as neutral)</span>",
            unsafe_allow_html=True,
        )

        st.divider()
        st.markdown("###### 💰 Profit / Loss / Breakeven")
        p1, p2, p3, p4 = st.columns(4)

        mp = s.max_profit
        mp_pct = s.max_profit_pct(margin_used)
        p1.metric(
            "Max Profit", f"₹{mp:,.0f}" if mp is not None else "N/A",
            f"{mp_pct:+.1f}%" if mp_pct is not None else None,
        )

        pl = s.profit_left
        pl_pct = s.profit_left_pct(margin_used)
        p2.metric(
            "Profit Left", f"₹{pl:,.0f}" if pl is not None else "N/A",
            f"{pl_pct:+.1f}%" if pl_pct is not None else None,
        )

        p3.metric("Loss Left", s.loss_left_label)

        pop = s.pop_pct
        p4.metric(
            "POP", f"{pop:.0f}%" if pop is not None else "N/A",
            help="Probability of Profit — modeled chance the underlying "
                 "finishes between the two breakevens at expiry, using "
                 "average implied vol across both legs.",
        )

        b1, b2, b3 = st.columns(3)
        lb, ub = s.lower_breakeven, s.upper_breakeven
        lb_pct, ub_pct = s.lower_breakeven_pct, s.upper_breakeven_pct
        b1.metric(
            "Lower Breakeven", f"₹{lb:,.1f}" if lb is not None else "N/A",
            f"{lb_pct:+.1f}%" if lb_pct is not None else None,
        )
        b2.metric(
            "Upper Breakeven", f"₹{ub:,.1f}" if ub is not None else "N/A",
            f"{ub_pct:+.1f}%" if ub_pct is not None else None,
        )
        b3.metric(
            "Margin Used", f"₹{margin_used:,.0f}" if margin_used is not None else "N/A",
            help="Real SPAN+exposure margin from Kite's basket calculator. "
                 "Shows N/A for mock connections, or if the live call fails "
                 "(see console log for the exact reason).",
        )


# ── Main ──────────────────────────────────────────────────────────────────

st.title("📊 Live Positions — Grouped by Strangle")
st.caption(
    "Positions across all your connected Kite accounts. "
    "PE legs shown first, CE legs second, both sorted by strike. "
    "Delta source shown per leg: **dhan** = Dhan HQ option chain, **bs** = Black-Scholes fallback."
)

refresh_minutes = st.session_state.get("dashboard_refresh_minutes", 5)

# Prefer st.fragment(run_every=...) when available — it re-runs just this
# section on a timer WITHOUT a full page reload, so the browser's
# WebSocket never disconnects on every refresh cycle (which is what was
# causing "Connection error" / session resets at short intervals). Falls
# back to a full-page meta-refresh on older Streamlit versions that don't
# support run_every yet.
import inspect

_supports_run_every = hasattr(st, "fragment") and \
    "run_every" in inspect.signature(st.fragment).parameters

col_refresh, col_info = st.columns([1, 8])
if col_refresh.button("🔄 Refresh now"):
    st.rerun()

if refresh_minutes and refresh_minutes > 0:
    mode = "smooth, no page reload" if _supports_run_every else "full page reload"
    col_info.caption(f"⏱️ Auto-refreshing every {refresh_minutes} min ({mode}) — "
                     f"change this on the main ArthaChakra page")
else:
    col_info.caption("Auto-refresh is off (enable it on the main ArthaChakra page)")

if refresh_minutes and refresh_minutes > 0 and not _supports_run_every:
    st.markdown(
        f'<meta http-equiv="refresh" content="{int(refresh_minutes) * 60}">',
        unsafe_allow_html=True,
    )


def _render_dashboard_body() -> None:
    connections = list_connections(db=db, user_id=user_id)
    if not connections:
        st.info("No Kite accounts connected yet. Go to the main page to add one.")
        return

    # ── Fetch raw positions from all connections ─────────────────────────
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
        return

    # ── Debug: show raw symbols from Kite ─────────────────────────────────
    with st.expander("🔍 Raw positions from Kite (click to debug symbol parsing)"):
        for p in all_raw:
            st.text(
                f"{p.get('tradingsymbol','?'):35s}  "
                f"qty={p.get('quantity',0):6d}  "
                f"ltp={p.get('last_price', p.get('ltp',0)):8.2f}  "
                f"avg={p.get('average_price',0):8.2f}  "
                f"account={p.get('_connection_label','?')}"
            )

    # ── Spot prices ────────────────────────────────────────────────────────
    needed_underlyings: set[str] = set()
    for p in all_raw:
        parsed = parse_option_symbol(p.get("tradingsymbol", ""))
        if parsed:
            needed_underlyings.add(parsed.underlying)

    spot_prices: dict[str, float] = {}
    real_conn = next((c for c in connections if not c.access_token.startswith("mock_tok_")), None)

    if real_conn and needed_underlyings:
        underlying_to_instr: dict[str, str] = {}
        for u in needed_underlyings:
            underlying_to_instr[u] = KITE_SPOT_MAP.get(u, f"NSE:{u}")

        ltp_data = fetch_ltp(real_conn, list(underlying_to_instr.values()))
        for u, instr in underlying_to_instr.items():
            spot_prices[u] = ltp_data.get(instr) or MOCK_SPOTS.get(u, 0.0)
    else:
        for u in needed_underlyings:
            spot_prices[u] = MOCK_SPOTS.get(u, 0.0)

    # ── Group + Greeks ─────────────────────────────────────────────────────
    strangles, unmatched = group_positions_into_strangles(all_raw, spot_prices)

    if not strangles:
        st.info("Positions found but none matched the strangle pattern (CE+PE pair).")
        if unmatched:
            with st.expander(f"⚠️ {len(unmatched)} unmatched position(s)"):
                for p in unmatched:
                    st.text(f"  {p.get('tradingsymbol','?')}  qty={p.get('quantity',0)}"
                            f"  account={p.get('_connection_label','?')}")
        return

    greeks_map = fetch_greeks_for_strangles(strangles, settings)
    for s in strangles:
        s.compute_deltas(greeks_map)

    connections_by_id = {c.connection_id: c for c in connections}

    # ── Render ─────────────────────────────────────────────────────────────
    st.markdown(f"**{len(strangles)} strangle(s)** across "
                f"**{len(connections)} account(s)** · "
                f"underlyings: {', '.join(sorted(needed_underlyings))}")
    st.markdown("---")

    for s in strangles:
        conn = connections_by_id.get(s.connection_id)
        margin_used = fetch_position_margin(conn, s.ce_legs + s.pe_legs) if conn else None
        render_strangle_card(s, margin_used)

    if unmatched:
        with st.expander(f"⚠️ {len(unmatched)} unmatched / non-strangle position(s)"):
            for p in unmatched:
                st.text(f"  {p.get('tradingsymbol','?')}  qty={p.get('quantity',0)}"
                        f"  account={p.get('_connection_label','?')}")


if _supports_run_every and refresh_minutes and refresh_minutes > 0:
    _render_dashboard_body_fragment = st.fragment(run_every=f"{int(refresh_minutes)}m")(_render_dashboard_body)
    _render_dashboard_body_fragment()
else:
    _render_dashboard_body()