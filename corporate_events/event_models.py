"""
corporate_events/event_models.py
─────────────────────────────────────
Dataclasses and enums for corporate events. Ported from the real
POC-11 implementation — this is the same code, not rebuilt from
scratch.

ONE CHANGE FROM POC-11: the merger/demerger same-day exit rule is
ES-09 here, not ES-06 — Step 3's rule review renamed it to avoid a
collision with the original rule book's own ES-06 ("no re-entry after
stop-loss"), which predates this module and means something unrelated.

PROJECT PATH:  corporate_events/event_models.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    QUARTERLY_RESULTS = "QUARTERLY_RESULTS"
    ANNUAL_RESULTS    = "ANNUAL_RESULTS"
    HALF_YEARLY       = "HALF_YEARLY"
    BOARD_MEETING     = "BOARD_MEETING"
    AGM               = "AGM"
    SPLIT             = "SPLIT"
    BONUS             = "BONUS"
    DIVIDEND          = "DIVIDEND"
    BUYBACK           = "BUYBACK"
    RIGHTS_ISSUE      = "RIGHTS_ISSUE"
    MERGER            = "MERGER"
    DEMERGER          = "DEMERGER"
    OTHER             = "OTHER"


class ImpactLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


class EventAction(str, Enum):
    BLOCK_ENTRY  = "BLOCK_ENTRY"
    REDUCE_SIZE  = "REDUCE_SIZE_50PCT"
    EXIT_IF_OPEN = "EXIT_IF_OPEN"
    MONITOR      = "MONITOR"
    CLEAR        = "CLEAR"


IMPACT_EMOJI: dict[ImpactLevel, str] = {
    ImpactLevel.CRITICAL: "🔴", ImpactLevel.HIGH: "🟠",
    ImpactLevel.MEDIUM: "🟡", ImpactLevel.LOW: "🟢",
}

ACTION_EMOJI: dict[EventAction, str] = {
    EventAction.BLOCK_ENTRY: "🚫", EventAction.REDUCE_SIZE: "⚠️",
    EventAction.EXIT_IF_OPEN: "🚨", EventAction.MONITOR: "👁️", EventAction.CLEAR: "✅",
}


@dataclass
class CorporateEvent:
    """Single corporate event for one symbol."""

    symbol:         str
    event_type:     EventType
    event_date:     date
    description:    str
    impact:         ImpactLevel
    days_away:      int
    action:         EventAction
    rule_triggered: str
    ex_date:        Optional[date] = None
    source:         str = "NSE"   # NSE | BSE | MOCK

    @property
    def is_blocking(self) -> bool:
        return self.action in (EventAction.BLOCK_ENTRY, EventAction.EXIT_IF_OPEN)

    @property
    def emoji(self) -> str:
        return IMPACT_EMOJI.get(self.impact, "⚪")

    @property
    def action_emoji(self) -> str:
        return ACTION_EMOJI.get(self.action, "")

    def to_agent_line(self) -> str:
        return (
            f"  {self.emoji} [{self.days_away}d] {self.symbol} — "
            f"{self.event_type.value}: {self.description} "
            f"| Impact: {self.impact.value} | Action: {self.action.value} "
            f"| Rule: {self.rule_triggered}"
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "event_type": self.event_type.value,
            "event_date": self.event_date.isoformat(), "description": self.description,
            "impact": self.impact.value, "days_away": self.days_away,
            "action": self.action.value, "rule_triggered": self.rule_triggered,
            "ex_date": self.ex_date.isoformat() if self.ex_date else None,
            "source": self.source,
        }


@dataclass
class EventSummary:
    """Aggregated event picture for all symbols or a single symbol."""

    symbol:          str
    events:          list[CorporateEvent] = field(default_factory=list)
    has_critical:    bool = False
    has_high:        bool = False
    blocking_events: list[CorporateEvent] = field(default_factory=list)
    next_event:      Optional[CorporateEvent] = None
    as_of:           Optional[date] = None

    def __post_init__(self):
        if self.events:
            self.has_critical = any(e.impact == ImpactLevel.CRITICAL for e in self.events)
            self.has_high = any(e.impact == ImpactLevel.HIGH for e in self.events)
            self.blocking_events = [e for e in self.events if e.is_blocking]
            upcoming = [e for e in self.events if e.days_away >= 0]
            self.next_event = min(upcoming, key=lambda e: e.days_away) if upcoming else None

    def to_agent_text(self) -> str:
        if not self.events:
            sym = self.symbol if self.symbol != "ALL" else "any approved instrument"
            return f"No upcoming corporate events found for {sym} in the next 14 days. ✅ Clear."

        lines = [f"Corporate Events for {self.symbol} (next 14 days):"]
        for e in sorted(self.events, key=lambda x: x.days_away):
            lines.append(e.to_agent_line())

        if self.blocking_events:
            lines.append("")
            lines.append("⚠️  BLOCKING EVENTS — check before entry:")
            for e in self.blocking_events:
                lines.append(f"  Rule {e.rule_triggered}: {e.action.value} — {e.description}")

        return "\n".join(lines)
