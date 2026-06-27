"""
core/nse_holidays.py
─────────────────────────
NSE trading holiday calendar — shared reference data used by:
  - rules/series_calendar.py (is_final_n_sessions — A-07's "last 3
    sessions" needs real trading days, not just "any weekday", since a
    holiday on a weekday was silently wrong before this existed)
  - market_data/bhavcopy.py (skip holidays when downloading a date
    range, not just weekends)

MAINTENANCE: NSE publishes next year's holiday list every December.
Add a new YEAR_HOLIDAYS entry when that happens — there's no API for
this, it's NSE's own published circular, manually transcribed.

2026 LIST SOURCE: user-supplied from NSE's official 2026 holiday
circular (confirmed: 26-Jun-2026 = Muharram, which is why a Bhavcopy
download failed on what looked like a normal Friday).

PROJECT PATH:  core/nse_holidays.py
"""

from __future__ import annotations

from datetime import date

# 2026 NSE trading holidays. Weekly weekends (Sat/Sun) are NOT listed
# here — those are handled separately via date.weekday(), since they
# recur every week rather than being named calendar dates.
HOLIDAYS_2026: set[date] = {
    date(2026, 1, 15),   # Municipal Corporation Election - Maharashtra
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali-Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
}

# Add future years here as NSE publishes them, e.g.:
# HOLIDAYS_2027: set[date] = {...}
ALL_HOLIDAYS: set[date] = HOLIDAYS_2026


def is_holiday(d: date) -> bool:
    """True if `d` is a named NSE trading holiday (not weekends)."""
    return d in ALL_HOLIDAYS


def is_trading_day(d: date) -> bool:
    """True if `d` is a real trading day — not a weekend, not a holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def known_years() -> list[int]:
    """Which years currently have a holiday list — useful for a sanity warning
    if code asks about a date outside what's been transcribed."""
    return sorted({d.year for d in ALL_HOLIDAYS})
