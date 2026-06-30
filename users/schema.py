"""
users/schema.py
──────────────────
THE single source of truth for every MongoDB collection ArthaChakra will
ever use — both shared (platform-wide) and per-user.

WHY DEFINE EVERYTHING NOW, EVEN COLLECTIONS LATER STEPS WILL USE:
  Deciding the full shape once avoids re-touching this file (and its
  indexes) as each build step lands. Steps 2-10 write code that reads
  and writes these collections — they don't redefine them.

TWO CATEGORIES:
  SHARED   — one copy, same data regardless of which user is asking.
             Stock universe, OHLC history, the rule catalogue, cached
             corporate events / brokerage intel.
  PER-USER — every document carries a user_id field. Login identity,
             broker connections, rule toggles, action plans, P&L,
             Telegram bindings, agent query history.

PROJECT PATH:  users/schema.py
"""

from __future__ import annotations

COLLECTION_SCHEMA: dict[str, dict] = {

    # ═══════════════════════════════════════════════════════════════════
    #  SHARED — same data for every user
    # ═══════════════════════════════════════════════════════════════════

    "nse_stocks": {
        "shared":  True,
        "purpose": "Stock/index universe — symbol, sector, full name. (Step 5)",
        "indexes": [
            {"keys": [("symbol", 1)], "unique": True},
        ],
    },
    "monthly_ohlc": {
        "shared":  True,
        "purpose": "Historical monthly Open/High/Low per symbol. (Step 5)",
        "indexes": [
            {"keys": [("symbol", 1), ("month_key", 1)], "unique": True},
        ],
    },
    "platform_rules": {
        "shared":  True,
        "purpose": "Mandatory rules — cannot be disabled by any user. (Step 3)",
        "indexes": [
            {"keys": [("rule_id", 1)], "unique": True},
        ],
    },
    "default_rules": {
        "shared":  True,
        "purpose": "Optional rules — ON by default, user-toggleable. (Step 3)",
        "indexes": [
            {"keys": [("rule_id", 1)], "unique": True},
        ],
    },
    "corporate_events_cache": {
        "shared":  True,
        "purpose": "Cached NSE event calendar results, shared across users. (Step 7)",
        "indexes": [
            {"keys": [("symbol", 1), ("event_date", 1)]},
        ],
    },
    "market_intel_cache": {
        "shared":  True,
        "purpose": "Cached brokerage/sentiment search results. (Step 7)",
        "indexes": [
            {"keys": [("symbol", 1), ("fetched_at", -1)]},
        ],
    },
    "vix_history": {
        "shared":  True,
        "purpose": "Timestamped India VIX readings — closes S-01, S-02, EP-04, S-15. (Step 5)",
        "indexes": [
            {"keys": [("captured_at", -1)]},
        ],
    },
    "iv_history": {
        "shared":  True,
        "purpose": "Daily ATM IV per symbol from NSE Bhavcopy, feeds IVR (Rule S-08). (Step 5)",
        "indexes": [
            {"keys": [("symbol", 1), ("date", 1)], "unique": True},
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    #  PER-USER — every document carries user_id
    # ═══════════════════════════════════════════════════════════════════

    "users": {
        "shared":  False,
        "purpose": "Login identity. (Step 1 — this step)",
        "indexes": [
            {"keys": [("username", 1)], "unique": True},
            {"keys": [("email", 1)],    "unique": True},
        ],
    },
    "broker_connections": {
        "shared":  False,
        "purpose": "One row per Kite/Dhan account; multiple per user allowed. (Step 2/5)",
        "indexes": [
            {"keys": [("user_id", 1), ("connection_id", 1)], "unique": True},
        ],
    },
    "user_rules": {
        "shared":  False,
        "purpose": "Per-user default-rule toggle state + custom rules. (Step 3)",
        "indexes": [
            {"keys": [("user_id", 1), ("rule_id", 1)], "unique": True},
        ],
    },
    "telegram_config": {
        "shared":  False,
        "purpose": "Per-user Telegram chat binding. (Step 10)",
        "indexes": [
            {"keys": [("user_id", 1)], "unique": True},
        ],
    },
    "action_plans": {
        "shared":  False,
        "purpose": "Per-user, NAMED stock shortlists from the Stock Selector — "
                   "multiple shortlists allowed per month, distinguished by name. (Step 9)",
        "indexes": [
            {"keys": [("user_id", 1), ("month_key", 1), ("shortlist_name", 1)], "unique": True},
        ],
    },
    "positions_cache": {
        "shared":  False,
        "purpose": "Per-user live strangle positions snapshot. (Step 9)",
        "indexes": [
            {"keys": [("user_id", 1), ("connection_id", 1)]},
        ],
    },
    "daily_pnl": {
        "shared":  False,
        "purpose": "Per-user end-of-day P&L snapshot, one doc per user per date. (Step 8)",
        "indexes": [
            {"keys": [("user_id", 1), ("date", 1)], "unique": True},
        ],
    },
    "trade_decisions": {
        "shared":  False,
        "purpose": "Per-user agent query history / GO-NOGO log. (Step 6)",
        "indexes": [
            {"keys": [("user_id", 1), ("created_at", -1)]},
        ],
    },
}


def shared_collections() -> list[str]:
    return [name for name, spec in COLLECTION_SCHEMA.items() if spec["shared"]]


def per_user_collections() -> list[str]:
    return [name for name, spec in COLLECTION_SCHEMA.items() if not spec["shared"]]


def print_schema_report() -> None:
    """Human-readable summary — used by scripts/init_db.py and verify_setup.py."""
    print(f"\n  SHARED collections ({len(shared_collections())}):")
    for name in shared_collections():
        idx_count = len(COLLECTION_SCHEMA[name]["indexes"])
        print(f"    • {name:<26} {idx_count} index(es)  —  {COLLECTION_SCHEMA[name]['purpose']}")

    print(f"\n  PER-USER collections ({len(per_user_collections())}):")
    for name in per_user_collections():
        idx_count = len(COLLECTION_SCHEMA[name]["indexes"])
        print(f"    • {name:<26} {idx_count} index(es)  —  {COLLECTION_SCHEMA[name]['purpose']}")
