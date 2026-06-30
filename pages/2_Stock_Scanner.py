"""
pages/2_Stock_Scanner.py
───────────────────────────
ArthaChakra — Stock Scanner

FOUR TABS, two complementary workflows side by side:
  1. Stock Selector  — shared ranking, NOW with Events/Intel status
     columns + checkbox multi-select + Excel export (ported from
     POC-13's final version, which already integrated POC-11/12).
  2. Stock Detail    — drill into one symbol: win-rate chart, monthly
     history, full events/intel detail (ported from POC-13).
  3. Monthly Update  — manual O/H/L entry fallback, for whenever a
     stock is missing from both automated paths (Bhavcopy + yfinance
     backfill) (ported from POC-13).
  4. My Action Plan  — PER-USER, automatic rule-engine verdicts
     (ENTER/CAUTION/AVOID via S-01/S-06/S-07/S-08/S-25/S-27). This is
     NOT a port of POC-13 — POC-13 never evaluated rules at all, only
     showed informational status badges. Kept distinct from Tab 1's
     manual checkbox shortlist (saved via dashboard/saved_shortlist.py)
     — one is a human's manual pick, the other is the rule book's
     automatic verdict. Both are useful, for different reasons.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from core.database import Database
from corporate_events.event_calendar import EventCalendar
from dashboard.action_plan import build_action_plan
from dashboard.saved_shortlist import get_shortlist, get_shortlists_for_month, save_shortlist
from dashboard.stock_analysis import THRESHOLDS, compute_stock_analysis, rank_and_filter, records_from_dicts
from dashboard.stock_universe import (
    get_all_symbols, get_latest_month, get_ohlc_for_analysis,
    get_stock, get_universe_stats, manual_upsert_ohlc,
)
from market_intel.intel_scanner import IntelScanner
from users.session_builder import build_user_session

st.set_page_config(page_title="Stock Scanner", page_icon="📊", layout="wide")

CURRENT_MONTH = datetime.now().strftime("%Y-%m")


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

TIER_BG = {"Violet": "#EDE7F6", "Green": "#E8F5E9", "Yellow": "#FFF8E1", "Red": "#FFEBEE"}
TIER_ICON = {"Violet": "🟣", "Green": "🟢", "Yellow": "🟡", "Red": "🔴"}


# ── Events / Intel quick-status helpers (Tab 1 columns + Tab 2 detail) ────

@st.cache_data(ttl=300)
def load_event_status(symbol: str) -> dict:
    """Quick status badge for the Selector table column."""
    try:
        cal = EventCalendar(db)
        summary = cal.get_summary(symbol, days_ahead=14)
        if not summary.events:
            return {"status": "✅ Clear", "detail": summary.to_agent_text()}
        if summary.blocking_events:
            actions = {e.action.value for e in summary.blocking_events}
            if "EXIT_IF_OPEN" in actions:
                return {"status": "🚨 Exit", "detail": summary.to_agent_text()}
            return {"status": "🔴 Block", "detail": summary.to_agent_text()}
        return {"status": "🔵 Watch", "detail": summary.to_agent_text()}
    except Exception as e:
        return {"status": "⚪ N/A", "detail": str(e)}


@st.cache_data(ttl=3600)
def load_intel_status(symbol: str) -> dict:
    """Quick status badge for the Selector table column."""
    try:
        scanner = IntelScanner(db)
        summary = scanner.scan_symbol(symbol)
        if summary.is_blocking:
            return {"status": "🔴 Block", "detail": summary.to_agent_text()}
        if summary.bearish_count > 0:
            return {"status": "🟡 Warn", "detail": summary.to_agent_text()}
        if summary.bullish_count > 0:
            return {"status": "🟢 Bull", "detail": summary.to_agent_text()}
        return {"status": "⚪ Neutral", "detail": summary.to_agent_text()}
    except Exception as e:
        return {"status": "⚪ N/A", "detail": str(e)}


# ── Analysis (shared) ──────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_all_analyses(_v: str, threshold: int, recency: bool):
    symbols = get_all_symbols(db)
    analyses = []
    for sym in symbols:
        raw = get_ohlc_for_analysis(db, sym)
        records = records_from_dicts(raw)
        if records:
            analyses.append(compute_stock_analysis(sym, records, target=threshold, recency_filter=recency))
    return analyses


def make_excel_download(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()


st.title("📊 Stock Scanner")
st.caption(f"NSE F&O universe  ·  Month: **{CURRENT_MONTH}**")

stats = get_universe_stats(db)
if stats["stocks"] == 0:
    st.error(
        "❌ No stocks in the universe yet. Run "
        "`python scripts/import_fo_universe.py /path/to/fo_mktlots.csv` first."
    )
    st.stop()

c1, c2 = st.columns(2)
c1.metric("Stocks in universe", stats["stocks"])
c2.metric("OHLC records", stats["ohlc_records"])

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 Stock Selector", "🔍 Stock Detail", "➕ Monthly Update", "🎯 My Action Plan"]
)

with st.sidebar:
    st.header("⚙️ Selector Settings")
    threshold = st.slider("Target Threshold %", 4, 15, 10, 1)
    min_win = st.slider("Minimum Win Rate %", 50, 90, 60, 5)
    recency = st.toggle("Recency Filter", value=True,
                        help="Last 2 months both Loose -> Red regardless of history")
    show_red = st.checkbox("Show Red tier", value=False)
    st.divider()
    load_events_intel = st.toggle("Load Events + Intel columns", value=False,
                                  help="Fetches real data per stock - adds latency the first time.")


# ══════════════════════════════════════════════════════════════════════════
# TAB 1: STOCK SELECTOR — shared ranking + Events/Intel columns + checkboxes
# ══════════════════════════════════════════════════════════════════════════

with tab1:
    all_analyses = load_all_analyses("v2", threshold, recency)
    ranked = rank_and_filter(all_analyses, min_win_pct=min_win, include_red=show_red)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Qualifying stocks", len(ranked))
    m2.metric("Violet 🟣", sum(1 for a in ranked if a.tier == "Violet"))
    m3.metric("Green 🟢", sum(1 for a in ranked if a.tier == "Green"))
    m4.metric("Yellow 🟡", sum(1 for a in ranked if a.tier == "Yellow"))

    st.subheader(f"Qualified Stocks — {threshold}% threshold, ≥{min_win}% win rate")

    if not ranked:
        st.info("No stocks qualify at these settings. Try lowering the minimum win rate.")
    else:
        select_all = st.checkbox("Select All", value=False, key="select_all_toggle")

        rows = []
        for i, a in enumerate(ranked, 1):
            recent = a.last_3_months   # most recent first
            row = {
                "Select": select_all, "Rank": i, "Tier": f"{TIER_ICON.get(a.tier,'')} {a.tier}",
                "Symbol": a.symbol, "Months": a.total_months, "Win%": f"{a.target_win_pct:.0f}%",
            }
            # Last 3 months, most recent first - was a single "Latest" column
            # showing only recent[0]; now shows all 3 (per the new requirement).
            for j in range(3):
                col_label = f"M-{j+1}" if j > 0 else "Latest"
                if j < len(recent):
                    row[col_label] = "✅" if recent[j]["status"] == "Win" else "❌"
                else:
                    row[col_label] = "-"
            if load_events_intel:
                row["Events"] = load_event_status(a.symbol)["status"]
                row["Intel"] = load_intel_status(a.symbol)["status"]
            rows.append(row)
        df = pd.DataFrame(rows)

        edited = st.data_editor(
            df, use_container_width=True, height=500, hide_index=True,
            disabled=[c for c in df.columns if c != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn(required=True),
                "Latest": st.column_config.TextColumn(help="Most recent completed month"),
                "M-2": st.column_config.TextColumn(help="2 months ago"),
                "M-3": st.column_config.TextColumn(help="3 months ago"),
            },
        )

        selected_symbols = edited[edited["Select"] == True]["Symbol"].tolist()

        st.divider()
        sc1, sc2, sc3 = st.columns(3)

        with sc1:
            shortlist_name = st.text_input(
                "Shortlist name", value="", placeholder="e.g. Conservative, High IV",
                key="shortlist_name_input",
                help="Save multiple shortlists for the same month under different names.",
            )
            notes = st.text_input("Notes (optional)", key="shortlist_notes")
            if st.button("💾 Save Shortlist", disabled=not selected_symbols, type="primary"):
                save_shortlist(db, user_id, CURRENT_MONTH, selected_symbols,
                               shortlist_name=shortlist_name,
                               filters={"threshold": threshold, "min_win": min_win, "recency": recency},
                               notes=notes)
                saved_name = shortlist_name.strip() or "Default"
                st.success(f"✅ Saved '{saved_name}' ({len(selected_symbols)} stocks) for {CURRENT_MONTH}")

        with sc2:
            existing_lists = get_shortlists_for_month(db, user_id, CURRENT_MONTH)
            if existing_lists:
                st.caption(f"📂 {len(existing_lists)} saved shortlist(s) this month:")
                for el in existing_lists:
                    st.caption(f"  **{el.get('shortlist_name', 'Default')}**: {', '.join(el.get('symbols', []))}")
            else:
                st.caption("No saved shortlists yet for this month.")

        with sc3:
            if selected_symbols:
                plan_df = edited[edited["Select"] == True].drop(columns=["Select"])
                xlsx = make_excel_download(plan_df, sheet_name=f"Shortlist-{CURRENT_MONTH}")
                st.download_button("⬇️ Download Selected", data=xlsx,
                                  file_name=f"shortlist_{CURRENT_MONTH}.xlsx",
                                  mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ══════════════════════════════════════════════════════════════════════════
# TAB 2: STOCK DETAIL — drill into one symbol
# ══════════════════════════════════════════════════════════════════════════

with tab2:
    all_symbols = get_all_symbols(db)
    selected = st.selectbox("Select Stock", all_symbols, key="detail_symbol")

    if selected:
        raw = get_ohlc_for_analysis(db, selected)
        records = records_from_dicts(raw)

        if not records:
            st.info(f"No OHLC history for {selected} yet.")
        else:
            detail_t = st.select_slider("Detail Threshold %", THRESHOLDS, value=threshold, key="detail_threshold")
            a = compute_stock_analysis(selected, records, target=detail_t)
            info = get_stock(db, selected)

            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Total Months", a.total_months)
            d2.metric(f"Win @{detail_t}%", f"{a.target_win_pct:.0f}%")
            d3.metric("Tier", f"{TIER_ICON.get(a.tier,'')} {a.tier}")
            d4.metric("Index membership", ", ".join(info.get("index_memberships", [])) or "-" if info else "-")

            st.subheader("Win Rate at Each Threshold")
            chart_df = pd.DataFrame({
                "Threshold": [f"{t}%" for t in THRESHOLDS],
                "Win Rate": [round(a.win_rates[t] * 100, 1) for t in THRESHOLDS],
            })
            st.bar_chart(chart_df.set_index("Threshold")["Win Rate"])

            st.subheader(f"Monthly History (Win = both sides ≤ {detail_t}%)")
            mrows = [
                {"Month": r.month_key, "Open": r.open, "High": r.high, "Low": r.low,
                 "OH%": round(r.oh_pct * 100, 2), "OL%": round(r.ol_pct * 100, 2),
                 "Status": "✅ Win" if r.is_win(detail_t) else "❌ Loose"}
                for r in a.monthly
            ]
            hist_df = pd.DataFrame(mrows)
            st.dataframe(hist_df, use_container_width=True, height=400, hide_index=True)

            st.divider()
            st.subheader("📅 Corporate Events (next 14 days)")
            ev = load_event_status(selected)
            st.text(ev["detail"])

            st.subheader("📰 Brokerage & Market Intelligence")
            intel = load_intel_status(selected)
            st.text(intel["detail"])


# ══════════════════════════════════════════════════════════════════════════
# TAB 3: MONTHLY UPDATE — manual O/H/L entry fallback
# ══════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("Add / Override Month OHLC Data")
    st.info(
        "Manual fallback — the daily Bhavcopy job and the yfinance backfill "
        "(market_data/) already populate this automatically. Use this only "
        "for a stock missing from both, or to correct a bad value."
    )

    month_key = st.text_input("Month (YYYY-MM)", value=CURRENT_MONTH)
    valid_month = len(month_key) == 7 and month_key[4] == "-"
    if month_key and not valid_month:
        st.error("❌ Invalid format. Use YYYY-MM")

    symbol_to_update = st.selectbox("Symbol", get_all_symbols(db), key="manual_symbol")

    if valid_month and symbol_to_update:
        latest = get_latest_month(db, symbol_to_update)
        st.caption(f"Latest month on file for {symbol_to_update}: {latest or 'none'}")

        c1, c2, c3 = st.columns(3)
        o = c1.number_input("Open", min_value=0.0, step=0.05)
        h = c2.number_input("High", min_value=0.0, step=0.05)
        l = c3.number_input("Low", min_value=0.0, step=0.05)

        if st.button("💾 Save", disabled=not (o > 0 and h > 0 and l > 0)):
            if h < o:
                st.error(f"❌ High ({h}) cannot be less than Open ({o})")
            elif l > o:
                st.error(f"❌ Low ({l}) cannot be greater than Open ({o})")
            else:
                manual_upsert_ohlc(db, symbol_to_update, month_key, o, h, l)
                st.success(f"✅ Saved {symbol_to_update} for {month_key}")
                st.cache_data.clear()


# ══════════════════════════════════════════════════════════════════════════
# TAB 4: MY ACTION PLAN — per-user, automatic rule-engine verdicts
# ══════════════════════════════════════════════════════════════════════════

with tab4:
    st.subheader("🎯 My Action Plan")
    st.caption(
        "Different from Tab 1's manual shortlist — this runs the actual rule "
        "book (VIX, IVR, beta, range-bound, results-before-expiry, market intel) "
        "against each candidate, scoped to YOUR effective rules and broker "
        "connection. Another user can see a different plan here for the same stocks."
    )

    session = build_user_session(db, user_id)

    available_shortlists = get_shortlists_for_month(db, user_id, CURRENT_MONTH)
    shortlist_options = ["(All qualifying stocks — no shortlist)"] + [
        s["shortlist_name"] for s in available_shortlists
    ]
    chosen_shortlist_name = st.selectbox(
        "Stocks to evaluate",
        shortlist_options,
        help="Choose one of your saved shortlists from Tab 1, or evaluate the full "
            "qualifying universe directly.",
    )

    candidate_symbols = None
    if chosen_shortlist_name != shortlist_options[0]:
        chosen_doc = next(s for s in available_shortlists if s["shortlist_name"] == chosen_shortlist_name)
        candidate_symbols = chosen_doc["symbols"]
        st.caption(f"📂 Evaluating {len(candidate_symbols)} stocks from '{chosen_shortlist_name}': "
                  f"{', '.join(candidate_symbols)}")

    a1, a2 = st.columns(2)
    min_tier_for_plan = a1.selectbox("Minimum tier to consider", ["Violet", "Green", "Yellow"], index=1,
                                     disabled=candidate_symbols is not None,
                                     help="Tier filtering only applies when evaluating the full "
                                         "universe — a chosen shortlist is evaluated as-is.")
    plan_target = a2.slider("Win-rate target threshold %", 4, 15, 10, 1, key="plan_threshold")

    if st.button("🔄 Build My Action Plan", type="primary"):
        with st.spinner("Checking VIX, IVR, beta, corporate events, market intel for each candidate..."):
            plan = build_action_plan(
                db, session, candidate_symbols=candidate_symbols,
                min_tier=min_tier_for_plan, target_threshold=plan_target,
            )
        st.session_state["_action_plan"] = plan

    plan = st.session_state.get("_action_plan")
    if plan is None:
        st.info("☝️ Click 'Build My Action Plan' to generate your personalized recommendations.")
    else:
        p1, p2, p3 = st.columns(3)
        p1.metric("✅ ENTER", len(plan.enter))
        p2.metric("🟡 CAUTION", len(plan.caution))
        p3.metric("🔴 AVOID", len(plan.avoid))

        for label, group in [("✅ ENTER", plan.enter), ("🟡 CAUTION", plan.caution), ("🔴 AVOID", plan.avoid)]:
            if not group:
                continue
            st.markdown(f"### {label}")
            for v in list(group):   # iterate a copy - the button below mutates `group` itself
                exp_col, btn_col = st.columns([5, 1])
                with exp_col:
                    with st.expander(f"{v.symbol}  (tier={v.tier}, win={v.win_pct:.0f}%)"):
                        for r in v.rule_results:
                            icon = {"PASS": "✅", "FAIL": "🔴", "WARN": "🟡", "ADVISORY": "ℹ️",
                                   "NOT_YET_EVALUABLE": "⚪"}.get(r.status, "")
                            st.markdown(f"{icon} **[{r.rule_id}]** {r.status} — {r.message}")
                with btn_col:
                    if st.button("🗑️ Remove", key=f"remove_{v.symbol}_{label}"):
                        group.remove(v)
                        st.rerun()
