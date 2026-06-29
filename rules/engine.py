"""
rules/engine.py
───────────────────
The rule evaluator. Takes the per-user effective rule list (from
rules_service.get_effective_rules) and a live Strangle/context, and
returns a structured result per rule.

THREE-WAY STATUS, NOT JUST PASS/FAIL:
  Every rule's eval_status (set in seed_rules.py) determines how it's
  evaluated here:
    EVALUABLE          → dispatched to a real handler function below,
                          checked against actual Strangle/context data.
    ADVISORY            → always returns an informational reminder —
                          these are qualitative/operational rules with
                          no market-data check to run (e.g. "set GTT
                          orders within 2 hours").
    NOT_YET_EVALUABLE  → returns that status verbatim, carrying the
                          specific reason from seed_rules.py (VIX feed
                          doesn't exist, corporate_events_cache is
                          empty, etc.) — never silently skipped, never
                          faked as a pass.

CONTEXT SCHEMA — passed alongside the Strangle for checks that need
more than current position state (entry-time checks, time-of-day):
  as_of               : date       — defaults to today
  as_of_datetime      : datetime   — defaults to now
  is_new_entry        : bool       — True when evaluating a proposed
                                      brand-new strangle, not an
                                      existing open position
  proposed_ce_strike  : float | None
  proposed_pe_strike  : float | None
  intends_new_short    : bool       — True when the candidate action
                                      being checked is selling a new
                                      short leg (relevant to A-07)

A handler that needs a context field which wasn't supplied returns
ADVISORY (not FAIL) explaining what's missing — never guesses.

PROJECT PATH:  rules/engine.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from dashboard.strangle_grouper import Strangle
from rules import series_calendar as cal


# ── Result type ───────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule_id:    str
    name:       str
    status:     str   # "PASS" | "FAIL" | "WARN" | "ADVISORY" | "NOT_YET_EVALUABLE"
    message:    str
    severity:   str = "MEDIUM"


def _default_context() -> dict:
    now = datetime.now()
    return {
        "as_of": now.date(),
        "as_of_datetime": now,
        "is_new_entry": False,
        "proposed_ce_strike": None,
        "proposed_pe_strike": None,
        "intends_new_short": False,
        # Step 5 — market data, fetched by the caller (engine.py stays
        # decoupled from Database; see market_data/ for how these get
        # populated). None means "not supplied this call", handled the
        # same as any other missing-context case — see _missing_context_result.
        "vix": None,                    # current India VIX (float)
        "vix_5day_readings": None,      # list[float], oldest first, for S-02's trend check
        "vix_intraday_readings": None,  # list[float], oldest first, for EP-04's spike check
        "ivr": None,                    # IV Rank for the symbol being evaluated (S-08)
        "beta": None,                   # beta vs Nifty for the symbol (S-07)
        "range_pct_3m": None,           # 3-month high-low range as % of avg close (S-06)
        # Step 7 — pre-fetched corporate event / market intel for the
        # underlying being checked (see agent/tools.py for how these
        # get populated). Same decoupled pattern as VIX/IVR/beta above
        # — the engine never reaches into Database or the network
        # itself, the caller fetches first.
        "corporate_event": None,        # dict: {action, rule_triggered, description, days_away} or None
        "market_intel": None,           # dict: {is_blocking, bearish_count, bullish_count, action} or None
        "results_before_expiry": None,  # dict (event) or None - for S-27's whole-series check
    }


# ── Generic handling for ADVISORY / NOT_YET_EVALUABLE rules ───────────────

def _advisory_result(rule: dict) -> RuleResult:
    return RuleResult(
        rule_id=rule["rule_id"], name=rule["name"], status="ADVISORY",
        message=rule["description"], severity=rule.get("severity", "MEDIUM"),
    )


def _not_yet_evaluable_result(rule: dict) -> RuleResult:
    reason = rule.get("not_evaluable_reason", "Required data source not yet built.")
    return RuleResult(
        rule_id=rule["rule_id"], name=rule["name"], status="NOT_YET_EVALUABLE",
        message=reason, severity=rule.get("severity", "MEDIUM"),
    )


def _missing_context_result(rule: dict, missing_field: str) -> RuleResult:
    return RuleResult(
        rule_id=rule["rule_id"], name=rule["name"], status="ADVISORY",
        message=f"Not evaluated this time — needs context field '{missing_field}', "
                f"which wasn't provided (e.g. this check only applies at new entry "
                f"or re-entry, not to an already-open position).",
        severity=rule.get("severity", "MEDIUM"),
    )


# ── Shared small helpers ───────────────────────────────────────────────────

def _short_legs(legs: list) -> list:
    return [l for l in legs if l.is_short]


def _long_legs(legs: list) -> list:
    return [l for l in legs if not l.is_short]


def _is_final_week(as_of: date) -> bool:
    return cal.get_week_number(as_of) == cal.get_week_count(as_of)


# ── EVALUABLE rule handlers (the 18 with real checks today) ──────────────

def range_breach(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """ES-01 — close ONLY the breached leg, not the whole strangle."""
    if s.ce_strike and s.spot and s.ce_distance_pct < 0:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"CE strike {s.ce_strike} breached (spot {s.spot}) — close the CE leg.",
                          rule["severity"])
    if s.pe_strike and s.spot and s.pe_distance_pct < 0:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"PE strike {s.pe_strike} breached (spot {s.spot}) — close the PE leg.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No strike breached.", rule["severity"])


def leg_delta_warning(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """ES-04 — warning only, never an automatic exit."""
    flagged = [l for l in s.ce_legs + s.pe_legs
              if l.delta_info.get("delta") is not None and abs(l.delta_info["delta"]) > 0.30]
    if flagged:
        names = ", ".join(f"{l.option_type} {l.strike} (Δ={l.delta_info['delta']:+.3f})" for l in flagged)
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"Leg(s) exceeding 0.30 delta: {names}. Review — not an automatic exit.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No leg exceeds 0.30 delta.", rule["severity"])


def early_delta_trigger(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """A-03 — proactive middle step between normal operation and A-10's cap."""
    flagged = [l for l in s.ce_legs + s.pe_legs
              if l.delta_info.get("delta") is not None and abs(l.delta_info["delta"]) > 0.25]
    if flagged:
        names = ", ".join(f"{l.option_type} {l.strike} (Δ={l.delta_info['delta']:+.3f})" for l in flagged)
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"Leg(s) crossing 0.25 delta, consider adjusting: {names}",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No leg above 0.25 delta.", rule["severity"])


