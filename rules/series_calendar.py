"""
rules/series_calendar.py
────────────────────────────
Series and expiry calendar math — the one piece of shared logic behind
nine different rules (S-26, A-02, A-06, A-07, A-08, A-11, EP-01, EP-02,
X-09). Built once here so every rule that depends on "which week of the
series is it" or "is this a 4-week or 5-week month" computes the same
answer, rather than nine slightly different re-derivations that could
silently disagree with each other.

CORE ASSUMPTIONS (confirmed against the user's own worked examples):
  - Expiry = the LAST TUESDAY of each calendar month.
  - A series runs from (previous month's expiry + 1 calendar day)
    through (this month's expiry), inclusive.
  - Weeks within a series are 7-CALENDAR-day blocks (not trading-day
    blocks) starting at the series' first day.
  - A series is "4-week" if it spans 28 calendar days, "5-week" if it
    spans 35 calendar days (the only two cases that occur with a
    last-Tuesday-of-month expiry rule).

VERIFIED EXAMPLE (June 2026, user-confirmed 5-week series):
  May 2026 expiry  = 26 May 2026 (last Tuesday)
  June 2026 expiry = 30 Jun 2026 (last Tuesday)
  Series start      = 27 May 2026
  Week 1 = 27 May – 2 Jun   (12% OTM threshold)
  Week 2 = 3 Jun  – 9 Jun   (10%)
  Week 3 = 10 Jun – 16 Jun  (8%)
  Week 4 = 17 Jun – 23 Jun  (6%)
  Week 5 = 24 Jun – 30 Jun  (4%)

"SESSIONS" — is_final_n_sessions() now uses the real NSE holiday
calendar (core/nse_holidays.py), not just a weekday approximation.
This used to be a known gap (flagged explicitly here) until a real
download failure on 26 Jun 2026 — a Friday that turned out to be a
holiday (Muharram) — surfaced it concretely. Update
core/nse_holidays.py each December when NSE publishes next year's list.

PROJECT PATH:  rules/series_calendar.py
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# ── Week-scaled schedules, confirmed during rule review ──────────────────

# S-26 / A-02 / A-08 — required OTM% by week of series
OTM_SCHEDULE_4WEEK: dict[int, float] = {1: 10.0, 2: 8.0, 3: 6.0, 4: 4.0}
OTM_SCHEDULE_5WEEK: dict[int, float] = {1: 12.0, 2: 10.0, 3: 8.0, 4: 6.0, 5: 4.0}

# EP-01 — required profit-decay % by week, with a final-stretch override
PROFIT_SCHEDULE_4WEEK: dict[int, float] = {1: 50.0, 2: 60.0, 3: 70.0, 4: 80.0}
PROFIT_SCHEDULE_5WEEK: dict[int, float] = {1: 50.0, 2: 60.0, 3: 70.0, 4: 75.0, 5: 80.0}
# Final 3 trading days before expiry override the week value regardless
# of 4 vs 5 week series. Keyed by CALENDAR days before expiry — since
# expiry is always a Tuesday, the preceding Friday is always 4 calendar
# days before (Fri->Sat->Sun->Mon->Tue), and the preceding Monday is
# always 1 day before. NOT 2 and 1 — the weekend gap matters here.
FINAL_STRETCH_PROFIT_OVERRIDE: dict[int, float] = {4: 85.0, 1: 90.0, 0: 95.0}


@dataclass(frozen=True)
class SeriesWindow:
    series_start: date
    expiry: date
    week_count: int   # 4 or 5

    @property
    def total_days(self) -> int:
        return (self.expiry - self.series_start).days + 1


def last_tuesday_of_month(year: int, month: int) -> date:
    """The last Tuesday of the given calendar month."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 1:   # Monday=0, Tuesday=1
        d -= timedelta(days=1)
    return d


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def get_series_window(as_of: date) -> SeriesWindow:
    """
    The series window containing `as_of`. If `as_of` is after this
    month's expiry (i.e. expiry already happened), the window rolls
    forward to next month's series, since "today" is then already
    inside the *next* series.
    """
    expiry = last_tuesday_of_month(as_of.year, as_of.month)
    if as_of > expiry:
        ny, nm = _next_month(as_of.year, as_of.month)
        expiry = last_tuesday_of_month(ny, nm)
        py, pm = _prev_month(ny, nm)
    else:
        py, pm = _prev_month(as_of.year, as_of.month)

    prev_expiry = last_tuesday_of_month(py, pm)
    series_start = prev_expiry + timedelta(days=1)
    total_days = (expiry - series_start).days + 1
    week_count = 5 if total_days > 28 else 4
    return SeriesWindow(series_start=series_start, expiry=expiry, week_count=week_count)


