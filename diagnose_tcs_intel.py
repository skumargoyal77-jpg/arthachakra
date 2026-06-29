"""
Shows the RAW Tavily results for TCS before any of our classification
or filtering runs - to see whether fewer results came back this time,
or whether our filter is eating something it shouldn't.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from market_intel.tavily_search import TavilySearch
from market_intel.signal_classifier import is_boilerplate, classify_result

searcher = TavilySearch()
print(f"Tavily mode: {'LIVE' if searcher.is_live else 'MOCK'}\n")

raw_results = searcher.search("TCS", query_types=["brokerage_report", "company_news"])
print(f"RAW results from Tavily (before any filtering): {len(raw_results)}\n")

for r in raw_results:
    title = r.get("title", "")
    is_bp = is_boilerplate(title)
    classified = classify_result("TCS", r)
    status = f"FILTERED (boilerplate)" if is_bp else f"-> {classified.sentiment.value if classified else 'None'}"
    print(f"  [{status}]")
    print(f"    title: {title}")
    print(f"    url: {r.get('url','')}")
    print()