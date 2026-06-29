"""
corporate_events/event_classifier.py
─────────────────────────────────────────
Classifies a raw NSE/BSE event string into EventType, ImpactLevel,
EventAction, and a rule ID. Ported from the real POC-11 implementation.

RULES IMPLEMENTED:
  S-21:  No entry within 5 trading days of quarterly/annual/half-yearly results
  S-22:  Block entry if M&A / merger / demerger announced
  S-23:  No entry within 3 days of split or bonus ex-date
  S-24:  Reduce size 50% in results week (6-7 days before results)
  M-09:  Monitor — board meeting, AGM, or rights issue within 7 days
  ES-09: Exit open position if same-day merger/demerger announced
         (renamed from POC-11's ES-06 — that ID was already taken by
         the original rule book's "no re-entry after stop-loss")

PROJECT PATH:  corporate_events/event_classifier.py
"""

from __future__ import annotations

import re
from datetime import date

from corporate_events.event_models import (
    CorporateEvent, EventAction, EventType, ImpactLevel,
)

_RESULTS_KEYWORDS  = ["quarterly results", "q1", "q2", "q3", "q4",
                      "annual results", "half year", "half yearly",
                      "financial results", "unaudited results"]
_BOARD_KEYWORDS    = ["board meeting", "board of directors"]
_AGM_KEYWORDS      = ["agm", "annual general meeting", "egm",
                      "extraordinary general meeting"]
_SPLIT_KEYWORDS    = ["stock split", "split", "face value"]
_BONUS_KEYWORDS    = ["bonus"]
_DIVIDEND_KEYWORDS = ["dividend", "interim dividend", "final dividend"]
_BUYBACK_KEYWORDS  = ["buyback", "buy back", "repurchase"]
_RIGHTS_KEYWORDS   = ["rights issue", "rights entitlement"]
_MERGER_KEYWORDS   = ["merger", "amalgamation", "acquisition",
                      "takeover", "scheme of arrangement"]
_DEMERGER_KEYWORDS = ["demerger", "spin-off", "spinoff", "spin off",
                      "hive off", "slump sale", "restructuring"]


def parse_event_type(description: str) -> EventType:
    """
    Order matters here MORE than the original POC-11 comment implied:
    NSE often combines purposes into one string, e.g. "Financial
    Results/Dividend" — both keywords genuinely present at once. The
    higher-risk classification must win in that case, not whichever
    keyword happens to be checked first. Confirmed via real NSE data
    (a live TCS results announcement was being misclassified as a
    plain DIVIDEND, completely missing the actual results risk S-21
    exists to catch) — results-related keywords are now checked
    before dividend/buyback/rights, matching the real risk ordering
    (results = HIGH impact, dividend/buyback/rights = MEDIUM).
    """
    desc = description.lower().strip()

    if any(k in desc for k in _MERGER_KEYWORDS):   return EventType.MERGER
    if any(k in desc for k in _DEMERGER_KEYWORDS): return EventType.DEMERGER

    # Results checked BEFORE dividend/buyback/rights/split/bonus — a
    # combined "Results/Dividend" string is a results event first.
    if any(k in desc for k in _RESULTS_KEYWORDS):
        if "annual" in desc: return EventType.ANNUAL_RESULTS
        if "half" in desc:   return EventType.HALF_YEARLY
        return EventType.QUARTERLY_RESULTS

    if any(k in desc for k in _BUYBACK_KEYWORDS):  return EventType.BUYBACK
    if any(k in desc for k in _RIGHTS_KEYWORDS):   return EventType.RIGHTS_ISSUE
    if any(k in desc for k in _SPLIT_KEYWORDS):    return EventType.SPLIT
    if any(k in desc for k in _BONUS_KEYWORDS):    return EventType.BONUS
    if any(k in desc for k in _DIVIDEND_KEYWORDS): return EventType.DIVIDEND

    if any(k in desc for k in _AGM_KEYWORDS):   return EventType.AGM
    if any(k in desc for k in _BOARD_KEYWORDS): return EventType.BOARD_MEETING

    return EventType.OTHER


def classify_impact(event_type: EventType) -> ImpactLevel:
    _CRITICAL = {EventType.MERGER, EventType.DEMERGER}
    _HIGH = {EventType.QUARTERLY_RESULTS, EventType.ANNUAL_RESULTS,
             EventType.SPLIT, EventType.BONUS, EventType.HALF_YEARLY}
    _MEDIUM = {EventType.BUYBACK, EventType.RIGHTS_ISSUE,
               EventType.BOARD_MEETING, EventType.DIVIDEND}

    if event_type in _CRITICAL: return ImpactLevel.CRITICAL
    if event_type in _HIGH:     return ImpactLevel.HIGH
    if event_type in _MEDIUM:   return ImpactLevel.MEDIUM
    return ImpactLevel.LOW


def classify_action(event_type: EventType, days_away: int) -> tuple[EventAction, str]:
    """Returns (EventAction, rule_id)."""
    _RESULTS_TYPES = {EventType.QUARTERLY_RESULTS, EventType.ANNUAL_RESULTS, EventType.HALF_YEARLY}

    if event_type in {EventType.MERGER, EventType.DEMERGER}:
        if days_away <= 0:
            return EventAction.EXIT_IF_OPEN, "ES-09"   # renamed from POC-11's ES-06
        return EventAction.BLOCK_ENTRY, "S-22"

    if event_type in _RESULTS_TYPES:
        if days_away <= 5:
            return EventAction.BLOCK_ENTRY, "S-21"
        if days_away <= 7:
            return EventAction.REDUCE_SIZE, "S-24"

    if event_type in {EventType.SPLIT, EventType.BONUS}:
        if days_away <= 3:
            return EventAction.BLOCK_ENTRY, "S-23"

    if 0 <= days_away <= 7:
        return EventAction.MONITOR, "M-09"

    return EventAction.CLEAR, ""


def classify_event(
    symbol: str, description: str, event_date: date,
    today: date | None = None, ex_date: date | None = None, source: str = "NSE",
) -> CorporateEvent:
    today = today or date.today()
    reference_date = ex_date if ex_date else event_date
    days_away = (reference_date - today).days

    event_type = parse_event_type(description)
    impact = classify_impact(event_type)
    action, rule = classify_action(event_type, days_away)

    return CorporateEvent(
        symbol=symbol.upper(), event_type=event_type, event_date=event_date,
        description=_clean_description(description), impact=impact,
        days_away=days_away, action=action, rule_triggered=rule,
        ex_date=ex_date, source=source,
    )


def _clean_description(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw.strip())
    return cleaned[:80] if len(cleaned) > 80 else cleaned
