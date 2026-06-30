"""
dashboard/stock_analysis.py
────────────────────────────
Pure computation engine. No database, no I/O, no dependencies beyond
stdlib. Ported nearly verbatim from POC-13's analysis_engine.py — this
logic needed no changes, it was already dependency-free.

CORE LOGIC:
  For each month:
    OH% = (High - Open) / Open   ← upside move from entry
    OL% = (Open - Low)  / Open   ← downside move from entry

  Win at threshold T:
    OH% ≤ T  AND  OL% ≤ T    (strangle survived on both sides)

  Win rate at T:
    count(Win months) / count(total valid months)

This is the quantified, backtested version of what Rule S-06 ("Range-
Bound Stock Confirmation") was always meant to check — S-06 itself
stays a qualitative ADVISORY judgment call (no fixed threshold was
ever defined for it), but this engine gives the trader the actual
historical numbers to make that judgment with.

INPUT DATA SOURCE: db.monthly_ohlc (Step 5's market_data/ohlc_updater.py
already populates this with exactly the open/high/low shape this
engine needs — no separate data pipeline required).

TIER CLASSIFICATION (configurable thresholds):
  Violet : Win rate ≥ 80%
  Green  : Win rate 70–80%
  Yellow : Win rate 60–70%
  Red    : Win rate < 60%  OR  last 2 months both Loose (recency filter)

PROJECT PATH:  dashboard/stock_analysis.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Default analysis thresholds (%)
THRESHOLDS     = [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
DEFAULT_TARGET = 10   # primary threshold used for tier + ranking

TIER_VIOLET_MIN = 80
TIER_GREEN_MIN  = 70
TIER_YELLOW_MIN = 60

TIER_COLORS = {
    "Violet": "#9B59B6",
    "Green":  "#27AE60",
    "Yellow": "#F39C12",
    "Red":    "#E74C3C",
}

TIER_ORDER = {"Violet": 0, "Green": 1, "Yellow": 2, "Red": 3}


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class MonthlyRecord:
    month_key:  str     # "YYYY-MM"
    open:       float
    high:       float
    low:        float

    @property
    def oh_pct(self) -> float:
        """Upside move from open — (High - Open) / Open"""
        return (self.high - self.open) / self.open

    @property
    def ol_pct(self) -> float:
        """Downside move from open — (Open - Low) / Open"""
        return (self.open - self.low) / self.open

    def is_win(self, threshold_pct: int) -> bool:
        """Win = neither side moved beyond threshold"""
        t = threshold_pct / 100
        return self.oh_pct <= t and self.ol_pct <= t

    def to_dict(self) -> dict:
        return {
            "month_key":  self.month_key,
            "open":       self.open,
            "high":       self.high,
            "low":        self.low,
            "oh_pct":     round(self.oh_pct * 100, 3),
            "ol_pct":     round(self.ol_pct * 100, 3),
        }


@dataclass
class StockAnalysis:
    """Full analysis result for one stock at a given target threshold."""

    symbol:         str
    total_months:   int
    win_rates:      dict[int, float]       # threshold → win rate (0–1)
    win_counts:     dict[int, int]         # threshold → win count
    last_3_months:  list[dict]             # [{month_key, status, oh_pct, ol_pct}]
    tier:           str                    # Violet / Green / Yellow / Red
    target:         int                    # the target threshold used for tier
    monthly:        list[MonthlyRecord]    # all records, newest first

    @property
    def target_win_rate(self) -> float:
        return self.win_rates.get(self.target, 0.0)

    @property
    def target_win_pct(self) -> float:
        """Win rate as 0–100"""
        return round(self.target_win_rate * 100, 1)

    @property
    def tier_rank(self) -> int:
        return TIER_ORDER.get(self.tier, 9)

    @property
    def recent_2_both_loose(self) -> bool:
        if len(self.last_3_months) >= 2:
            return (self.last_3_months[0]["status"] == "Loose" and
                    self.last_3_months[1]["status"] == "Loose")
        return False

    def win_rate_display(self, threshold: int) -> str:
        rate = self.win_rates.get(threshold, 0)
        return f"{round(rate * 100, 0):.0f}%"

    def to_summary_dict(self) -> dict:
        d = {
            "symbol":        self.symbol,
            "total_months":  self.total_months,
            "tier":          self.tier,
            "win_pct":       self.target_win_pct,
            f"win_{self.target}pct": self.target_win_pct,
        }
        for t in THRESHOLDS:
            d[f"win_{t}pct"] = round(self.win_rates.get(t, 0) * 100, 1)
        for i, m in enumerate(self.last_3_months):
            d[f"month_{i+1}_key"]    = m["month_key"]
            d[f"month_{i+1}_status"] = m["status"]
        return d


# ── Core analysis functions ────────────────────────────────────────────────

def compute_stock_analysis(
    symbol:          str,
    records:         list[MonthlyRecord],
    target:          int  = DEFAULT_TARGET,
    recency_filter:  bool = True,
    recency_months:  int  = 3,
) -> StockAnalysis:
    """
    Full analysis pipeline for one stock.

    Args:
        symbol:         NSE/investing.com symbol
        records:        all MonthlyRecord objects for this stock
        target:         primary threshold % for tier classification
        recency_filter: if True, last-2-months-both-Loose → Red
        recency_months: how many recent months to report

    Returns:
        StockAnalysis with win rates at all thresholds + tier.
    """
    # Only use records with valid OHLC
    valid = [r for r in records if r.open and r.high and r.low and r.open > 0]

    # Sort newest first
    valid.sort(key=lambda r: r.month_key, reverse=True)
    total = len(valid)

    if total == 0:
        return StockAnalysis(
            symbol=symbol, total_months=0,
            win_rates={t: 0.0 for t in THRESHOLDS},
            win_counts={t: 0  for t in THRESHOLDS},
            last_3_months=[], tier="Red",
            target=target, monthly=[],
        )

    # Win counts + rates at each threshold
    win_counts: dict[int, int]  = {}
    win_rates:  dict[int, float] = {}
    for t in THRESHOLDS:
        count = sum(1 for r in valid if r.is_win(t))
        win_counts[t] = count
        win_rates[t]  = count / total

    # Recent months
    last_n = []
    for r in valid[:recency_months]:
        last_n.append({
            "month_key": r.month_key,
            "status":    "Win" if r.is_win(target) else "Loose",
            "oh_pct":    round(r.oh_pct * 100, 2),
            "ol_pct":    round(r.ol_pct * 100, 2),
        })

    # Tier
    tier = classify_tier(
        win_rate       = win_rates[target],
        recent_statuses = [m["status"] for m in last_n],
        recency_filter  = recency_filter,
    )

    return StockAnalysis(
        symbol        = symbol,
        total_months  = total,
        win_rates     = win_rates,
        win_counts    = win_counts,
        last_3_months = last_n,
        tier          = tier,
        target        = target,
        monthly       = valid,
    )


def classify_tier(
    win_rate:        float,
    recent_statuses: list[str],
    recency_filter:  bool = True,
) -> str:
    """
    Classify stock into tier based on win rate and recent performance.

    Recency override: if last 2 months both Loose → Red, regardless of history.
    """
    # Recency override
    if recency_filter and len(recent_statuses) >= 2:
        if recent_statuses[0] == "Loose" and recent_statuses[1] == "Loose":
            return "Red"

    rate_pct = win_rate * 100
    if rate_pct >= TIER_VIOLET_MIN:  return "Violet"
    if rate_pct >= TIER_GREEN_MIN:   return "Green"
    if rate_pct >= TIER_YELLOW_MIN:  return "Yellow"
    return "Red"


def rank_and_filter(
    analyses:         list[StockAnalysis],
    min_win_pct:      float = 60.0,
    include_red:      bool  = False,
    sort_by:          str   = "win_rate",   # "win_rate" | "tier"
) -> list[StockAnalysis]:
    """
    Filter and rank stocks for monthly selection.

    Args:
        analyses:    list of StockAnalysis objects
        min_win_pct: minimum Win% at target threshold (0–100)
        include_red: if False, Red-tier stocks are excluded
        sort_by:     sort criterion

    Returns:
        Sorted, filtered list — ready for the Action Plan.
    """
    filtered = []
    for a in analyses:
        if not include_red and a.tier == "Red":
            continue
        if a.target_win_pct < min_win_pct:
            continue
        filtered.append(a)

    if sort_by == "tier":
        filtered.sort(key=lambda a: (a.tier_rank, -a.target_win_rate))
    else:
        filtered.sort(key=lambda a: -a.target_win_rate)

    return filtered


def records_from_dicts(raw_list: list[dict]) -> list[MonthlyRecord]:
    """
    Convert list of dicts (from MongoDB or Excel) to MonthlyRecord objects.

    Expected keys: month_key (str YYYY-MM), open, high, low
    """
    records = []
    for r in raw_list:
        try:
            records.append(MonthlyRecord(
                month_key = str(r["month_key"]),
                open      = float(r["open"]),
                high      = float(r["high"]),
                low       = float(r["low"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return records
