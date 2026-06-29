"""
corporate_events/nse_client.py
───────────────────────────────────
Low-level HTTP client for NSE's public event-calendar and
corporate-actions APIs. Reuses the exact session-priming pattern
already proven correct in market_data/bhavcopy.py (confirmed working
against live NSE in Step 5 — same headers, same cookie-priming step).

ENDPOINTS (no authentication needed, public NSE APIs):
  Results / board meetings:
    GET https://www.nseindia.com/api/event-calendar
        ?index=equities&fromDate=DD-MM-YYYY&toDate=DD-MM-YYYY

  Corporate actions (splits, bonuses, dividends, buybacks):
    GET https://www.nseindia.com/api/corporates-corporateActions
        ?index=equities&symbol=SYMBOL

CANNOT BE TESTED IN THIS SANDBOX — nseindia.com isn't on the network
allowlist here, same constraint as market_data/bhavcopy.py. Defensive
date parsing (tries several formats) since NSE's exact date format in
the response hasn't been verified against a live call from this
environment — same "diagnosable in one round-trip, not a guess"
philosophy used everywhere else NSE data was integrated.

PROJECT PATH:  corporate_events/nse_client.py
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.logging_config import setup_logging

logger = setup_logging(__name__)

EVENT_CALENDAR_URL = "https://www.nseindia.com/api/event-calendar"
CORPORATE_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com",
    "Accept": "application/json,text/html,*/*",
}

# NSE's date format in API responses isn't verified against a live
# call from this sandbox — tried in order, first match wins. If none
# match, parse_nse_date logs the raw value so the real format is
# diagnosable in one round-trip rather than another guess.
_DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"]


def parse_nse_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    logger.warning("Could not parse NSE date format: %r — none of %s matched", raw, _DATE_FORMATS)
    return None


class NSEClient:
    """Thin HTTP client for NSE's event-calendar and corporate-actions APIs."""

    def __init__(self) -> None:
        self._session = None
        self._primed = False

    def _prime_session(self) -> None:
        if self._primed:
            return
        import requests
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        try:
            self._session.get("https://www.nseindia.com", timeout=10)
            self._primed = True
        except Exception as e:
            logger.warning("Could not prime NSE session (may get 403): %s", e)

    def get_event_calendar(self, from_date: date, to_date: date) -> list[dict]:
        """Results/board meeting calendar across all equities in the date range.
        Raises on failure (rather than returning []) so EventCalendar's
        mock-fallback logic — which triggers on an exception — actually
        fires. Silently returning [] here was indistinguishable from
        "genuinely no events found", which produced a misleading
        false-clear result when NSE was actually just unreachable."""
        self._prime_session()
        resp = self._session.get(
            EVENT_CALENDAR_URL,
            params={
                "index": "equities",
                "fromDate": from_date.strftime("%d-%m-%Y"),
                "toDate": to_date.strftime("%d-%m-%Y"),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("NSE event-calendar HTTP %d: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"NSE event-calendar returned HTTP {resp.status_code}")
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    def get_corporate_actions(self, symbol: str) -> list[dict]:
        """Splits, bonuses, dividends, buybacks for one symbol.
        Raises on failure — see get_event_calendar's docstring for why."""
        self._prime_session()
        resp = self._session.get(
            CORPORATE_ACTIONS_URL,
            params={"index": "equities", "symbol": symbol.upper()},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("NSE corporate-actions HTTP %d: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"NSE corporate-actions returned HTTP {resp.status_code}")
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
