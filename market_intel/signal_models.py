"""
market_intel/signal_models.py
───────────────────────────────────
Dataclasses for classified market intelligence signals. Built to match
exactly what intel_scanner.py (ported verbatim from the real POC-12
implementation) expects — IntelSummary's properties and MarketSignal's
fields are used as-is by that file, not redesigned here.

PROJECT PATH:  market_intel/signal_models.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Sentiment(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SignalType(str, Enum):
    BROKERAGE_REPORT  = "BROKERAGE_REPORT"
    COMPANY_NEWS      = "COMPANY_NEWS"
    SECTOR_NEWS       = "SECTOR_NEWS"
    PROMOTER_ACTIVITY = "PROMOTER_ACTIVITY"
    FII_DII           = "FII_DII"
    OTHER             = "OTHER"


@dataclass
class MarketSignal:
    """One classified search result."""

    symbol:      str
    title:       str
    url:         str
    sentiment:   Sentiment
    summary:     str
    signal_type: SignalType
    source:      str = ""             # domain, e.g. "moneycontrol.com"
    broker_name: Optional[str] = None  # extracted broker name, for S-25's dedup
    published:   str = ""

    def to_agent_line(self) -> str:
        return f"  [{self.sentiment.value}] {self.title[:80]}"


# Action thresholds — S-25's "3+ bearish brokerage calls" rule
BEARISH_BLOCK_THRESHOLD = 3


@dataclass
class IntelSummary:
    """Aggregated market intelligence for one symbol."""

    symbol:  str = ""
    signals: list[MarketSignal] = field(default_factory=list)
    as_of:   str = ""

    @property
    def bullish_count(self) -> int:
        return sum(1 for s in self.signals if s.sentiment == Sentiment.BULLISH)

    @property
    def bearish_count(self) -> int:
        return sum(1 for s in self.signals if s.sentiment == Sentiment.BEARISH)

    @property
    def neutral_count(self) -> int:
        return sum(1 for s in self.signals if s.sentiment == Sentiment.NEUTRAL)

    @property
    def is_blocking(self) -> bool:
        """S-25 — 3+ bearish brokerage-specific calls blocks entry."""
        bearish_brokerage = sum(
            1 for s in self.signals
            if s.sentiment == Sentiment.BEARISH and s.signal_type == SignalType.BROKERAGE_REPORT
        )
        return bearish_brokerage >= BEARISH_BLOCK_THRESHOLD

    @property
    def action(self) -> str:
        if self.is_blocking:
            return "BLOCK_ENTRY"
        if self.bearish_count > 0:
            return "WARN_BEFORE_ENTRY"
        return "CLEAR"

    def to_agent_text(self) -> str:
        if not self.signals:
            return f"No recent market intelligence found for {self.symbol}. Clear."
        lines = [
            f"Market Intelligence for {self.symbol}:",
            f"  Bullish signals: {self.bullish_count}",
            f"  Bearish signals: {self.bearish_count}",
            f"  Neutral:         {self.neutral_count}",
            "",
            "Recent headlines:",
        ]
        for s in sorted(self.signals, key=lambda x: x.sentiment.value)[:6]:
            lines.append(s.to_agent_line())
        return "\n".join(lines)
