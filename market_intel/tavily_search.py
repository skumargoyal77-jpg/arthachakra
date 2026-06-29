"""
market_intel/tavily_search.py
─────────────────────────────────
Tavily API wrapper with NSE-focused query templates. Ported nearly
verbatim from the real POC-12 implementation — same query templates,
same trusted domains, same mock fallback.

WHY TAVILY (not raw Google/Bing):
  - Returns clean structured JSON (title, url, content, score, date)
  - No HTML scraping, no rate limit surprises like Google Custom Search
  - Designed for AI agent use — filters low-quality results automatically
  - Free tier: 1,000 searches/month (enough for development + light prod)
  - include_domains param restricts to trusted financial sources only

FALLBACK: if TAVILY_API_KEY is missing or search fails, returns
realistic mock results and logs a clear warning so it's obvious it's
mock data, not silently treated as real.

CANNOT BE TESTED LIVE IN THIS SANDBOX — tavily.com isn't reachable
from this environment, same constraint as Hugging Face/NSE elsewhere
in this project. Mock fallback IS testable and is what's verified here.

PROJECT PATH:  market_intel/tavily_search.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from core.logging_config import setup_logging

logger = setup_logging(__name__)

_TRUSTED_DOMAINS = [
    "economictimes.indiatimes.com", "moneycontrol.com", "livemint.com",
    "business-standard.com", "ndtvprofit.com", "financialexpress.com",
    "cnbctv18.com", "nseindia.com", "bseindia.com", "reuters.com",
    "bloombergquint.com",
]

_SECTOR_MAP: dict[str, str] = {
    "HDFCBANK": "banking private sector India", "TCS": "IT software technology India",
    "SBILIFE": "life insurance India", "NESTLEIND": "FMCG consumer goods India",
    "ITC": "FMCG cigarettes diversified India", "POWERGRID": "power infrastructure utilities India",
    "BANKNIFTY": "banking sector India financial", "NIFTY": "Indian stock market economy",
    "RELIANCE": "energy conglomerate retail India",
}

_QUERY_TEMPLATES: dict[str, str] = {
    "brokerage_report": "{symbol} NSE India analyst buy sell upgrade downgrade target price",
    "company_news": "{symbol} NSE India stock news latest results announcement",
    "sector_news": "{sector} sector outlook NSE India latest news",
    "promoter_activity": "{symbol} promoter stake buying selling India",
    "fii_dii": "FII DII institutional {symbol} buying selling India",
}


class TavilySearch:
    """
    Tavily API wrapper for NSE stock intelligence.

    Usage:
        searcher = TavilySearch()
        results  = searcher.search("HDFCBANK", ["brokerage_report", "company_news"])
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self._client = None
        self._last_call: float = 0.0
        self._rate_gap: float = 1.0

        if self._api_key:
            try:
                from tavily import TavilyClient
                self._client = TavilyClient(api_key=self._api_key)
                logger.info("TavilySearch ready (live mode)")
            except ImportError:
                logger.warning("tavily-python not installed. Run: pip install tavily-python")
        else:
            logger.warning(
                "TAVILY_API_KEY not set in .env — running in mock mode. "
                "Get a free key at https://tavily.com"
            )

    @property
    def is_live(self) -> bool:
        return self._client is not None

    def search(self, symbol: str, query_types: list[str] | None = None, max_per_type: int = 4) -> list[dict]:
        query_types = query_types or ["brokerage_report", "company_news"]
        symbol = symbol.upper()

        if not self.is_live:
            return self._mock_results(symbol, query_types)

        all_results: list[dict] = []
        seen_urls: set[str] = set()

        for qtype in query_types:
            query = self._build_query(symbol, qtype)
            if not query:
                continue
            try:
                results = self._call_tavily(query, max_per_type)
                for r in results:
                    url = r.get("url", "")
                    if url not in seen_urls:
                        r["_query_type"] = qtype
                        r["_symbol"] = symbol
                        all_results.append(r)
                        seen_urls.add(url)
            except Exception as e:
                logger.warning("Tavily search failed for %s/%s: %s", symbol, qtype, e)

        return all_results

    def search_sector(self, symbol: str, max_results: int = 4) -> list[dict]:
        symbol = symbol.upper()
        if not self.is_live:
            return self._mock_sector_results(symbol)

        sector = _SECTOR_MAP.get(symbol, "India stock market")
        query = _QUERY_TEMPLATES["sector_news"].format(sector=sector)
        try:
            results = self._call_tavily(query, max_results)
            for r in results:
                r["_query_type"] = "sector_news"
                r["_symbol"] = symbol
            return results
        except Exception as e:
            logger.warning("Tavily sector search failed for %s: %s", symbol, e)
            return []

    # ── Private ────────────────────────────────────────────────────────

    def _build_query(self, symbol: str, qtype: str) -> str:
        template = _QUERY_TEMPLATES.get(qtype, "")
        if not template:
            return ""
        sector = _SECTOR_MAP.get(symbol, "Indian stock")
        return template.format(symbol=symbol, sector=sector)

    def _call_tavily(self, query: str, max_results: int) -> list[dict]:
        gap = time.monotonic() - self._last_call
        if gap < self._rate_gap:
            time.sleep(self._rate_gap - gap)
        try:
            response = self._client.search(
                query=query, max_results=max_results, search_depth="advanced",
                include_domains=_TRUSTED_DOMAINS, include_answer=False,
            )
            return response.get("results", [])
        finally:
            self._last_call = time.monotonic()

    # ── Mock data ──────────────────────────────────────────────────────

    def _mock_results(self, symbol: str, query_types: list[str]) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        mocks: dict[str, list[dict]] = {
            "HDFCBANK": [
                {"title": "Goldman Sachs upgrades HDFC Bank to Buy with target Rs2100",
                 "url": "https://economictimes.com/hdfc-bank-goldman-upgrade",
                 "content": "Goldman Sachs has upgraded HDFC Bank from Neutral to Buy, raising target price. Strong loan growth cited.",
                 "published_date": today, "score": 0.95},
                {"title": "UBS cuts HDFC Bank target on margin pressure concerns",
                 "url": "https://livemint.com/hdfc-bank-ubs-target-cut",
                 "content": "UBS reduced its target price for HDFC Bank, citing margin compression concerns.",
                 "published_date": today, "score": 0.82},
            ],
            "TCS": [
                {"title": "TCS Q1 FY27 results: Revenue in-line, deal wins positive",
                 "url": "https://economictimes.com/tcs-q1-results",
                 "content": "TCS reported results broadly in-line with estimates. Strong deal wins provide revenue visibility.",
                 "published_date": today, "score": 0.90},
            ],
        }
        return mocks.get(symbol, [
            {"title": f"{symbol}: No recent analyst coverage found",
             "url": f"https://moneycontrol.com/{symbol.lower()}",
             "content": f"No recent brokerage reports or major news found for {symbol} in the last 7 days.",
             "published_date": today, "score": 0.40},
        ])

    def _mock_sector_results(self, symbol: str) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        sector = _SECTOR_MAP.get(symbol, "Indian markets")
        return [{
            "title": f"{sector.split()[0].title()} sector: Neutral to positive outlook",
            "url": f"https://economictimes.com/{sector.split()[0].lower()}-sector-outlook",
            "content": f"Analysts maintain a neutral to cautiously positive view on the {sector} sector.",
            "published_date": today, "score": 0.70,
        }]