def get_week_number(as_of: date) -> int:
    """1-based week number within the series containing `as_of`."""
    window = get_series_window(as_of)
    days_in = (as_of - window.series_start).days
    return min(days_in // 7 + 1, window.week_count)


def get_week_count(as_of: date) -> int:
    return get_series_window(as_of).week_count


def days_to_expiry(as_of: date) -> int:
    return (get_series_window(as_of).expiry - as_of).days


def is_final_n_sessions(as_of: date, n: int) -> bool:
    """
    True if `as_of` falls within the last `n` trading SESSIONS before
    expiry. Uses the real NSE holiday calendar (core/nse_holidays.py)
    — this used to be a weekday-only approximation, which was wrong on
    any holiday that fell on a weekday (confirmed: this caused a real
    download failure on 26 Jun 2026, a Friday that was actually a
    holiday — Muharram — not a normal trading day).
    """
    from core.nse_holidays import is_trading_day

    window = get_series_window(as_of)
    if as_of > window.expiry:
        return False
    sessions = 0
    d = window.expiry
    while d >= as_of:
        if is_trading_day(d):
            sessions += 1
        d -= timedelta(days=1)
    return sessions <= n


def required_otm_pct(as_of: date) -> float:
    """S-26 / A-02 / A-08 — required OTM% for the week `as_of` falls in."""
    window = get_series_window(as_of)
    week = get_week_number(as_of)
    schedule = OTM_SCHEDULE_5WEEK if window.week_count == 5 else OTM_SCHEDULE_4WEEK
    return schedule[week]


def required_profit_pct(as_of: date) -> float:
    """
    EP-01 — required profit-decay % for `as_of`. Checks the final-3-day
    override first (Fri/Mon/expiry-Tue get a fixed value regardless of
    week count), then falls back to the week-scaled schedule.
    """
    window = get_series_window(as_of)
    days_left = (window.expiry - as_of).days
    if days_left in FINAL_STRETCH_PROFIT_OVERRIDE and as_of <= window.expiry:
        return FINAL_STRETCH_PROFIT_OVERRIDE[days_left]
    week = get_week_number(as_of)
    schedule = PROFIT_SCHEDULE_5WEEK if window.week_count == 5 else PROFIT_SCHEDULE_4WEEK
    return schedule[week]


def is_first_wednesday_of_series(as_of: date) -> bool:
    """S-12 — true if `as_of` is the first Wednesday on/after series start."""
    window = get_series_window(as_of)
    d = window.series_start
    while d.weekday() != 2:   # Wednesday=2
        d += timedelta(days=1)
    return as_of == d


def is_entry_time_permitted(as_of_datetime) -> bool:
    """
    S-12 — entry permitted from first Wednesday of series onward, not
    before 12:00 that day. No upper bound after that point.
    """
    as_of_date = as_of_datetime.date()
    window = get_series_window(as_of_date)
    first_wed = window.series_start
    while first_wed.weekday() != 2:
        first_wed += timedelta(days=1)
    if as_of_date < first_wed:
        return False
    if as_of_date == first_wed and as_of_datetime.hour < 12:
        return False
    return True


def is_last_friday_or_monday_post_2pm(as_of_datetime) -> bool:
    """EP-02 — final exit window: last Friday or Monday of series, after 2PM."""
    as_of_date = as_of_datetime.date()
    window = get_series_window(as_of_date)
    days_left = (window.expiry - as_of_date).days
    # Expiry is always a Tuesday: Monday before = 1 day left, Friday
    # before = 4 days left (weekend gap), NOT 2 and 1.
    if days_left not in (4, 1):
        return False
    return as_of_datetime.hour >= 14


def first_day_after_last_expiry(as_of: date) -> date:
    """X-09 — anchor date for next-series allocation planning."""
    window = get_series_window(as_of)
    return window.series_start
