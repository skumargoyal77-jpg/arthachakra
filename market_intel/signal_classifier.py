"""
market_intel/signal_classifier.py
───────────────────────────────────────
Classifies one raw Tavily search result into a MarketSignal.

REWRITTEN AFTER REAL-DATA TESTING FOUND TWO GENUINE BUGS:

  1. Generic ticker/quote-page results (e.g. "HDFC Bank Share Price
     Today - Live NSE/BSE Updates") were being classified as real
     signals. These pages have no actual sentiment — they're template
     boilerplate, often containing words like "sell" somewhere on the
     page (a Buy/Sell button label, navigation, disclaimers) that
     triggered false-positive BEARISH classifications across nearly
     every result, regardless of actual content. Fixed by filtering
     these out by title pattern BEFORE classification even runs.

  2. Classification searched the ENTIRE page content (title + full
     scraped text) as one blob with plain substring matching. A real
     Jefferies headline — "ups HDFC, ICICI Bk target p[rice]" — was
     misclassified BEARISH, almost certainly because "sell" or another
     bearish substring appeared somewhere in the long scraped content,
     even though the actual headline was bullish (a raised target).
     Fixed by classifying primarily on the TITLE (the actual
     news/analyst statement), with word-boundary regex matching (not
     substring) to avoid partial-word false matches, falling back to
     only the FIRST ~200 chars of content (the lead, not buried
     boilerplate) when the title alone gives no signal.

PROJECT PATH:  market_intel/signal_classifier.py
"""

from __future__ import annotations

import re

from market_intel.signal_models import MarketSignal, Sentiment, SignalType

# Word-boundary patterns, not plain substrings — "sell" must be a whole
# word, not embedded in something else, and ordered so multi-word
# phrases are checked before their component single words.
_BULLISH_PATTERNS = [
    r"\bupgrad\w*\b", r"\boutperform\b", r"\bstrong buy\b", r"\boverweight\b",
    r"\bbeats? estimates?\b", r"\binitiates? buy\b",
    r"\b(ups|raises?|hikes?)\b.{0,40}\btarget\b",   # "ups HDFC...target", words in between are normal
    r"\btarget (raise|hike)\b", r"\b(rises?|jumps?|surges?|soars?|rallies?)\b",
    r"\bbeats? (expectations|forecast)\b", r"\bbuy rating\b",
]
_BEARISH_PATTERNS = [
    r"\bdowngrad\w*\b", r"\bunderperform\b", r"\bunderweight\b",
    r"\bmiss(es)? estimates?\b", r"\bbelow expectations?\b", r"\breduce rating\b",
    r"\b(?<!rate )(cuts?|lowers?|trims?)\b.{0,25}\btarget\b", r"\btarget cut\b",
    r"\b(falls?|declin\w*|drops?|plunges?|tumbles?|slumps?)\b", r"\bsell rating\b",
    r"\bpat (falls?|declines?|drops?)\b",
]
# "sell"/"buy" alone are deliberately excluded from the patterns above —
# both words appear constantly as generic UI/button text on finance
# pages regardless of actual sentiment, which was the exact source of
# the false-positive bug. Multi-word phrases ("sell rating", "buy
# rating", "initiates buy") are specific enough to keep.

# Titles matching any of these are generic ticker/quote landing pages,
# not real news or analysis — filtered out before classification runs.
_BOILERPLATE_TITLE_PATTERNS = [
    r"share price today", r"stock price (live|today)", r"share/stock price",
    r"live nse/bse", r"nse/bse (updates|rates)", r"stock market watch",
    r"pre-open market", r"share price\s*-?\s*stocks", r"share price highlights",
    # Bare "{Company} Ltd - SourceSite.com" listing-page titles, e.g.
    # "HDFC Bank Ltd - Moneycontrol.com" — no real words beyond the
    # company name and a known finance-site domain suffix, found via
    # real data: this exact title carried no actual sentiment at all.
    r"-\s*(moneycontrol|economictimes|livemint|business-standard|"
    r"ndtvprofit|cnbctv18|financialexpress)\.com\s*$",
]

# Common brokerage names — used to extract broker_name for S-25's
# per-broker dedup (3+ DISTINCT bearish calls, not the same one 3 times)
_KNOWN_BROKERS = [
    "Goldman Sachs", "Morgan Stanley", "UBS", "Motilal Oswal", "Emkay",
    "IIFL", "Kotak", "HDFC Securities", "Jefferies", "CLSA", "Citi",
    "Nomura", "JP Morgan", "JPMorgan", "Macquarie", "BofA", "Bank of America",
    "Credit Suisse", "Nuvama", "ICICI Securities", "Axis Securities",
    "Sharekhan", "Edelweiss", "Antique",
]


