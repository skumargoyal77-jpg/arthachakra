"""
pnl/reporting.py
─────────────────────
Weekly/monthly P&L rollups, built from the daily_pnl snapshots
pnl/snapshot.py writes.

PROJECT PATH:  pnl/reporting.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from core.database import Database


@dataclass
class PnlRollup:
    user_id: str
    period_label: str
    start_date: date
    end_date: date
    daily_totals: list[tuple[str, float]] = field(default_factory=list)   # (date_iso, total_pnl)
    days_with_data: int = 0
    sum_pnl: float = 0.0
    best_day: tuple[str, float] | None = None
    worst_day: tuple[str, float] | None = None

    @property
    def avg_daily_pnl(self) -> float:
        return self.sum_pnl / self.days_with_data if self.days_with_data else 0.0

    def to_text(self) -> str:
        if self.days_with_data == 0:
            return f"No P&L snapshots found for {self.period_label} ({self.start_date} to {self.end_date})."

        lines = [
            f"P&L Rollup — {self.period_label} ({self.start_date} to {self.end_date})",
            f"  Days with data : {self.days_with_data}",
            f"  Total P&L      : {self.sum_pnl:+,.2f}",
            f"  Average/day    : {self.avg_daily_pnl:+,.2f}",
        ]
        if self.best_day:
            lines.append(f"  Best day       : {self.best_day[0]} ({self.best_day[1]:+,.2f})")
        if self.worst_day:
            lines.append(f"  Worst day      : {self.worst_day[0]} ({self.worst_day[1]:+,.2f})")
        return "\n".join(lines)


def _get_daily_totals(db: Database, user_id: str, start: date, end: date) -> list[tuple[str, float]]:
    docs = db.daily_pnl.find({"user_id": user_id})
    in_range = [
        (d["date"], d["total_pnl"]) for d in docs
        if start.isoformat() <= d["date"] <= end.isoformat()
    ]
    return sorted(in_range, key=lambda t: t[0])


def _build_rollup(db: Database, user_id: str, label: str, start: date, end: date) -> PnlRollup:
    daily_totals = _get_daily_totals(db, user_id, start, end)
    rollup = PnlRollup(user_id=user_id, period_label=label, start_date=start, end_date=end,
                       daily_totals=daily_totals)
    if daily_totals:
        rollup.days_with_data = len(daily_totals)
        rollup.sum_pnl = sum(t[1] for t in daily_totals)
        rollup.best_day = max(daily_totals, key=lambda t: t[1])
        rollup.worst_day = min(daily_totals, key=lambda t: t[1])
    return rollup


def get_weekly_rollup(db: Database, user_id: str, as_of: date | None = None) -> PnlRollup:
    """Monday-to-as_of of the current calendar week."""
    as_of = as_of or date.today()
    monday = as_of - timedelta(days=as_of.weekday())
    return _build_rollup(db, user_id, "This Week", monday, as_of)


def get_monthly_rollup(db: Database, user_id: str, as_of: date | None = None) -> PnlRollup:
    """1st-of-month to as_of of the current calendar month."""
    as_of = as_of or date.today()
    first_of_month = as_of.replace(day=1)
    return _build_rollup(db, user_id, "This Month", first_of_month, as_of)


def get_custom_rollup(db: Database, user_id: str, start: date, end: date) -> PnlRollup:
    label = f"{start.isoformat()} to {end.isoformat()}"
    return _build_rollup(db, user_id, label, start, end)
