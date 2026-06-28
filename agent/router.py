"""
agent/router.py
───────────────────
Decides Haiku vs Sonnet per query.

ROUTING CRITERIA (per the Phase 2 plan):
  Haiku  — single-fact lookups: "what's the VIX", "is my SBILIFE position
           naked" — fast, cheap, no real synthesis needed.
  Sonnet — anything needing synthesis across multiple rules/data points:
           "should I enter HDFCBANK" needs VIX + margin + IVR + several
           rule checks combined into one recommendation.

This is a cheap heuristic, not a model call — the whole point is to
avoid spending a Sonnet-grade call deciding which model to use for a
Sonnet-grade call. Keyword-based for now; revisit if it misclassifies
in practice (same iterate-on-real-evidence approach as everywhere else
in this project).

PROJECT PATH:  agent/router.py
"""

from __future__ import annotations

import re

# Patterns suggesting "just look something up" rather than "decide something"
SIMPLE_PATTERNS = [
    r"\bwhat('s| is)\b.*\bvix\b",
    r"\bcurrent\b.*\bvix\b",
    r"\bwhat('s| is)\b.*\bdelta\b",
    r"\bis\b.*\bnaked\b",
    r"\bhow many\b.*\bleg",
    r"\bwhat('s| is)\b.*\bp&?l\b",
    r"\bcheck\b.*\brule\b",
]

# Patterns suggesting real synthesis across multiple things
COMPLEX_PATTERNS = [
    r"\bshould i\b",
    r"\bcan i enter\b",
    r"\benter\b.*\?",
    r"\bwhat should i do\b",
    r"\brecommend",
    r"\badvice\b",
    r"\bworth\b",
]


def route(question: str) -> str:
    """Returns 'haiku' or 'sonnet' for the given question."""
    q = question.lower().strip()

    for pattern in COMPLEX_PATTERNS:
        if re.search(pattern, q):
            return "sonnet"

    for pattern in SIMPLE_PATTERNS:
        if re.search(pattern, q):
            return "haiku"

    # Default: unfamiliar phrasing gets the more capable model rather
    # than risking a wrong "simple" classification on something that
    # actually needed synthesis.
    return "sonnet"
