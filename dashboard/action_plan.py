"""
dashboard/action_plan.py
───────────────────────────
The per-user Action Plan — takes the SHARED stock universe ranking
(dashboard/stock_analysis.py's backtested win-rate tiers) and filters
it through EACH USER'S OWN effective rules.

WHY THIS IS GENUINELY PER-USER, NOT JUST A SHARED LIST WITH A USER_ID
TAG ON IT (which is what POC-13's original action_plans collection
was — one shared list per month, no user differentiation at all):

  1. get_effective_rules(db, user_id) returns a DIFFERENT rule set per
     user (toggled defaults, custom rules) — two users checking the
     same candidate symbol can get different verdicts on the same
     rule_id, or have entirely different custom rules firing.
  2. VIX context comes from the user's OWN broker connection — a user
     with no real connection gets ADVISORY (missing context) on
     VIX-gated rules where a user with a real connection gets a real
     PASS/FAIL.
  3. Corporate events / market intel checks run per candidate symbol
     regardless of user, but WHICH rules apply to evaluate them is
     still user-specific (effective_rules).

This is computed fresh, not persisted as a shared monthly snapshot —
unlike POC-13's original design, there's no "Action Plan for everyone
this month" document; it's "this user's Action Plan, right now."

PROJECT PATH:  dashboard/action_plan.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from corporate_events.event_calendar import EventCalendar
from core.database import Database
from core.logging_config import setup_logging
from dashboard.stock_analysis import (
    compute_stock_analysis, rank_and_filter, records_from_dicts,
)
from dashboard.stock_universe import get_all_symbols, get_ohlc_for_analysis
from market_data.iv_updater import get_latest_ivr
from market_data.ohlc_updater import compute_beta, get_monthly_range_pct
from market_intel.intel_scanner import IntelScanner
from rules.engine import RuleEngine
from rules.rules_service import get_effective_rules
from rules.series_calendar import get_series_window
from users.models import UserSession

logger = setup_logging(__name__)

# Rules checked per candidate symbol when building an Action Plan —
# entry-time, symbol-level checks. Position-specific rules (A-xx,
# ES-xx) don't apply here since there's no open position yet.
ACTION_PLAN_RULE_IDS = ["S-01", "S-06", "S-07", "S-08", "S-25", "S-27"]


@dataclass
class SymbolVerdict:
    symbol: str
    tier: str                  # Violet/Green/Yellow/Red from the shared stock analysis
    win_pct: float
    verdict: str = "ENTER"     # ENTER | CAUTION | AVOID
    rule_results: list = field(default_factory=list)   # list[RuleResult]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "tier": self.tier, "win_pct": self.win_pct,
            "verdict": self.verdict,
            "rule_results": [
                {"rule_id": r.rule_id, "status": r.status, "message": r.message}
                for r in self.rule_results
            ],
        }


@dataclass
class ActionPlan:
    user_id: str
    as_of: date
    enter: list[SymbolVerdict] = field(default_factory=list)
    caution: list[SymbolVerdict] = field(default_factory=list)
    avoid: list[SymbolVerdict] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [f"Action Plan ({self.as_of}) — {len(self.enter)} ENTER, "
                f"{len(self.caution)} CAUTION, {len(self.avoid)} AVOID"]
        for label, group in [("ENTER", self.enter), ("CAUTION", self.caution), ("AVOID", self.avoid)]:
            if not group:
                continue
            lines.append(f"\n{label}:")
            for v in group:
                lines.append(f"  {v.symbol} (tier={v.tier}, win={v.win_pct:.0f}%)")
                for r in v.rule_results:
                    if r.status not in ("PASS",):
                        lines.append(f"    [{r.rule_id}] {r.status}: {r.message}")
        return "\n".join(lines)


def _verdict_from_results(results) -> str:
    if any(r.status == "FAIL" for r in results):
        return "AVOID"
    if any(r.status in ("WARN",) for r in results):
        return "CAUTION"
    return "ENTER"


def build_action_plan(
    db: Database,
    session: UserSession,
    candidate_symbols: list[str] | None = None,
    min_tier: str = "Green",
    target_threshold: int = 10,
    as_of: date | None = None,
) -> ActionPlan:
    """
    Builds ONE user's Action Plan. Two different sessions (two
    different users) calling this with the same candidate_symbols can
    get different verdicts — see module docstring for why.
    """
    as_of = as_of or date.today()
    user_id = session.user_id

    symbols = candidate_symbols or get_all_symbols(db)
    if not symbols:
        return ActionPlan(user_id=user_id, as_of=as_of)

    # ── Shared stock-universe ranking (same for every user) ─────────────
    analyses = []
    for sym in symbols:
        raw = get_ohlc_for_analysis(db, sym)
        records = records_from_dicts(raw)
        if records:
            analyses.append(compute_stock_analysis(sym, records, target=target_threshold))

    min_tier_rank = {"Violet": 0, "Green": 1, "Yellow": 2, "Red": 3}.get(min_tier, 1)
    qualified = [a for a in rank_and_filter(analyses, min_win_pct=0, include_red=True)
                if a.tier_rank <= min_tier_rank]

    # ── Per-user rule evaluation against each qualified symbol ─────────
    effective_rules = get_effective_rules(db, user_id)
    rules_by_id = {r["rule_id"]: r for r in effective_rules if r["rule_id"] in ACTION_PLAN_RULE_IDS}
    engine = RuleEngine()

    real_conn = next(
        (c for c in session.active_connections
         if c.access_token and not c.access_token.startswith("mock_tok_")),
        None,
    )
    vix = None
    if real_conn:
        from market_data.vix_fetcher import get_latest_vix
        latest = get_latest_vix(db)
        vix = latest["value"] if latest else None

    cal = EventCalendar(db)
    scanner = IntelScanner(db)
    series_expiry = get_series_window(as_of).expiry

    plan = ActionPlan(user_id=user_id, as_of=as_of)

    for a in qualified:
        ctx = {
            "as_of": as_of,
            "vix": vix,
            "beta": compute_beta(db, a.symbol),
            "ivr": get_latest_ivr(db, a.symbol),
            "range_pct_3m": get_monthly_range_pct(db, a.symbol),
        }

        try:
            found, event = cal.has_results_before_expiry(a.symbol, series_expiry, today=as_of)
            ctx["results_before_expiry"] = event.to_dict() if found else None
        except Exception as e:
            logger.warning("Corporate events check failed for %s: %s", a.symbol, e)

        try:
            intel_summary = scanner.scan_symbol(a.symbol)
            ctx["market_intel"] = {
                "is_blocking": intel_summary.is_blocking,
                "bearish_count": intel_summary.bearish_count,
                "bullish_count": intel_summary.bullish_count,
                "action": intel_summary.action,
            }
        except Exception as e:
            logger.warning("Market intel check failed for %s: %s", a.symbol, e)

        results = [
            engine.evaluate_rule(rule, None, ctx)
            for rule in rules_by_id.values()
        ]

        verdict = SymbolVerdict(
            symbol=a.symbol, tier=a.tier, win_pct=a.target_win_pct,
            verdict=_verdict_from_results(results), rule_results=results,
        )

        if verdict.verdict == "ENTER":
            plan.enter.append(verdict)
        elif verdict.verdict == "CAUTION":
            plan.caution.append(verdict)
        else:
            plan.avoid.append(verdict)

    return plan