def delta_neutral_after_adjustment(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """
    A-05 — deliberately has no strict numeric pass/fail threshold (the
    rule itself says "as close as practical," not exact zero). Reports
    the current net delta as information, doesn't fabricate a cutoff.
    """
    nd = s.net_position_delta
    if nd is None:
        return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                          "Net position delta unavailable (Greeks not computed for all legs).",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                      f"Net position delta = {nd:+.1f} ({s.delta_status}). "
                      f"No strict zero target — review qualitatively.",
                      rule["severity"])


def max_ce_pe_ratio(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """A-10 — live snapshot only, not adjustment history. Doesn't apply if one side is naked (see A-11)."""
    ce_n, pe_n = len(s.ce_legs), len(s.pe_legs)
    if ce_n == 0 or pe_n == 0:
        return RuleResult(rule["rule_id"], rule["name"], "PASS",
                          "One side has zero legs (naked) — governed by A-11, not this ratio check.",
                          rule["severity"])
    ratio_ok = max(ce_n, pe_n) <= 3 * min(ce_n, pe_n)
    if not ratio_ok:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"CE:PE leg ratio {ce_n}:{pe_n} breaches the 3:1 cap — "
                          f"block further same-side adds (see ES-10).",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"CE:PE leg ratio {ce_n}:{pe_n} within 3:1 cap.", rule["severity"])