_QUERY_TYPE_TO_SIGNAL_TYPE = {
    "brokerage_report": SignalType.BROKERAGE_REPORT,
    "company_news": SignalType.COMPANY_NEWS,
    "sector_news": SignalType.SECTOR_NEWS,
    "promoter_activity": SignalType.PROMOTER_ACTIVITY,
    "fii_dii": SignalType.FII_DII,
}


def _extract_broker_name(text: str) -> str | None:
    for broker in _KNOWN_BROKERS:
        if broker.lower() in text.lower():
            return broker
    return None


def _extract_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _has_repeated_phrase(title: str) -> bool:
    """
    Catches aggregator/tag pages two ways, both found via real data:

    1. The title's leading 3-word phrase reappears verbatim later
       (e.g. "Tata Consultancy Services Q2 Results... Find Tata
       Consultancy Services Q2 Earnings Result").
    2. The title's first WORD (the company/topic name) repeats 3+
       times overall, even with a varying suffix each time (e.g.
       "TCS Q4 Results - TCS Q4 earnings News, TCS Q4 result
       updates" — "Results"/"earnings"/"result" differ, but "TCS"
       repeating 3x is still the same aggregator signature).

    DELIBERATELY does NOT flag a short phrase repeating just twice
    mid-sentence (e.g. "interim dividend of Rs 11/share; special
    dividend of Rs 46/share") — that was a real, substantive headline
    incidentally repeating "dividend of Rs" because it legitimately
    compares two different real numbers, not because it's an
    aggregator page. Threshold of 3+ for the first-word check, and
    requiring the LEADING phrase specifically (not any phrase
    anywhere) for the trigram check, both exist specifically to avoid
    re-triggering that false positive.
    """
    words = re.findall(r"[a-z]+", title.lower())
    if len(words) < 6:
        return False

    lead_trigram = " ".join(words[:3])
    rest = " ".join(words[3:])
    if lead_trigram in rest:
        return True

    first_word = words[0]
    if len(first_word) >= 3 and words.count(first_word) >= 3:
        return True

    return False


def is_boilerplate(title: str) -> bool:
    """True if this is a generic ticker/quote landing page, not real
    news or analysis — see module docstring for why these get filtered
    out entirely rather than classified."""
    t = title.lower()
    if any(re.search(p, t) for p in _BOILERPLATE_TITLE_PATTERNS):
        return True
    return _has_repeated_phrase(title)


def _classify_sentiment(text: str) -> Sentiment:
    """Word-boundary regex matching, NOT substring — see module
    docstring for why plain substring matching on "sell"/"buy" caused
    near-universal false positives."""
    t = text.lower()
    bearish = any(re.search(p, t) for p in _BEARISH_PATTERNS)
    bullish = any(re.search(p, t) for p in _BULLISH_PATTERNS)
    if bearish and bullish:
        return Sentiment.BEARISH   # ambiguous mixed signal - treat cautiously
    if bearish:
        return Sentiment.BEARISH
    if bullish:
        return Sentiment.BULLISH
    return Sentiment.NEUTRAL


def classify_result(symbol: str, raw: dict) -> MarketSignal | None:
    """
    Classify one raw Tavily result dict into a MarketSignal. Returns
    None for boilerplate ticker/quote pages — caller should drop these,
    not include them as a (fake) neutral signal.

    Classifies PRIMARILY on the title (the actual headline/claim, not
    a long scraped page). Only falls back to the first ~200 chars of
    content (the lead, not buried boilerplate further down the page)
    when the title alone gives no signal.
    """
    title = raw.get("title", "")
    content = raw.get("content", "")

    if is_boilerplate(title):
        return None

    sentiment = _classify_sentiment(title)
    if sentiment == Sentiment.NEUTRAL and content:
        sentiment = _classify_sentiment(content[:200])

    query_type = raw.get("_query_type", "company_news")
    signal_type = _QUERY_TYPE_TO_SIGNAL_TYPE.get(query_type, SignalType.OTHER)

    return MarketSignal(
        symbol=symbol, title=title, url=raw.get("url", ""),
        sentiment=sentiment, summary=content[:300], signal_type=signal_type,
        source=_extract_domain(raw.get("url", "")),
        broker_name=_extract_broker_name(title + " " + content),
        published=raw.get("published_date", ""),
    )
