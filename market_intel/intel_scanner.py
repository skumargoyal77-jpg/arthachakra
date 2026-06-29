"""
market_intel/intel_scanner.py
─────────────────────────────────
Main intelligence scanner. Orchestrates Tavily searches, classifies
results, and caches output. Ported from the real POC-12
implementation, with one adaptation: caching uses
db.market_intel_cache (a real Mongo collection, already defined in the
schema since Step 1) instead of an in-memory dict — shared market
data, not per-user.

PROJECT PATH:  market_intel/intel_scanner.py
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from core.database import Database
from core.logging_config import setup_logging
from market_intel.signal_classifier import classify_result
from market_intel.signal_models import IntelSummary, MarketSignal, Sentiment, SignalType
from market_intel.tavily_search import TavilySearch

logger = setup_logging(__name__)

_DEFAULT_QUERIES = ["brokerage_report", "company_news"]
_CACHE_TTL_SECONDS = 3600   # 60 minutes — brokerage reports don't change hourly


class IntelScanner:
    """
    Market intelligence scanner for NSE short strangle positions.

    Usage:
        scanner = IntelScanner(db)
        summary = scanner.scan_symbol("HDFCBANK")
        print(summary.to_agent_text())
        print(summary.action)   # CLEAR | WARN_BEFORE_ENTRY | BLOCK_ENTRY
    """

    def __init__(self, db: Database, mock_mode: bool = False) -> None:
        self._db = db
        self._searcher = TavilySearch()
        self._mock_mode = mock_mode or not self._searcher.is_live

    @property
    def is_live(self) -> bool:
        return self._searcher.is_live and not self._mock_mode

    def scan_symbol(self, symbol: str, query_types: list[str] | None = None, force: bool = False) -> IntelSummary:
        symbol = symbol.upper()
        query_types = query_types or _DEFAULT_QUERIES
        cache_key = f"{symbol}_{'_'.join(sorted(query_types))}"

        if not force:
            cached = self._read_cache(cache_key)
            if cached is not None:
                return cached

        try:
            raw_results = self._searcher.search(symbol=symbol, query_types=query_types)
            classified = [classify_result(symbol, r) for r in raw_results]
            signals = [s for s in classified if s is not None]   # drop boilerplate ticker pages
            signals = self._deduplicate(signals)
        except Exception as e:
            logger.warning("scan_symbol failed for %s: %s", symbol, e)
            signals = []

        summary = IntelSummary(symbol=symbol, signals=signals, as_of=datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._write_cache(cache_key, summary)
        logger.info(
            "IntelScanner: %s -> %d signals (B:%d Bear:%d N:%d) -> %s",
            symbol, len(signals), summary.bullish_count, summary.bearish_count,
            summary.neutral_count, summary.action,
        )
        return summary

    def scan_portfolio(self, symbols: list[str], query_types: list[str] | None = None) -> dict[str, IntelSummary]:
        results: dict[str, IntelSummary] = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut_map = {ex.submit(self.scan_symbol, sym, query_types): sym for sym in symbols}
            for fut in as_completed(fut_map):
                sym = fut_map[fut]
                try:
                    results[sym] = fut.result()
                except Exception as e:
                    logger.warning("Portfolio scan failed for %s: %s", sym, e)
                    results[sym] = IntelSummary(symbol=sym)
        return results

    def check_s25(self, symbol: str) -> tuple[bool, str]:
        """Quick S-25 check: 3+ bearish brokerage calls -> BLOCK_ENTRY."""
        summ = self.scan_symbol(symbol, query_types=["brokerage_report"])
        if summ.is_blocking:
            bearish_brokers = [
                s for s in summ.signals
                if s.sentiment == Sentiment.BEARISH and s.signal_type == SignalType.BROKERAGE_REPORT
            ]
            names = [s.broker_name or s.source for s in bearish_brokers]
            return True, f"S-25: {len(bearish_brokers)} bearish calls ({', '.join(names[:3])})"
        return False, ""

    # ── Private: Mongo cache ──────────────────────────────────────────

    def _read_cache(self, cache_key: str) -> IntelSummary | None:
        doc = self._db.market_intel_cache.find_one({"cache_key": cache_key})
        if doc is None:
            return None
        if time.time() - doc.get("fetched_at", 0) > _CACHE_TTL_SECONDS:
            return None
        return self._summary_from_dict(doc)

    def _write_cache(self, cache_key: str, summary: IntelSummary) -> None:
        self._db.market_intel_cache.update_one(
            {"cache_key": cache_key},
            {"$set": {
                "cache_key": cache_key, "symbol": summary.symbol, "as_of": summary.as_of,
                "fetched_at": time.time(),
                "signals": [
                    {"symbol": s.symbol, "title": s.title, "url": s.url,
                     "sentiment": s.sentiment.value, "summary": s.summary,
                     "signal_type": s.signal_type.value, "source": s.source,
                     "broker_name": s.broker_name, "published": s.published}
                    for s in summary.signals
                ],
            }},
            upsert=True,
        )

    @staticmethod
    def _summary_from_dict(doc: dict) -> IntelSummary:
        signals = [
            MarketSignal(
                symbol=s["symbol"], title=s["title"], url=s["url"],
                sentiment=Sentiment(s["sentiment"]), summary=s["summary"],
                signal_type=SignalType(s["signal_type"]), source=s.get("source", ""),
                broker_name=s.get("broker_name"), published=s.get("published", ""),
            )
            for s in doc.get("signals", [])
        ]
        return IntelSummary(symbol=doc["symbol"], signals=signals, as_of=doc.get("as_of", ""))

    @staticmethod
    def _deduplicate(signals: list[MarketSignal]) -> list[MarketSignal]:
        seen_titles: set[str] = set()
        seen_brokers: set[str] = set()
        unique: list[MarketSignal] = []
        for s in signals:
            title_key = s.title[:60].lower().strip()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            if s.signal_type == SignalType.BROKERAGE_REPORT and s.broker_name:
                broker_key = s.broker_name.lower()
                if broker_key in seen_brokers:
                    continue
                seen_brokers.add(broker_key)
            unique.append(s)
        return unique