def naked_position_exception(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """A-11 — naked CE tolerated only in the final week; naked PE not preferred."""
    ce_n, pe_n = len(s.ce_legs), len(s.pe_legs)
    if ce_n > 0 and pe_n > 0:
        return RuleResult(rule["rule_id"], rule["name"], "PASS", "Not naked — both sides open.", rule["severity"])
    if ce_n == 0 and pe_n == 0:
        return RuleResult(rule["rule_id"], rule["name"], "PASS", "No legs open at all.", rule["severity"])

    as_of = ctx.get("as_of", date.today())
    final_week = _is_final_week(as_of)
    naked_side = "CE" if pe_n == 0 else "PE"

    if naked_side == "PE":
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          "Naked PE — not preferred (downside risk moves faster than upside). "
                          "Consider re-strangling or hedging.", rule["severity"])
    if not final_week:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Naked CE outside the final week (week {cal.get_week_number(as_of)} "
                          f"of {cal.get_week_count(as_of)}) — hard gate not met. "
                          f"Re-strangle or close.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      "Naked CE in final week — acceptable exception.", rule["severity"])


def leg_count_hard_cap(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """L-03 — absorbed L-02; the number 8 derives from A-10's 3:1 ratio."""
    total = len(s.ce_legs) + len(s.pe_legs)
    if total > 8:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"{total} total legs exceeds the hard cap of 8 — emergency close required.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"{total} total legs, within hard cap.", rule["severity"])


def same_expiry_check(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """L-05 — true by construction (strangle_grouper groups by underlying+expiry)."""
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      "CE and PE legs share one expiry by construction.", rule["severity"])


def itm_overnight(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """
    RF-02 — flags any currently-ITM short leg. The "overnight" TIMING
    trigger is an operational/scheduling concern (Step 8's EOD job),
    not this function's job — this checks ITM state whenever it's run.
    """
    itm_legs = []
    for l in _short_legs(s.ce_legs):
        if s.spot and l.strike < s.spot:
            itm_legs.append(l)
    for l in _short_legs(s.pe_legs):
        if s.spot and l.strike > s.spot:
            itm_legs.append(l)
    if itm_legs:
        names = ", ".join(f"{l.option_type} {l.strike}" for l in itm_legs)
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"Short leg(s) currently ITM: {names} — should not hold ITM short overnight.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No short leg currently ITM.", rule["severity"])


def ratio_breach_exit(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """ES-10 — fires when already at the 3:1 cap AND delta is still skewed."""
    ce_n, pe_n = len(s.ce_legs), len(s.pe_legs)
    at_cap = ce_n > 0 and pe_n > 0 and max(ce_n, pe_n) == 3 * min(ce_n, pe_n)
    if at_cap and s.delta_status.startswith("SKEWED"):
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"At 3:1 ratio cap ({ce_n}:{pe_n}) and delta status is {s.delta_status} — "
                          f"cannot rebalance further. Exit, or see ES-11 for a hedge alternative.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      "Not stuck at the ratio cap with skewed delta.", rule["severity"])


def capping_hedge_alternative(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """ES-11 — only surfaces as a live alternative once ES-10's condition is true."""
    ce_n, pe_n = len(s.ce_legs), len(s.pe_legs)
    at_cap = ce_n > 0 and pe_n > 0 and max(ce_n, pe_n) == 3 * min(ce_n, pe_n)
    if at_cap and s.delta_status.startswith("SKEWED"):
        stuck_side = "CE" if ce_n > pe_n else "PE"
        return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                          f"ES-10 has triggered on the {stuck_side} side — instead of exiting, "
                          f"consider buying one further-OTM long {stuck_side} as a capping hedge "
                          f"(doesn't count against the 3:1 cap, per L-06's precedent).",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "ES-10 hasn't triggered — no hedge decision needed.", rule["severity"])


def tiered_leg_closure(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """EP-01 — per-leg premium-decay trigger, scaled by week of series."""
    as_of = ctx.get("as_of", date.today())
    required_pct = cal.required_profit_pct(as_of)
    flagged = []
    for l in s.ce_legs + s.pe_legs:
        if l.avg_price <= 0:
            continue
        decay_pct = (1 - l.ltp / l.avg_price) * 100
        if decay_pct >= required_pct:
            flagged.append((l, decay_pct))
    if flagged:
        names = ", ".join(f"{l.option_type} {l.strike} (decayed {d:.0f}%)" for l, d in flagged)
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Week {cal.get_week_number(as_of)} threshold is {required_pct:.0f}% — "
                          f"close entire quantity on: {names}. Resell per A-02.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"No leg has decayed to this week's {required_pct:.0f}% threshold.", rule["severity"])


def final_exit_window(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """EP-02 — close everything by last Friday/Monday of series, after 2PM."""
    as_of_dt = ctx.get("as_of_datetime", datetime.now())
    if cal.is_last_friday_or_monday_post_2pm(as_of_dt) and (s.ce_legs or s.pe_legs):
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          "Final exit window reached (Fri/Mon post-2PM) — close all remaining legs now.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "Not yet in the final exit window.", rule["severity"])


def no_new_shorts_final_sessions(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """A-07 — blocks NEW short legs in the final 3 sessions; existing legs unaffected."""
    if not ctx.get("intends_new_short"):
        return _missing_context_result(rule, "intends_new_short")
    as_of = ctx.get("as_of", date.today())
    if cal.is_final_n_sessions(as_of, 3):
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          "Within final 3 sessions before expiry — no new short legs.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "Outside the final-3-session window.", rule["severity"])


def reentry_strike_otm(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """A-08 — re-entry strike must be week-aware OTM% from CURRENT spot, not original entry spot."""
    proposed = ctx.get("proposed_ce_strike") or ctx.get("proposed_pe_strike")
    if proposed is None or not s.spot:
        return _missing_context_result(rule, "proposed_ce_strike / proposed_pe_strike")
    as_of = ctx.get("as_of", date.today())
    required_pct = cal.required_otm_pct(as_of)
    actual_pct = abs(proposed - s.spot) / s.spot * 100
    if actual_pct < required_pct:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Proposed re-entry strike {proposed} is only {actual_pct:.1f}% OTM "
                          f"from current spot {s.spot} — week {cal.get_week_number(as_of)} requires {required_pct:.0f}%.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"Proposed strike {actual_pct:.1f}% OTM, meets week-{cal.get_week_number(as_of)} "
                      f"requirement of {required_pct:.0f}%.", rule["severity"])


def strike_distance_schedule(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-26 — same week-aware OTM% schedule, applied to a brand-new entry."""
    if not ctx.get("is_new_entry"):
        return _missing_context_result(rule, "is_new_entry")
    ce_k, pe_k = ctx.get("proposed_ce_strike"), ctx.get("proposed_pe_strike")
    if ce_k is None or pe_k is None or not s.spot:
        return _missing_context_result(rule, "proposed_ce_strike and proposed_pe_strike")
    as_of = ctx.get("as_of", date.today())
    required_pct = cal.required_otm_pct(as_of)
    ce_pct = abs(ce_k - s.spot) / s.spot * 100
    pe_pct = abs(s.spot - pe_k) / s.spot * 100
    if ce_pct < required_pct or pe_pct < required_pct:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Week {cal.get_week_number(as_of)} requires {required_pct:.0f}% OTM both sides — "
                          f"CE is {ce_pct:.1f}%, PE is {pe_pct:.1f}%.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"Both strikes meet week-{cal.get_week_number(as_of)}'s {required_pct:.0f}% OTM requirement.",
                      rule["severity"])


def entry_timing_window(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-12 — entry permitted from first Wednesday of series onward, not before 12PM."""
    if not ctx.get("is_new_entry"):
        return _missing_context_result(rule, "is_new_entry")
    as_of_dt = ctx.get("as_of_datetime", datetime.now())
    if cal.is_entry_time_permitted(as_of_dt):
        return RuleResult(rule["rule_id"], rule["name"], "PASS", "Within the permitted entry window.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                      "Before first Wednesday 12PM of the series — entry not yet permitted.", rule["severity"])


def delta_neutral_at_entry(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-14 — net delta within +/-0.05 AT ENTRY (has a real numeric threshold, unlike A-05)."""
    if not ctx.get("is_new_entry"):
        return _missing_context_result(rule, "is_new_entry")
    nd = s.net_position_delta
    if nd is None:
        return RuleResult(rule["rule_id"], rule["name"], "ADVISORY", "Net delta unavailable.", rule["severity"])
    if abs(nd) > 0.05:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Net delta {nd:+.3f} exceeds +/-0.05 at entry.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"Net delta {nd:+.3f}, within +/-0.05.", rule["severity"])


def exactly_two_legs_at_entry(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """L-01 — a new strangle starts as exactly 1 CE + 1 PE."""
    if not ctx.get("is_new_entry"):
        return _missing_context_result(rule, "is_new_entry")
    if len(s.ce_legs) == 1 and len(s.pe_legs) == 1:
        return RuleResult(rule["rule_id"], rule["name"], "PASS", "Exactly 1 CE + 1 PE.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                      f"New strangle has {len(s.ce_legs)} CE + {len(s.pe_legs)} PE — must be exactly 1+1.",
                      rule["severity"])


# ── Step 5 handlers — VIX-based ───────────────────────────────────────────

def vix_hard_limit(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-01 — never enter when India VIX >= 25."""
    vix = ctx.get("vix")
    if vix is None:
        return _missing_context_result(rule, "vix")
    if vix >= 25:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL", f"VIX {vix:.1f} >= 25 — block entry.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"VIX {vix:.1f}, below the 25 hard limit.", rule["severity"])


def vix_slope(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-02 — VIX 20-25 band with a >3pt rise over the last 5 days = caution zone."""
    vix = ctx.get("vix")
    readings = ctx.get("vix_5day_readings")
    if vix is None or not readings or len(readings) < 2:
        return _missing_context_result(rule, "vix and vix_5day_readings")
    rise = readings[-1] - readings[0]
    if 20 <= vix <= 25 and rise > 3:
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"VIX {vix:.1f} in 20-25 band, risen {rise:+.1f}pts over 5 days — caution zone.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"VIX {vix:.1f}, 5-day change {rise:+.1f}pts — not in caution zone.", rule["severity"])


def vix_spike_exit(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """EP-04 — exit everything if VIX rises >5pts within the same session."""
    readings = ctx.get("vix_intraday_readings")
    if not readings or len(readings) < 2:
        return _missing_context_result(rule, "vix_intraday_readings")
    spike = readings[-1] - readings[0]
    if spike > 5:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"VIX spiked {spike:+.1f}pts intraday — exit all positions now.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"Intraday VIX change {spike:+.1f}pts — no spike.", rule["severity"])


def high_iv_entry_protocol(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-15 — VIX 20-25 band needs a wider 12-13% range + protective put."""
    vix = ctx.get("vix")
    if vix is None:
        return _missing_context_result(rule, "vix")
    if 20 <= vix <= 25:
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"VIX {vix:.1f} in 20-25 band — use 12-13% OTM range and consider a protective put.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"VIX {vix:.1f}, outside the high-IV band.", rule["severity"])


# ── Step 5 handlers — market data based (IVR, beta, range%) ──────────────

def iv_rank_check(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-08 — only sell premium when IV Rank > 40."""
    ivr = ctx.get("ivr")
    if ivr is None:
        return _missing_context_result(rule, "ivr")
    if ivr <= 40:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL", f"IVR {ivr:.1f} <= 40 — premium too cheap to sell.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"IVR {ivr:.1f} > 40 — premium is rich enough to sell.", rule["severity"])


def beta_check(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-07 — only trade stocks with beta < 1.2 vs Nifty."""
    beta = ctx.get("beta")
    if beta is None:
        return _missing_context_result(rule, "beta")
    if beta >= 1.2:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL", f"Beta {beta:.2f} >= 1.2 — too volatile relative to Nifty.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", f"Beta {beta:.2f} < 1.2.", rule["severity"])


def range_bound_check(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """
    S-06 — range-bound confirmation. Reports the actual 3-month
    high-low range %, but as ADVISORY rather than a strict pass/fail —
    no specific threshold was ever defined for "range-bound enough"
    (this was always meant to be a visual/qualitative judgment call,
    per the original rule review), so this surfaces the number for the
    trader to judge rather than inventing a cutoff.
    """
    range_pct = ctx.get("range_pct_3m")
    if range_pct is None:
        return _missing_context_result(rule, "range_pct_3m")
    return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                      f"3-month high-low range is {range_pct:.1f}% of average close — "
                      f"judge range-bound-ness visually, no fixed cutoff defined.",
                      rule["severity"])


# ── Step 7 handlers — corporate events ────────────────────────────────────
# All check the SAME pre-fetched ctx["corporate_event"] dict, looking for
# whether ITS rule_triggered matches the rule being evaluated — the
# actual classification (which rule_id applies) already happened in
# corporate_events/event_classifier.py before this ever runs.

def _corporate_event_check(rule: dict, ctx: dict, expected_rule_id: str) -> RuleResult:
    if "corporate_event" not in ctx:
        return _missing_context_result(rule, "corporate_event")
    event = ctx.get("corporate_event")
    if event is not None and event.get("rule_triggered") == expected_rule_id:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"{event.get('description')} in {event.get('days_away')} day(s) — "
                          f"{event.get('action')}.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      "No blocking corporate event for this rule.", rule["severity"])


def no_entry_near_results(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-21 — no entry within 5 trading days of quarterly/annual/half-yearly results."""
    return _corporate_event_check(rule, ctx, "S-21")


def no_entry_on_merger(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-22 — block entry if M&A/merger/demerger announced."""
    return _corporate_event_check(rule, ctx, "S-22")


def no_entry_near_split_bonus(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-23 — no entry within 3 days of split/bonus ex-date."""
    return _corporate_event_check(rule, ctx, "S-23")


def reduce_size_results_week(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-24 — halve lot size if results are 6-7 days away."""
    if "corporate_event" not in ctx:
        return _missing_context_result(rule, "corporate_event")
    event = ctx.get("corporate_event")
    if event is not None and event.get("rule_triggered") == "S-24":
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"{event.get('description')} in {event.get('days_away')} day(s) — "
                          f"reduce position size 50%.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "Not in results week.", rule["severity"])


def monitor_board_meeting(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """M-09 — informational only, never blocks. Board meeting/AGM within 7 days."""
    if "corporate_event" not in ctx:
        return _missing_context_result(rule, "corporate_event")
    event = ctx.get("corporate_event")
    if event is not None and event.get("rule_triggered") == "M-09":
        return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                          f"{event.get('description')} in {event.get('days_away')} day(s) — "
                          f"informational, not blocking.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No event to monitor.", rule["severity"])


def exit_on_same_day_merger(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """ES-09 — exit an OPEN position immediately if merger/demerger announced same day."""
    if "corporate_event" not in ctx:
        return _missing_context_result(rule, "corporate_event")
    event = ctx.get("corporate_event")
    if event is not None and event.get("rule_triggered") == "ES-09":
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"{event.get('description')} announced TODAY — exit this position now.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No same-day merger/demerger.", rule["severity"])


def no_entry_results_before_expiry(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """
    S-27 — whole-series block, not a narrow timing window like S-21.
    If results fall ANYWHERE between today and this series' expiry,
    block entry (and re-entry/adjustment) for this stock for the rest
    of the series — entering earlier in the month doesn't avoid the
    risk, since the position would still be open when results hit.
    """
    if "results_before_expiry" not in ctx:
        return _missing_context_result(rule, "results_before_expiry")
    event = ctx.get("results_before_expiry")
    if event is not None:
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"Results ({event.get('description')}) fall on {event.get('event_date')}, "
                          f"before this series' expiry — do not enter, re-enter, or adjust this stock "
                          f"for the rest of the series.", rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      "No results scheduled before this series' expiry.", rule["severity"])


# ── Step 7 handlers — market intelligence ─────────────────────────────────

def block_on_bearish_calls(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """S-25 — block entry if 3+ distinct bearish brokerage calls in recent search."""
    intel = ctx.get("market_intel")
    if intel is None:
        return _missing_context_result(rule, "market_intel")
    if intel.get("is_blocking"):
        return RuleResult(rule["rule_id"], rule["name"], "FAIL",
                          f"{intel.get('bearish_count', 0)} bearish brokerage call(s) found — block entry.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS",
                      f"{intel.get('bearish_count', 0) if intel else 0} bearish call(s), below the block threshold.",
                      rule["severity"])


def warn_bearish_signal(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """M-11 — warn (don't block) before entry if any bearish signal found."""
    intel = ctx.get("market_intel")
    if intel is None:
        return _missing_context_result(rule, "market_intel")
    if intel.get("bearish_count", 0) > 0:
        return RuleResult(rule["rule_id"], rule["name"], "WARN",
                          f"{intel.get('bearish_count')} bearish signal(s) found — review before entry.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No bearish signals found.", rule["severity"])


def review_sector_bearish_news(rule: dict, s: Strangle, ctx: dict) -> RuleResult:
    """M-12 — flag portfolio-wide review if sector-wide bearish news detected."""
    intel = ctx.get("market_intel")
    if intel is None:
        return _missing_context_result(rule, "market_intel")
    if intel.get("sector_bearish"):
        return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                          "Sector-wide bearish news detected — review all positions in this sector.",
                          rule["severity"])
    return RuleResult(rule["rule_id"], rule["name"], "PASS", "No sector-wide bearish news.", rule["severity"])


# ── Dispatch table ─────────────────────────────────────────────────────────

EVALUABLE_HANDLERS = {
    "range_breach": range_breach,
    "leg_delta_warning": leg_delta_warning,
    "early_delta_trigger": early_delta_trigger,
    "delta_neutral_after_adjustment": delta_neutral_after_adjustment,
    "max_ce_pe_ratio": max_ce_pe_ratio,
    "naked_position_exception": naked_position_exception,
    "leg_count_hard_cap": leg_count_hard_cap,
    "same_expiry_check": same_expiry_check,
    "itm_overnight": itm_overnight,
    "ratio_breach_exit": ratio_breach_exit,
    "capping_hedge_alternative": capping_hedge_alternative,
    "tiered_leg_closure": tiered_leg_closure,
    "final_exit_window": final_exit_window,
    "no_new_shorts_final_sessions": no_new_shorts_final_sessions,
    "reentry_strike_otm": reentry_strike_otm,
    "strike_distance_schedule": strike_distance_schedule,
    "entry_timing_window": entry_timing_window,
    "delta_neutral_at_entry": delta_neutral_at_entry,
    "exactly_two_legs_at_entry": exactly_two_legs_at_entry,
    "vix_hard_limit": vix_hard_limit,
    "vix_slope": vix_slope,
    "vix_spike_exit": vix_spike_exit,
    "high_iv_entry_protocol": high_iv_entry_protocol,
    "iv_rank_check": iv_rank_check,
    "beta_check": beta_check,
    "range_bound_check": range_bound_check,
    "no_entry_near_results": no_entry_near_results,
    "no_entry_on_merger": no_entry_on_merger,
    "no_entry_near_split_bonus": no_entry_near_split_bonus,
    "reduce_size_results_week": reduce_size_results_week,
    "monitor_board_meeting": monitor_board_meeting,
    "exit_on_same_day_merger": exit_on_same_day_merger,
    "no_entry_results_before_expiry": no_entry_results_before_expiry,
    "block_on_bearish_calls": block_on_bearish_calls,
    "warn_bearish_signal": warn_bearish_signal,
    "review_sector_bearish_news": review_sector_bearish_news,
}

# Handlers that check market-wide or candidate-symbol conditions rather
# than an existing position — none of these reference the `strangle`
# argument at all, so they're callable even when there's no open
# position yet (e.g. "is VIX safe to enter ANYTHING right now").
STRANGLE_OPTIONAL_HANDLERS = {
    "vix_hard_limit", "vix_slope", "vix_spike_exit", "high_iv_entry_protocol",
    "iv_rank_check", "beta_check", "range_bound_check",
    "no_entry_near_results", "no_entry_on_merger", "no_entry_near_split_bonus",
    "reduce_size_results_week", "monitor_board_meeting", "exit_on_same_day_merger",
    "no_entry_results_before_expiry",
    "block_on_bearish_calls", "warn_bearish_signal", "review_sector_bearish_news",
}


# ── Public API ─────────────────────────────────────────────────────────────

class RuleEngine:
    """
    Evaluates an effective rule list (from rules_service.get_effective_rules)
    against one Strangle + context. Custom rules (source="custom") use a
    simple generic metric/operator/value check against the Strangle.
    """

    def evaluate_rule(self, rule: dict, strangle: Optional[Strangle], context: Optional[dict] = None) -> RuleResult:
        ctx = {**_default_context(), **(context or {})}

        if not rule.get("enabled", True):
            return RuleResult(rule["rule_id"], rule["name"], "PASS", "Rule disabled by user.", rule.get("severity", "MEDIUM"))

        eval_status = rule.get("eval_status", "NOT_YET_EVALUABLE")

        if rule.get("source") == "custom":
            return self._evaluate_custom_rule(rule, strangle, ctx)

        if eval_status == "NOT_YET_EVALUABLE":
            return _not_yet_evaluable_result(rule)
        if eval_status == "ADVISORY":
            return _advisory_result(rule)

        handler_fn = EVALUABLE_HANDLERS.get(rule.get("handler", ""))
        if handler_fn is None:
            return RuleResult(rule["rule_id"], rule["name"], "NOT_YET_EVALUABLE",
                              f"No handler registered for '{rule.get('handler')}'.",
                              rule.get("severity", "MEDIUM"))
        if strangle is None and rule.get("handler") not in STRANGLE_OPTIONAL_HANDLERS:
            return _missing_context_result(rule, "strangle")
        return handler_fn(rule, strangle, ctx)

    def evaluate_all(self, rules: list[dict], strangle: Optional[Strangle], context: Optional[dict] = None) -> list[RuleResult]:
        return [self.evaluate_rule(r, strangle, context) for r in rules]

    @staticmethod
    def _evaluate_custom_rule(rule: dict, strangle: Optional[Strangle], ctx: dict) -> RuleResult:
        cd = rule.get("custom_def") or {}
        metric, op, value = cd.get("metric"), cd.get("operator"), cd.get("value")
        if strangle is None or metric is None:
            return _missing_context_result(rule, "strangle / custom_def.metric")

        metric_value = {
            "net_delta": strangle.net_position_delta,
            "worst_distance_pct": strangle.worst_distance_pct,
            "total_pnl": strangle.total_pnl,
        }.get(metric)

        if metric_value is None:
            return RuleResult(rule["rule_id"], rule["name"], "ADVISORY",
                              f"Custom metric '{metric}' not recognized or unavailable.", "MEDIUM")

        ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
              ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}
        triggered = ops.get(op, lambda a, b: False)(metric_value, value)
        status = "WARN" if triggered else "PASS"
        msg = (f"{metric}={metric_value:.2f} {op} {value} -> {cd.get('action', 'WARN')}"
              if triggered else f"{metric}={metric_value:.2f}, condition not met.")
        return RuleResult(rule["rule_id"], rule["name"], status, msg, "MEDIUM")
