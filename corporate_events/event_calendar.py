"""
corporate_events/event_calendar.py
─────────────────────────────────────
Main calendar class. Wires together NSEClient + classifier. Ported
from the real POC-11 implementation, with one adaptation: caching uses
db.corporate_events_cache (a real Mongo collection, already defined in
the schema since Step 1) instead of an in-memory dict — this is shared
market data, not per-user, so it should persist and be shared across
every user's queries rather than refetching per Python process.

WHAT IT DOES:
  1. Fetches raw events from NSE event-calendar API (results, board meetings)
  2. Fetches raw corporate actions from NSE corporateActions API (splits, bonuses)
  3. Classifies each event -> CorporateEvent with impact + action + rule
  4. Caches results in Mongo (5-minute TTL) to avoid hammering NSE
  5. Falls back to realistic mock data if NSE API is unreachable

PROJECT PATH:  corporate_events/event_calendar.py
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

from core.database import Database
from core.logging_config import setup_logging
from corporate_events.event_classifier import classify_event
from corporate_events.event_models import CorporateEvent, EventSummary, EventType
from corporate_events.nse_client import NSEClient, parse_nse_date

logger = setup_logging(__name__)

CACHE_TTL_SECONDS = 300   # 5 minutes


class EventCalendar:
    """
    Corporate event calendar for NSE short strangle strategy.

    Usage:
        cal    = EventCalendar(db)
        events = cal.get_events("HDFCBANK", days_ahead=14)
        summ   = cal.get_summary("HDFCBANK")
    """

    def __init__(self, db: Database, mock_mode: bool = False) -> None:
        self._db = db
        self._client = NSEClient()
        self._mock_mode = mock_mode

    # ── Public API ───────────────────────────────────────────────────

    def get_events(self, symbol: str, days_ahead: int = 14, today: date | None = None) -> list[CorporateEvent]:
        """Upcoming events for one symbol, sorted by days_away ascending."""
        today = today or date.today()
        symbol = symbol.upper()

        cached = self._read_cache(symbol)
        if cached is not None:
            events = cached
        elif self._mock_mode:
            events = self._mock_events(symbol, today, days_ahead)
        else:
            try:
                events = self._fetch_events(symbol, today, days_ahead)
                self._write_cache(symbol, events)
            except Exception as e:
                logger.warning("EventCalendar live fetch failed for %s: %s — using mock", symbol, e)
                events = self._mock_events(symbol, today, days_ahead)

        events = [e for e in events if 0 <= e.days_away <= days_ahead]
        events.sort(key=lambda e: e.days_away)
        return events

    def get_summary(self, symbol: str, days_ahead: int = 14) -> EventSummary:
        events = self.get_events(symbol, days_ahead)
        return EventSummary(symbol=symbol.upper(), events=events, as_of=date.today())

    def get_all_summaries(self, symbols: list[str], days_ahead: int = 14) -> dict[str, EventSummary]:
        """Fetch events for multiple symbols in parallel."""
        results: dict[str, EventSummary] = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut_map = {ex.submit(self.get_summary, sym, days_ahead): sym for sym in symbols}
            for fut in as_completed(fut_map):
                sym = fut_map[fut]
                try:
                    results[sym] = fut.result()
                except Exception as e:
                    logger.warning("get_summary failed for %s: %s", sym, e)
                    results[sym] = EventSummary(symbol=sym, events=[])
        return results

    def has_blocking_event(self, symbol: str, days_ahead: int = 7) -> tuple[bool, Optional[CorporateEvent]]:
        """Quick check used by rules/engine.py for S-21/S-22/S-23/ES-09."""
        events = self.get_events(symbol, days_ahead)
        blocking = [e for e in events if e.is_blocking]
        return (True, blocking[0]) if blocking else (False, None)

    def has_results_before_expiry(
        self, symbol: str, expiry: date, today: date | None = None,
    ) -> tuple[bool, Optional[CorporateEvent]]:
        """
        Checks if a results-type event (quarterly/annual/half-yearly)
        falls ANYWHERE between today and the given series expiry —
        not just within a narrow day-window like S-21's 5-day block.

        WHY THIS EXISTS SEPARATELY FROM has_blocking_event():
          S-21 only blocks entry in the last 5 days before results.
          But a strangle normally runs to expiry, not to the results
          date — so entering 10 days before results (outside S-21's
          window) still leaves the position open and exposed when
          results actually land, if that's before this series even
          expires. Found via real TCS data: results 10 days away,
          inside the series, but S-21/S-24/M-09 all stayed silent
          since 10 days exceeds every one of their windows.

        REUSABLE BY STEP 9 (stock scanner) DIRECTLY — this is plain
        data, not routed through the rule engine, so the scanner can
        call this to filter results-before-expiry stocks out of its
        candidate list without going through check_rule at all.
        """
        today = today or date.today()
        days_to_expiry = (expiry - today).days
        if days_to_expiry < 0:
            return False, None

        events = self.get_events(symbol, days_ahead=days_to_expiry, today=today)
        results_types = {EventType.QUARTERLY_RESULTS, EventType.ANNUAL_RESULTS, EventType.HALF_YEARLY}
        for e in events:
            if e.event_type in results_types and e.event_date <= expiry:
                return True, e
        return False, None

    # ── Private: Mongo cache ──────────────────────────────────────────

    def _read_cache(self, symbol: str) -> Optional[list[CorporateEvent]]:
        doc = self._db.corporate_events_cache.find_one({"symbol": symbol})
        if doc is None:
            return None
        if time.time() - doc.get("fetched_at", 0) > CACHE_TTL_SECONDS:
            return None
        return [self._event_from_dict(e) for e in doc.get("events", [])]

    def _write_cache(self, symbol: str, events: list[CorporateEvent]) -> None:
        self._db.corporate_events_cache.update_one(
            {"symbol": symbol},
            {"$set": {"symbol": symbol, "events": [e.to_dict() for e in events], "fetched_at": time.time()}},
            upsert=True,
        )

    @staticmethod
    def _event_from_dict(d: dict) -> CorporateEvent:
        from corporate_events.event_models import EventAction, EventType, ImpactLevel
        return CorporateEvent(
            symbol=d["symbol"], event_type=EventType(d["event_type"]),
            event_date=date.fromisoformat(d["event_date"]), description=d["description"],
            impact=ImpactLevel(d["impact"]), days_away=d["days_away"],
            action=EventAction(d["action"]), rule_triggered=d["rule_triggered"],
            ex_date=date.fromisoformat(d["ex_date"]) if d.get("ex_date") else None,
            source=d.get("source", "NSE"),
        )

    # ── Private: fetch from NSE ────────────────────────────────────────

    def _fetch_events(self, symbol: str, today: date, days_ahead: int) -> list[CorporateEvent]:
        to_date = today + timedelta(days=days_ahead)
        events: list[CorporateEvent] = []

        raw_cal = self._client.get_event_calendar(today, to_date)
        for item in raw_cal:
            if item.get("symbol", "").upper() != symbol:
                continue
            event_date = parse_nse_date(item.get("date", ""))
            if not event_date:
                continue
            description = (item.get("purpose") or item.get("bm_desc") or "Board Meeting").strip()
            if not description:
                continue
            events.append(classify_event(symbol, description, event_date, today=today, source="NSE"))

        time.sleep(1.0)   # rate limit between the two NSE endpoints
        raw_actions = self._client.get_corporate_actions(symbol)
        for item in raw_actions:
            subject = item.get("subject", "").strip()
            # Short-circuit: only try the next fallback field if the
            # previous one didn't work. NSE uses a literal "-" for
            # fields that genuinely don't apply to a given record (e.g.
            # an old dividend entry with no separate book-closure date)
            # - that's normal, not a failure, and shouldn't warn if we
            # already have a perfectly good value from an earlier field.
            ex_date = parse_nse_date(item.get("exDate") or item.get("ex_date", ""))
            rec_date = ex_date or parse_nse_date(item.get("recDate") or item.get("rec_date", ""))
            ann_date = rec_date or parse_nse_date(item.get("date", ""))
            ref_date = ex_date or rec_date or ann_date
            if not ref_date or not subject:
                continue
            if not (0 <= (ref_date - today).days <= days_ahead):
                continue
            events.append(classify_event(symbol, subject, ref_date, today=today, ex_date=ex_date, source="NSE"))

        return events

    # ── Private: mock fallback ─────────────────────────────────────────

    def _mock_events(self, symbol: str, today: date, days_ahead: int) -> list[CorporateEvent]:
        """Realistic mock events — used when NSE is unreachable or mock_mode=True."""
        mock_map: dict[str, list[tuple]] = {
            "HDFCBANK": [(12, "Quarterly Results Q1 FY27")],
            "TCS": [(8, "Quarterly Results Q1 FY27"), (20, "Dividend Rs 10/- Per Share")],
            "SBILIFE": [(3, "Board Meeting"), (18, "AGM")],
            "NESTLEIND": [(22, "Annual General Meeting"), (6, "Quarterly Results Q1 FY27")],
            "ITC": [(5, "Dividend - Interim Rs 6.50/- Per Share")],
            "POWERGRID": [(15, "Quarterly Results Q1 FY27")],
        }
        events = []
        for days_offset, description in mock_map.get(symbol, []):
            if days_offset > days_ahead:
                continue
            event_date = today + timedelta(days=days_offset)
            events.append(classify_event(symbol, description, event_date, today=today, source="MOCK"))
        return events
