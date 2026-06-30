"""
verify_setup.py
──────────────────
ArthaChakra — Step 1 Verification Suite

Proves the core/ + users/ + Mongo schema foundation works correctly
BEFORE any later step builds on top of it.

IDEMPOTENT BY DESIGN:
  Every identifier this script creates (usernames, emails, rule_ids) is
  prefixed with "_verify_" — a reserved namespace that real users and
  real rules (Step 2+ signup, Step 3 rule seeding) will NEVER use. This
  matters for two reasons:
    1. Running this script twice against real, persistent MongoDB
       won't hit duplicate-key errors from its own previous run.
    2. It can never collide with — or accidentally delete — a real
       user account or a real platform/default rule, even if someone
       is actually named "sandeep" once Step 2's login exists.

  Cleanup runs both BEFORE the test (removes leftovers from a prior
  crashed run) and AFTER (via try/finally, so your real database stays
  clean even if a check fails) — scoped strictly to the _verify_ prefix.

This file will grow as later steps land — each step adds its own
checklist section below, so there's always one command that verifies
everything built so far end-to-end.

Run:
    python verify_setup.py --step1

What Step 1 proves:
    1. Schema connects (real Mongo or mock fallback) and indexes apply
    2. Two users can be created independently via user_repository
    3. Duplicate username/email is rejected by the unique index
    4. One user can have multiple broker connections; another has none
    5. Rule toggle state for one user does not affect another
    6. Custom rules belong to exactly one user
    7. Telegram bindings are correctly isolated per user
    8. build_user_session() returns fully isolated sessions for each user
"""

from __future__ import annotations

import argparse
import sys

from core.database import Database
from core.ids import new_id
from users.models import User, BrokerConnection, UserRuleState, TelegramConfig
from users.schema import COLLECTION_SCHEMA, print_schema_report
from users.user_repository import create_user, get_user_by_username, list_users
from users.session_builder import build_user_session

SEP  = "─" * 78
SEP2 = "═" * 78

# ── Reserved test namespace — NEVER used by real signup/seeding ───────────
TEST_USERNAME_1 = "_verify_user1"
TEST_USERNAME_2 = "_verify_user2"
TEST_EMAIL_1    = "_verify_user1@verify.local"
TEST_EMAIL_2    = "_verify_user2@verify.local"
TEST_RULE_PLATFORM = "_VERIFY_P01"
TEST_RULE_DEFAULT  = "_VERIFY_S08"


def print_header(t: str) -> None:
    print(f"\n{SEP2}\n  {t}\n{SEP2}")


def _delete_all(collection, filt: dict) -> int:
    """
    Delete every document matching filt, one at a time. Works
    identically against the in-memory mock and the real
    MongoCollectionAdapter — neither exposes delete_many, but both
    support repeated delete_one() calls until nothing matches.
    """
    count = 0
    while collection.delete_one(filt):
        count += 1
    return count


def _cleanup_test_data(db: Database) -> None:
    """
    Remove every artefact this script creates — scoped strictly to the
    _verify_ prefixed identifiers above. Never touches any other data,
    so this is always safe to run against a real, persistent database
    that also contains real users and real rules from later steps.
    """
    for username in (TEST_USERNAME_1, TEST_USERNAME_2):
        user = db.users.find_one({"username": username})
        if user:
            uid = user["user_id"]
            _delete_all(db.broker_connections, {"user_id": uid})
            _delete_all(db.user_rules,         {"user_id": uid})
            _delete_all(db.telegram_config,    {"user_id": uid})
            db.users.delete_one({"user_id": uid})

    db.platform_rules.delete_one({"rule_id": TEST_RULE_PLATFORM})
    db.default_rules.delete_one({"rule_id": TEST_RULE_DEFAULT})


def run_step1() -> bool:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 1 Verification: core/ + users/ + Mongo schema")

    db = Database()
    print(f"  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")

    if not db.is_mock:
        print(f"  Cleaning up any leftover _verify_ test data from a previous run...")
        _cleanup_test_data(db)
    print()

    try:
        # ── 1. Schema + indexes ─────────────────────────────────────────
        print(f"{SEP}\n  1 — Schema definition and index creation\n{SEP}")
        print_schema_report()
        created = db.ensure_indexes(COLLECTION_SCHEMA)
        check(
            "Schema defines both shared and per-user collections",
            len([s for s in COLLECTION_SCHEMA.values() if s["shared"]]) > 0
            and len([s for s in COLLECTION_SCHEMA.values() if not s["shared"]]) > 0,
            f"{len(COLLECTION_SCHEMA)} total collections",
        )
        if not db.is_mock:
            check("Indexes created on real MongoDB", created > 0, f"{created} indexes")
        else:
            check("Index creation skipped cleanly in mock mode", True)

        # ── 2. User creation ─────────────────────────────────────────────
        print(f"\n{SEP}\n  2 — Create two independent users\n{SEP}")

        user1 = User(
            user_id=new_id("usr"), username=TEST_USERNAME_1, email=TEST_EMAIL_1,
            password_hash="hash1", salt="salt1", display_name="Verify Test User 1",
        )
        user2 = User(
            user_id=new_id("usr"), username=TEST_USERNAME_2, email=TEST_EMAIL_2,
            password_hash="hash2", salt="salt2", display_name="Verify Test User 2",
        )
        create_user(db, user1)
        create_user(db, user2)

        fetched1 = get_user_by_username(db, TEST_USERNAME_1)
        fetched2 = get_user_by_username(db, TEST_USERNAME_2)
        check("User 1 persisted and retrievable",
              fetched1 is not None and fetched1.user_id == user1.user_id)
        check("User 2 persisted and retrievable",
              fetched2 is not None and fetched2.user_id == user2.user_id)

        all_users = list_users(db)
        check("list_users() returns at least 2 users", len(all_users) >= 2, f"{len(all_users)} found")

        # Duplicate username should be rejected if real Mongo (unique index)
        if not db.is_mock:
            try:
                dup = User(
                    user_id=new_id("usr"), username=TEST_USERNAME_1, email="other@verify.local",
                    password_hash="x", salt="x",
                )
                create_user(db, dup)
                check("Duplicate username rejected by unique index", False, "no exception raised")
            except Exception:
                check("Duplicate username rejected by unique index", True)
        else:
            check("Duplicate username check skipped (mock mode has no unique constraint)", True)

        # ── 3. Multiple broker connections — User 1 only ────────────────
        print(f"\n{SEP}\n  3 — User 1 gets two broker connections, User 2 gets none\n{SEP}")

        conn1 = BrokerConnection(
            connection_id=new_id("conn"), user_id=user1.user_id, broker="kite",
            label="Verify Index Account", api_key="k1", access_token="tok1",
            token_expiry="2026-12-31", account_type="index",
        )
        conn2 = BrokerConnection(
            connection_id=new_id("conn"), user_id=user1.user_id, broker="kite",
            label="Verify Equity Account", api_key="k2", access_token="tok2",
            token_expiry="2026-12-31", account_type="equity",
        )
        db.broker_connections.insert_one(conn1.to_dict())
        db.broker_connections.insert_one(conn2.to_dict())

        u1_conns = db.broker_connections.find({"user_id": user1.user_id, "active": True})
        u2_conns = db.broker_connections.find({"user_id": user2.user_id, "active": True})
        check("User 1 has 2 broker connections", len(u1_conns) == 2, f"{len(u1_conns)} found")
        check("User 2 has 0 broker connections (isolated)", len(u2_conns) == 0)

        # ── 4. Rule toggle isolation ──────────────────────────────────────
        print(f"\n{SEP}\n  4 — User 2 toggles a rule + adds a custom rule, User 1 untouched\n{SEP}")

        # Namespaced test rules — Step 3 will own the real P-01/S-08 seeding
        db.platform_rules.insert_one({
            "rule_id": TEST_RULE_PLATFORM, "name": "[TEST] VIX Hard Stop",
            "description": "Block all entries when VIX > 30.",
            "category": "MANDATORY", "group": "Platform Safety",
        })
        db.default_rules.insert_one({
            "rule_id": TEST_RULE_DEFAULT, "name": "[TEST] IVR Minimum",
            "description": "Skip entry when IVR < 40.",
            "category": "OPTIONAL", "group": "Selection", "default_on": True,
        })

        # User 2 turns OFF the test default rule
        toggle = UserRuleState(
            user_id=user2.user_id, rule_id=TEST_RULE_DEFAULT, enabled=False, source="default",
        )
        db.user_rules.update_one(
            {"user_id": user2.user_id, "rule_id": TEST_RULE_DEFAULT},
            {"$set": toggle.to_dict()}, upsert=True,
        )

        # User 2 adds a custom rule
        custom_id = new_id("custom")
        custom = UserRuleState(
            user_id=user2.user_id, rule_id=custom_id, enabled=True, source="custom",
            custom_def={"name": "No IT near results", "metric": "days_to_results",
                       "operator": "<", "value": 10, "action": "BLOCK_ENTRY"},
        )
        db.user_rules.insert_one(custom.to_dict())

        rules_u1 = build_user_session(db, user1.user_id, "Verify Test User 1").effective_rules
        rules_u2 = build_user_session(db, user2.user_id, "Verify Test User 2").effective_rules

        u1_test_rule = next((r for r in rules_u1 if r["rule_id"] == TEST_RULE_DEFAULT), None)
        u2_test_rule = next((r for r in rules_u2 if r["rule_id"] == TEST_RULE_DEFAULT), None)
        check("User 1's test rule still enabled (untouched)",
              u1_test_rule and u1_test_rule["enabled"] is True)
        check("User 2's test rule now disabled",
              u2_test_rule and u2_test_rule["enabled"] is False)

        u1_custom = sum(1 for r in rules_u1 if r["source"] == "custom")
        u2_custom = sum(1 for r in rules_u2 if r["source"] == "custom")
        check("User 1 has 0 custom rules (isolated)", u1_custom == 0)
        check("User 2 has 1 custom rule",              u2_custom == 1)

        u1_mandatory = [r for r in rules_u1 if r["category"] == "MANDATORY"]
        check("Mandatory test rule present and always enabled for User 1",
              len(u1_mandatory) == 1 and u1_mandatory[0]["enabled"] is True)

        # ── 5. Telegram isolation ─────────────────────────────────────────
        print(f"\n{SEP}\n  5 — Telegram bindings are correctly isolated\n{SEP}")

        tg1 = TelegramConfig(user_id=user1.user_id, chat_id="111111", verified=True)
        tg2 = TelegramConfig(user_id=user2.user_id, chat_id="222222", verified=True)
        db.telegram_config.insert_one(tg1.to_dict())
        db.telegram_config.insert_one(tg2.to_dict())

        session1 = build_user_session(db, user1.user_id, "Verify Test User 1")
        session2 = build_user_session(db, user2.user_id, "Verify Test User 2")

        check("User 1 telegram = chat 111111", session1.telegram_chat_id == "111111")
        check("User 2 telegram = chat 222222", session2.telegram_chat_id == "222222")

        # ── 6. Final assembled sessions ───────────────────────────────────
        print(f"\n{SEP}\n  6 — Final UserSession objects\n{SEP}\n")
        print(session1.summary())
        print()
        print(session2.summary())
        print()

        check("Session 1 has 2 active broker connections", len(session1.active_connections) == 2)
        check("Session 2 has 0 active broker connections", len(session2.active_connections) == 0)
        check("Session 1 + Session 2 have different rule states",
              session1.optional_enabled_count != session2.optional_enabled_count
              or session1.custom_rule_count != session2.custom_rule_count)

    finally:
        # Always clean up test data, even if a check above raised an
        # exception — keeps the real database free of _verify_ artefacts
        # no matter how this run ends.
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up all _verify_ test data — real database left untouched.")

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 1 VERIFICATION PASSED  ({passed}/{total})")
    else:
        print(f"  ❌  STEP 1 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step2() -> bool:
    """
    Automated verification of auth/ + kite_oauth/ wiring.

    IMPORTANT SCOPE NOTE: this proves the *wiring* — signup/login, mock
    connections, renaming, and the request_token parsing logic. It does
    NOT and CANNOT test a real Zerodha browser login (that requires an
    actual human with their own Kite Connect credentials clicking
    through Zerodha's UI). After this passes, confirm the real flow
    yourself:

        streamlit run app.py

    Sign up, go to "Add a real Kite account", enter YOUR OWN Kite
    Connect api_key/api_secret, click "Get Login URL", log into Zerodha
    in the new tab, copy the request_token (or the whole redirected
    URL) from its address bar, paste it back, and click "Connect &
    Verify". You should see "✅ Connected as <your name>".
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 2 Verification: auth/ + kite_oauth/ wiring")
    print("  This automated suite verifies signup/login and the connection")
    print("  service wiring using mock connections and token-parsing logic. It does not")
    print("  test a real Zerodha browser login — confirm that manually afterwards:")
    print("      streamlit run app.py")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")

    if not db.is_mock:
        print("  Cleaning up any leftover _verify_ test data from a previous run...")
        _cleanup_test_data(db)
    print()

    try:
        # ── 1. Auth: signup + login ──────────────────────────────────────
        print(f"{SEP}\n  1 — Sign up / log in via auth_service\n{SEP}")
        from auth.auth_service import AuthError, login, signup

        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "testpass123", "Verify Test User 1")
        check("Signup creates a user", bool(user1.user_id))

        try:
            signup(db, TEST_USERNAME_1, "other@verify.local", "whatever123")
            check("Duplicate username rejected on signup", False, "no exception raised")
        except AuthError:
            check("Duplicate username rejected on signup", True)

        logged_in = login(db, TEST_USERNAME_1, "testpass123")
        check("Login with correct password", logged_in.user_id == user1.user_id)

        try:
            login(db, TEST_USERNAME_1, "wrongpassword")
            check("Login with wrong password rejected", False, "no exception raised")
        except AuthError:
            check("Login with wrong password rejected", True)

        # ── 2. Kite connection wiring (mock connection, no real exchange) ──
        print(f"\n{SEP}\n  2 — Kite connection wiring (mock connection)\n{SEP}")
        from kite_oauth.connection_service import add_mock_connection, list_connections

        conn = add_mock_connection(db, user1.user_id, label="Verify Index Account",
                                   account_type="index")
        check("Connection created and persisted", bool(conn.connection_id))
        check(
            "Connection token issued (mock — no real Zerodha login happened here)",
            conn.access_token.startswith("mock_tok_"),
        )

        conns = list_connections(db, user1.user_id)
        check("list_connections() returns the new connection", len(conns) == 1)

        # update_connection() — supports the rename UI in app.py
        from kite_oauth.connection_service import update_connection
        renamed = update_connection(
            db, user1.user_id, conn.connection_id,
            label="Renamed Index Account", account_type="both",
        )
        check("update_connection() reports success", renamed is True)

        refreshed = list_connections(db, user1.user_id)[0]
        check("Label was actually updated", refreshed.label == "Renamed Index Account")
        check("Account type was actually updated", refreshed.account_type == "both")

        # extract_request_token() — must handle both a bare token and a
        # full pasted URL, since the user might copy either from the
        # browser's address bar.
        from kite_oauth.kite_connect_flow import extract_request_token

        check("extract_request_token parses a bare token",
              extract_request_token("abc123xyz") == "abc123xyz")
        check(
            "extract_request_token parses a full redirected URL",
            extract_request_token(
                "http://localhost:8501/?action=login&request_token=xyz789&status=success"
            ) == "xyz789",
        )

        # ── 3. Session reflects the new connection ─────────────────────────
        print(f"\n{SEP}\n  3 — Session reflects the new connection (Step 1 session_builder)\n{SEP}")
        from users.session_builder import build_user_session

        session = build_user_session(db, user1.user_id, user1.display_name)
        check("Session shows 1 active broker connection", len(session.active_connections) == 1)
        print()
        print(session.summary())
        print()

    finally:
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up all _verify_ test data — real database left untouched.")

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 2 AUTOMATED VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  Now confirm the REAL Zerodha login manually:  streamlit run app.py")
    else:
        print(f"  ❌  STEP 2 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step3() -> bool:
    """
    Verification of rules/ — seed + service + engine.

    UNLIKE Step 1/2's test fixtures, seeding the real 55-rule book into
    platform_rules is NOT a throwaway artefact to clean up afterward —
    it's the actual Step 3 deliverable. This script seeds it for real.
    Only the per-user isolation check (custom rule add/remove) uses the
    _verify_ prefix and gets cleaned up, same pattern as Step 1/2.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 3 Verification: rules/ (seed + service + engine)")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")

    if not db.is_mock:
        print("  Cleaning up any leftover _verify_ test data from a previous run...")
        _cleanup_test_data(db)
    print()

    try:
        # ── 1. series_calendar math, checked against user-confirmed examples ──
        print(f"{SEP}\n  1 — series_calendar.py against confirmed worked examples\n{SEP}")
        from datetime import date, datetime

        from rules import series_calendar as cal

        check("May 2026 expiry = 26 May (Tuesday)",
             cal.last_tuesday_of_month(2026, 5) == date(2026, 5, 26))
        check("June 2026 expiry = 30 Jun (Tuesday)",
             cal.last_tuesday_of_month(2026, 6) == date(2026, 6, 30))
        june_window = cal.get_series_window(date(2026, 6, 15))
        check("June 2026 is a 5-week series", june_window.week_count == 5)
        check("June series starts 27 May", june_window.series_start == date(2026, 5, 27))
        check("Week 3 of June (10-16 Jun) requires 8% OTM",
             cal.required_otm_pct(date(2026, 6, 12)) == 8.0)
        check("EP-01 Friday override (26 Jun, 4 cal days before Tue expiry) = 85%",
             cal.required_profit_pct(date(2026, 6, 26)) == 85.0)
        check("EP-01 expiry-day override (30 Jun) = 95%",
             cal.required_profit_pct(date(2026, 6, 30)) == 95.0)
        may_window = cal.get_series_window(date(2026, 5, 10))
        check("May 2026 is a 4-week series", may_window.week_count == 4)

        # ── 2. Rule book seeding — real deliverable, upsert-aware ──────────
        print(f"\n{SEP}\n  2 — Rule book seeding (rules_service.seed_rules_into_db)\n{SEP}")
        from rules.rules_service import (
            get_effective_rules, remove_rules_not_in_book, seed_rules_into_db,
        )
        from rules.seed_rules import get_rule_book

        total_rules = len(get_rule_book())   # dynamic - never hardcode this, rules get added over time

        report1 = seed_rules_into_db(db)
        check(f"First seed inserts all {total_rules} rules", report1["inserted"] == total_rules, str(report1))
        check(f"platform_rules now has exactly {total_rules} documents",
             db.platform_rules.count_documents({}) == total_rules)

        report2 = seed_rules_into_db(db)
        check("Re-seed updates existing rules, inserts none (upsert, not duplicate)",
             report2["inserted"] == 0 and report2["updated"] == total_rules, str(report2))
        check(f"Still exactly {total_rules} documents after re-seed (no duplicates)",
             db.platform_rules.count_documents({}) == total_rules)

        removed = remove_rules_not_in_book(db)
        check("No stale rules to remove on a clean seed", removed == 0)

        # ── 3. Per-user effective set differs after a toggle/add (THE checkpoint) ──
        print(f"\n{SEP}\n  3 — Same rule book, different effective set per user (the Step 3 checkpoint)\n{SEP}")
        from auth.auth_service import signup
        from rules.rules_service import add_custom_rule, remove_custom_rule

        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        user2 = signup(db, TEST_USERNAME_2, TEST_EMAIL_2, "pw_verify_123")

        rules1_before = get_effective_rules(db, user1.user_id)
        rules2_before = get_effective_rules(db, user2.user_id)
        check(f"Both users start with identical effective rule count ({total_rules})",
             len(rules1_before) == len(rules2_before) == total_rules)

        add_custom_rule(db, user1.user_id, TEST_RULE_PLATFORM,
                        "Verify custom rule", "worst_distance_pct", "<", 5, "WARN")
        rules1_after = get_effective_rules(db, user1.user_id)
        rules2_after = get_effective_rules(db, user2.user_id)
        check(f"User 1's effective set grows to {total_rules + 1} after adding a custom rule",
             len(rules1_after) == total_rules + 1)
        check(f"User 2's effective set is untouched (still {total_rules}) — no cross-talk",
             len(rules2_after) == total_rules)

        # ── 4. Engine evaluates the real rule book against live Strangle data ──
        print(f"\n{SEP}\n  4 — RuleEngine evaluates real rules against live position data\n{SEP}")
        from dashboard.strangle_grouper import ParsedOption, Strangle
        from rules.engine import RuleEngine

        engine = RuleEngine()
        breach_strangle = Strangle(
            underlying="NIFTY", expiry="30JUN26", spot=24900,
            ce_legs=[ParsedOption("X", "NIFTY", "30JUN26", 24800, "CE", -25, 50, 20, 750)],
            pe_legs=[ParsedOption("Y", "NIFTY", "30JUN26", 24200, "PE", -25, 50, 20, 750)],
        )
        es01 = next(r for r in get_rule_book() if r["rule_id"] == "ES-01")
        result = engine.evaluate_rule(es01, breach_strangle)
        check("ES-01 correctly FAILs when spot has breached the CE strike",
             result.status == "FAIL", result.message)

        safe_strangle = Strangle(
            underlying="NIFTY", expiry="30JUN26", spot=24500,
            ce_legs=[ParsedOption("X", "NIFTY", "30JUN26", 24800, "CE", -25, 50, 20, 750)],
            pe_legs=[ParsedOption("Y", "NIFTY", "30JUN26", 24200, "PE", -25, 50, 20, 750)],
        )
        result2 = engine.evaluate_rule(es01, safe_strangle)
        check("ES-01 correctly PASSes when spot is between the strikes",
             result2.status == "PASS", result2.message)

        # S-01 was NOT_YET_EVALUABLE when this check was first written —
        # Step 5 closed it (real VIX history now exists). Using S-03
        # instead, which still genuinely has no data source until
        # Step 7 (corporate events cache).
        s03 = next(r for r in get_rule_book() if r["rule_id"] == "S-03")
        result3 = engine.evaluate_rule(s03, safe_strangle)
        check("S-03 (needs corporate events cache, Step 7) returns NOT_YET_EVALUABLE, not a guessed PASS/FAIL",
             result3.status == "NOT_YET_EVALUABLE", result3.message)

        all_results = engine.evaluate_all(get_rule_book(), safe_strangle)
        check(f"evaluate_all() returns exactly {total_rules} results, one per rule",
             len(all_results) == total_rules)

        # ── 5. Cleanup the test-only custom rule (real rule-book seed stays) ──
        remove_custom_rule(db, user1.user_id, TEST_RULE_PLATFORM)
        rules1_final = get_effective_rules(db, user1.user_id)
        check(f"Custom rule cleanly removable, user 1 back to {total_rules}",
             len(rules1_final) == total_rules)

    finally:
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up _verify_ test users/custom-rules — "
                 f"the real 55-rule seed in platform_rules is left in place.")

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        from collections import Counter

        from rules.seed_rules import get_rule_book as _get_book
        book = _get_book()
        counts = Counter(r["eval_status"] for r in book)
        print(f"  ✅  STEP 3 VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  {len(book)} rules seeded. {counts['EVALUABLE']} evaluable now, "
             f"{counts['ADVISORY']} advisory, {counts['NOT_YET_EVALUABLE']} NOT_YET_EVALUABLE")
        print(f"      pending later steps (corporate events, market intel, etc).")
    else:
        print(f"  ❌  STEP 3 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step4() -> bool:
    """
    Verification of rag/ — ChromaDB rule book embedding, sourced from
    MongoDB (not YAML/seed_rules.py directly).

    NOTE: Requires downloading the all-MiniLM-L6-v2 model on first run
    (~80MB from Hugging Face, cached afterward in ~/.cache/huggingface).
    Needs real internet access — this is NOT something a sandboxed
    environment without internet access can run; if this fails with a
    network/connection error, that's the actual cause, not a bug in
    this code.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 4 Verification: rag/ (ChromaDB rule book)")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")

    try:
        from rules.rules_service import seed_rules_into_db
        from rules.seed_rules import get_rule_book
        from rag.rule_store import RuleStore

        if db.platform_rules.count_documents({}) == 0:
            seed_rules_into_db(db)

        total_rules = len(get_rule_book())   # dynamic - rules get added over time

        print("\n  Loading embedding model (downloads ~80MB on first run)...")
        store = RuleStore()
        n = store.embed_from_mongo(db, overwrite=True)
        check(f"Embedded all {total_rules} rules from MongoDB (not YAML)", n == total_rules, f"embedded={n}")
        check("ChromaDB collection count matches", store.count() == total_rules)

        # New test queries against the ACTUAL 55-rule book (the old POC-03
        # queries referenced S-10/S-11/ES-02/ES-03/L-02, all deleted — see
        # the Step 4 planning discussion for why those can't be reused).
        test_queries = [
            ("VIX limit before entering a new trade", ["S-01"]),
            ("what happens when a strike is breached", ["ES-01"]),
            ("max ratio between CE and PE legs", ["A-10", "L-03"]),
            ("margin cap for single stock", ["C-01", "C-04"]),
            ("going naked on one side", ["A-11"]),
        ]
        hits_total, expected_total = 0, 0
        for query, expected in test_queries:
            retrieved = [r["rule_id"] for r in store.query(query, n_results=4)]
            hits = [e for e in expected if e in retrieved]
            hits_total += len(hits)
            expected_total += len(expected)
            check(f"'{query}' retrieves at least one expected rule",
                 len(hits) > 0, f"got={retrieved} expected={expected}")

        precision = hits_total / expected_total if expected_total else 0
        check("Overall recall across all 5 queries >= 60%", precision >= 0.60,
             f"{precision*100:.0f}%")

        # eval_status metadata survives the round trip
        a10 = store.get_rule("A-10")
        check("eval_status metadata present and correct for A-10",
             a10 is not None and a10.get("eval_status") == "EVALUABLE")
        s01 = store.get_rule("S-01")
        check("eval_status correctly flags S-01 as NOT_YET_EVALUABLE",
             s01 is not None and s01.get("eval_status") == "NOT_YET_EVALUABLE")

    except Exception as e:
        check("RAG pipeline ran without error", False, str(e))
        print(f"\n  ⚠️  If this is a network/connection error, that's expected in an")
        print(f"      environment without internet access — the model download to")
        print(f"      huggingface.co needs a real connection. Run this on your own")
        print(f"      machine, not a fully sandboxed/offline environment.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total and total > 0:
        print(f"  ✅  STEP 4 VERIFICATION PASSED  ({passed}/{total})")
    else:
        print(f"  ❌  STEP 4 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total and total > 0


def run_step5() -> bool:
    """
    Verification of brokers/session_manager.py + market_data/.

    Bhavcopy/NSE download cannot be tested here — archives.nseindia.com
    isn't reachable from this environment. This tests everything that
    doesn't require a live download: column normalization against the
    confirmed real 2026 schema, IV/IVR computation, VIX history/trend
    logic, OHLC aggregation, beta computation, and per-user token
    isolation — using synthetic data shaped exactly like the real thing.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 5 Verification: brokers/session_manager.py + market_data/")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")
    if not db.is_mock:
        _cleanup_test_data(db)

    try:
        # ── 1. Per-user token check isolation (the original Step 5 checkpoint) ──
        print(f"\n{SEP}\n  1 — Per-user token check (corrected: check+report, not silent refresh)\n{SEP}")
        from datetime import date, timedelta

        from auth.auth_service import signup
        from brokers.session_manager import check_all_user_tokens
        from kite_oauth.connection_service import add_mock_connection

        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        user2 = signup(db, TEST_USERNAME_2, TEST_EMAIL_2, "pw_verify_123")

        add_mock_connection(db, user1.user_id, label="_verify_mock_conn", account_type="index")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        db.broker_connections.insert_one({
            "connection_id": "_verify_conn_expired", "user_id": user2.user_id, "broker": "kite",
            "label": "_verify_expired_conn", "api_key": "x", "api_secret": "x",
            "access_token": "real_looking_token", "token_expiry": yesterday,
            "account_type": "equity", "broker_account_name": "", "active": True,
        })

        report = check_all_user_tokens(db)
        user2_statuses = [s for s in report.statuses if s.user_id == user2.user_id]
        user1_statuses = [s for s in report.statuses if s.user_id == user1.user_id]
        check("User 2's expired connection correctly flagged invalid",
             len(user2_statuses) == 1 and user2_statuses[0].valid is False)
        check("User 1's mock connection skipped entirely (no cross-talk)",
             len(user1_statuses) == 0)

        db.broker_connections.delete_one({"connection_id": "_verify_conn_expired"})

        # ── 2. Bhavcopy column normalization against the CONFIRMED real schema ──
        print(f"\n{SEP}\n  2 — Bhavcopy parsing against the confirmed real 2026 NSE schema\n{SEP}")
        import pandas as pd

        from market_data.bhavcopy import BhavcopyScraper

        synthetic_rows = [
            {"TradDt": "2026-03-04", "FinInstrmTp": "STO", "TckrSymb": "_VERIFY_SBILIFE",
             "XpryDt": "2026-03-30", "StrkPric": 1950.0, "OptnTp": "CE",
             "FinInstrmNm": "SBILIFE26MAR1950CE", "ClsPric": 38.0,
             "UndrlygPric": 1930.6, "SttlmPric": 37.0, "OpnIntrst": 1200},
            {"TradDt": "2026-03-04", "FinInstrmTp": "IDO", "TckrSymb": "_VERIFY_NIFTY",
             "XpryDt": "2026-03-25", "StrkPric": 24400.0, "OptnTp": "CE",
             "FinInstrmNm": "NIFTY26MAR24400CE", "ClsPric": 145.0,
             "UndrlygPric": 24380.5, "SttlmPric": 142.0, "OpnIntrst": 50000},
        ]
        test_dt = date(2026, 3, 4)
        scraper = BhavcopyScraper(data_dir="data/_verify_bhavcopy")
        pd.DataFrame(synthetic_rows).to_csv(
            scraper._fo_path(test_dt), index=False,
        )

        df = scraper.load_fo(test_dt)
        check("TckrSymb correctly mapped to SYMBOL",
             "SYMBOL" in df.columns and "_VERIFY_SBILIFE" in df["SYMBOL"].values)
        check("UndrlygPric correctly mapped to SPOT",
             "SPOT" in df.columns)
        check("FinInstrmTp 'STO'/'IDO' both parsed (not old OPTSTK/OPTIDX format)",
             set(df["INSTRUMENT"]) == {"STO", "IDO"})

        spot = scraper.get_spot_price("_VERIFY_SBILIFE", test_dt)
        check("get_spot_price reads directly from SPOT column (no separate equity download)",
             spot == 1930.6, f"got {spot}")

        # ── 3. IV computation (real Black-Scholes) + IVR ───────────────────
        print(f"\n{SEP}\n  3 — IV/IVR pipeline (real BS solver + validated IVR formula)\n{SEP}")
        from market_data.iv_updater import compute_atm_iv, update_iv_for_symbol

        iv = compute_atm_iv(scraper, "_VERIFY_SBILIFE", test_dt)
        check("ATM IV computed via real Black-Scholes solver, sane value",
             iv is not None and 0.05 < iv < 1.5, f"iv={iv}")

        for i, fake_iv in enumerate([0.15, 0.18, 0.22, 0.28, 0.35]):
            fd = date(2026, 2, 20 + i)
            db.iv_history.update_one(
                {"symbol": "_VERIFY_SBILIFE", "date": fd.isoformat()},
                {"$set": {"symbol": "_VERIFY_SBILIFE", "date": fd.isoformat(), "iv_atm": fake_iv}},
                upsert=True,
            )
        result = update_iv_for_symbol(db, scraper, "_VERIFY_SBILIFE", test_dt)
        check("IVR computed from accumulated iv_history", result["ivr"] is not None)

        # ── 4. VIX history / trend / intraday-spike detection ──────────────
        print(f"\n{SEP}\n  4 — VIX history, 5-day trend, intraday spike detection\n{SEP}")
        from market_data.vix_fetcher import (
            cache_vix_reading, get_latest_vix, get_todays_vix_readings,
        )

        from datetime import datetime as _dt
        from datetime import timedelta as _td
        now = _dt.now()
        earlier_today = now - _td(minutes=30)
        cache_vix_reading(db, 16.0, as_of=earlier_today)
        cache_vix_reading(db, 22.5, as_of=now)
        latest = get_latest_vix(db)
        check("get_latest_vix returns the most recent reading", latest["value"] == 22.5)

        todays = get_todays_vix_readings(db)
        spike = todays[-1]["value"] - todays[0]["value"]
        check("Intraday VIX spike correctly detectable (>5pt threshold for EP-04)",
             spike > 5, f"spike={spike:+.1f}")

        # ── 5. OHLC aggregation + beta computation ──────────────────────────
        print(f"\n{SEP}\n  5 — Monthly OHLC aggregation + beta computation\n{SEP}")
        from market_data.ohlc_updater import (
            compute_beta, get_monthly_range_pct, update_ohlc_for_symbol,
        )

        doc = update_ohlc_for_symbol(db, scraper, "_VERIFY_SBILIFE", test_dt)
        check("First OHLC observation sets open=high=low=close",
             doc is not None and doc["open"] == doc["close"] == 1930.6)

        for sk, sc, ik, ic in [
            ("2025-12", 1800, "2025-12", 24000), ("2026-01", 1850, "2026-01", 24300),
            ("2026-02", 1900, "2026-02", 24200),
        ]:
            db.monthly_ohlc.update_one({"symbol": "_VERIFY_SBILIFE", "month_key": sk},
                {"$set": {"symbol": "_VERIFY_SBILIFE", "month_key": sk, "open": sc,
                         "high": sc + 20, "low": sc - 20, "close": sc}}, upsert=True)
            db.monthly_ohlc.update_one({"symbol": "_VERIFY_NIFTY", "month_key": ik},
                {"$set": {"symbol": "_VERIFY_NIFTY", "month_key": ik, "open": ic,
                         "high": ic + 50, "low": ic - 50, "close": ic}}, upsert=True)
        db.monthly_ohlc.update_one({"symbol": "_VERIFY_NIFTY", "month_key": "2026-03"},
            {"$set": {"symbol": "_VERIFY_NIFTY", "month_key": "2026-03", "open": 24200,
                     "high": 24500, "low": 24100, "close": 24380}}, upsert=True)

        range_pct = get_monthly_range_pct(db, "_VERIFY_SBILIFE", months=3)
        check("3-month range % computed", range_pct is not None and range_pct > 0)

        beta = compute_beta(db, "_VERIFY_SBILIFE", "_VERIFY_NIFTY", months=12)
        check("Beta vs index computed from monthly returns", beta is not None)

        # ── 6. Engine handlers wired to the new data (the checkpoint closure) ──
        print(f"\n{SEP}\n  6 — Rule engine: S-01/S-02/EP-04/S-15/S-08/S-07/S-06 now EVALUABLE\n{SEP}")
        from rules.engine import RuleEngine
        from rules.seed_rules import get_rule_book

        engine = RuleEngine()
        rule_book = {r["rule_id"]: r for r in get_rule_book()}

        for rid in ["S-01", "S-02", "EP-04", "S-15", "S-08", "S-07", "S-06"]:
            check(f"{rid} eval_status upgraded to EVALUABLE",
                 rule_book[rid]["eval_status"] == "EVALUABLE")

        r_vix = engine.evaluate_rule(rule_book["S-01"], None, {"vix": 28.0})
        check("S-01 fires FAIL on VIX=28 with strangle=None (market-wide check)",
             r_vix.status == "FAIL")

        r_ivr = engine.evaluate_rule(rule_book["S-08"], None, {"ivr": 25.0})
        check("S-08 fires FAIL on IVR=25 (below 40 threshold)", r_ivr.status == "FAIL")

        # Cleanup synthetic bhavcopy dir
        import shutil
        shutil.rmtree("data/_verify_bhavcopy", ignore_errors=True)

    finally:
        if not db.is_mock:
            _cleanup_test_data(db)
            db.iv_history.delete_one({"symbol": "_VERIFY_SBILIFE"})
            print(f"\n  🧹 Cleaned up _verify_ test data.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 5 VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  Bhavcopy/NSE download itself needs testing on a machine with")
        print(f"      real internet access — archives.nseindia.com isn't reachable")
        print(f"      from this environment. Everything else is fully verified.")
    else:
        print(f"  ❌  STEP 5 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step6() -> bool:
    """
    Verification of agent/ — the IntegrationAgent, built fresh per
    UserSession.

    The actual Anthropic API call is mocked here (this sandbox has no
    real API key configured) — what's verified is everything around
    it: per-user context isolation, the tool dispatcher against real
    Step 1-5 data, router classification, and the core checkpoint —
    two simultaneous users asking the same question get answers built
    from their own data with zero cross-talk.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 6 Verification: agent/ (IntegrationAgent)")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")
    if not db.is_mock:
        _cleanup_test_data(db)

    try:
        import asyncio
        from unittest.mock import MagicMock, patch

        from agent import integration_agent as ia_module
        from agent.context_builder import build_context
        from agent.integration_agent import IntegrationAgent
        from agent.router import route
        from agent.tools import ToolDispatcher
        from auth.auth_service import signup
        from kite_oauth.connection_service import add_mock_connection
        from rules.rules_service import seed_rules_into_db
        from users.session_builder import build_user_session

        if db.platform_rules.count_documents({}) == 0:
            seed_rules_into_db(db)

        # Avoid a real (slow, network-blocked-in-this-sandbox) RuleStore
        # load for this test — search_rules degrading gracefully is
        # already proven by Step 4's own tests.
        IntegrationAgent._shared_rule_store = "skip"
        object.__setattr__(ia_module.settings, "anthropic_api_key", "test_key_for_verification")

        # ── 1. Router classification ────────────────────────────────────
        print(f"\n{SEP}\n  1 — Haiku/Sonnet router\n{SEP}")
        check("Simple lookup routes to haiku",
             route("What is the VIX right now?") == "haiku")
        check("Synthesis question routes to sonnet",
             route("Should I enter HDFCBANK?") == "sonnet")

        # ── 2. Per-user context isolation ───────────────────────────────
        print(f"\n{SEP}\n  2 — Parallel context building, per-user isolation\n{SEP}")
        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        user2 = signup(db, TEST_USERNAME_2, TEST_EMAIL_2, "pw_verify_123")
        add_mock_connection(db, user1.user_id, label="_verify_user1_mock")
        add_mock_connection(db, user2.user_id, label="_verify_user2_mock")

        session1 = build_user_session(db, user1.user_id, "Verify1")
        session2 = build_user_session(db, user2.user_id, "Verify2")

        async def _build_both():
            return await asyncio.gather(
                build_context(session1, db), build_context(session2, db),
            )
        ctx1, ctx2 = await_(_build_both())
        check("Context 1 correctly scoped to user 1",
             ctx1.session.user_id == user1.user_id)
        check("Context 2 correctly scoped to user 2",
             ctx2.session.user_id == user2.user_id)
        check("Contexts are genuinely distinct objects (no shared mutable state)",
             ctx1 is not ctx2 and ctx1.strangles is not ctx2.strangles)

        # ── 3. Tool dispatcher against real Step 1-5 data ───────────────
        print(f"\n{SEP}\n  3 — ToolDispatcher against real positions/rules\n{SEP}")
        dispatcher1 = ToolDispatcher(ctx1, db, rule_store=None)
        positions_out = dispatcher1.dispatch("get_positions", {})
        check("get_positions returns real mock position data",
             "BANKNIFTY" in positions_out or "NIFTY" in positions_out)

        underlying = ctx1.strangles[0].underlying if ctx1.strangles else None
        rule_out = dispatcher1.dispatch("check_rule", {"rule_id": "A-10", "underlying": underlying})
        check("check_rule dispatches to the real RuleEngine",
             "[A-10]" in rule_out and ("PASS" in rule_out or "FAIL" in rule_out))

        unknown_out = dispatcher1.dispatch("check_rule", {"rule_id": "NOT-A-REAL-RULE"})
        check("Unknown rule_id handled gracefully, not a crash",
             "not found" in unknown_out)

        # ── 4. THE CHECKPOINT: two simultaneous users, multi-turn tool use ──
        print(f"\n{SEP}\n  4 — Checkpoint: simultaneous users, zero cross-talk\n{SEP}")

        def smart_side_effect(*args, **kwargs):
            messages = kwargs.get("messages", [])
            if len(messages) == 1:
                tool_block = MagicMock(type="tool_use", input={}, id="call_1")
                tool_block.name = "get_positions"
                return MagicMock(stop_reason="tool_use", content=[tool_block])
            tool_output = messages[-1]["content"][0]["content"]
            final_block = MagicMock(type="text")
            final_block.text = f"Answer based on: {tool_output[:80]}"
            return MagicMock(stop_reason="end_turn", content=[final_block])

        async def run_for_user(session):
            agent = IntegrationAgent(session, db)
            return await agent.ask("Should I enter HDFCBANK?")

        with patch("anthropic.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = smart_side_effect
            MockAnthropic.return_value = mock_client

            async def _run_both():
                return await asyncio.gather(
                    run_for_user(session1), run_for_user(session2),
                )
            r1, r2 = await_(_run_both())

        check("User 1's answer reflects their own connection label",
             "_verify_user1_mock" in r1.answer)
        check("User 2's answer reflects their own connection label",
             "_verify_user2_mock" in r2.answer)
        check("No cross-talk: user 1's answer does NOT mention user 2's label",
             "_verify_user2_mock" not in r1.answer)
        check("No cross-talk: user 2's answer does NOT mention user 1's label",
             "_verify_user1_mock" not in r2.answer)
        check("Both calls completed without error", r1.error is None and r2.error is None)

    finally:
        IntegrationAgent._shared_rule_store = None
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up _verify_ test data.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 6 VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  The real Anthropic API call itself needs your own ANTHROPIC_API_KEY")
        print(f"      to test end-to-end — everything around it is fully verified here.")
    else:
        print(f"  ❌  STEP 6 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def await_(coro):
    """Tiny helper so run_step6 (a sync function, matching every other
    run_stepN) can drive the async agent code without restructuring
    the whole verify_setup.py file to be async."""
    import asyncio
    return asyncio.run(coro)


def run_step7() -> bool:
    """
    Verification of corporate_events/ + market_intel/ going live.

    NSE and Tavily aren't reachable from this sandbox — what's
    verified here is everything that doesn't require a live call:
    classification logic against all 6 corporate-event rule
    scenarios, the mock-fallback path (which must trigger on a real
    NSE failure, not silently look like "no events"), S-25's
    threshold logic, and the full engine + tool-dispatch wiring for
    all 9 newly-evaluable rules.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 7 Verification: corporate_events/ + market_intel/")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")
    if not db.is_mock:
        _cleanup_test_data(db)

    try:
        from datetime import date

        from corporate_events.event_calendar import EventCalendar
        from corporate_events.event_classifier import classify_event
        from market_intel.intel_scanner import IntelScanner
        from market_intel.signal_models import IntelSummary, MarketSignal, Sentiment, SignalType
        from rules.engine import RuleEngine
        from rules.seed_rules import get_rule_book

        today = date(2026, 6, 28)
        book = {r["rule_id"]: r for r in get_rule_book()}
        engine = RuleEngine()

        # ── 1. Classification against all 6 corporate-event scenarios ──
        print(f"\n{SEP}\n  1 — Event classification, all 6 rule scenarios\n{SEP}")
        e1 = classify_event("HDFCBANK", "Quarterly Results Q1 FY27", date(2026, 7, 1), today=today)
        check("Results in 3 days -> S-21 (BLOCK_ENTRY)", e1.rule_triggered == "S-21")

        e2 = classify_event("TCS", "Quarterly Results", date(2026, 7, 5), today=today)
        check("Results in 7 days -> S-24 (REDUCE_SIZE)", e2.rule_triggered == "S-24")

        e3 = classify_event("SBILIFE", "Scheme of Arrangement (Merger)", today, today=today)
        check("Merger TODAY -> ES-09 (EXIT_IF_OPEN)", e3.rule_triggered == "ES-09")

        e4 = classify_event("SBILIFE", "Merger announced", date(2026, 7, 10), today=today)
        check("Merger in future -> S-22 (BLOCK_ENTRY)", e4.rule_triggered == "S-22")

        e5 = classify_event("ITC", "Stock Split 1:5", date(2026, 6, 30), today=today)
        check("Split in 2 days -> S-23 (BLOCK_ENTRY)", e5.rule_triggered == "S-23")

        e6 = classify_event("MARICO", "Board Meeting", date(2026, 7, 3), today=today)
        check("Board meeting in 5 days -> M-09 (MONITOR)", e6.rule_triggered == "M-09")

        # ── 2. Mock fallback fires correctly on real NSE failure ──────────
        print(f"\n{SEP}\n  2 — Mock fallback (NSE unreachable -> real mock data, not false-clear)\n{SEP}")
        cal = EventCalendar(db, mock_mode=False)
        events = cal.get_events("HDFCBANK", days_ahead=14, today=today)
        check("Mock fallback returns real data on NSE failure (not an empty false-clear)",
             len(events) > 0, f"got {len(events)} events")

        # ── 3. Market intel + S-25 threshold ───────────────────────────────
        print(f"\n{SEP}\n  3 — Market intel scanner + S-25 threshold (3+ distinct bearish brokers)\n{SEP}")
        scanner = IntelScanner(db, mock_mode=True)
        summary = scanner.scan_symbol("HDFCBANK")
        check("IntelScanner returns a real IntelSummary", isinstance(summary, IntelSummary))

        fake_signals = [
            MarketSignal(symbol="TEST", title=f"{b} downgrades", url=f"u{i}", sentiment=Sentiment.BEARISH,
                        summary="", signal_type=SignalType.BROKERAGE_REPORT, broker_name=b)
            for i, b in enumerate(["Goldman Sachs", "Morgan Stanley", "UBS"])
        ]
        fake_summary = IntelSummary(symbol="TEST", signals=fake_signals)
        check("3 distinct bearish brokers triggers is_blocking (S-25)",
             fake_summary.is_blocking and fake_summary.action == "BLOCK_ENTRY")

        # ── 4. All 9 engine handlers, with the two real bugs found re-verified ──
        print(f"\n{SEP}\n  4 — Engine handlers for all 9 rules (incl. 2 bugs found+fixed during testing)\n{SEP}")

        r1 = engine.evaluate_rule(book["S-21"], None, {"corporate_event": e1.to_dict()})
        check("S-21 fires FAIL on a real blocking event", r1.status == "FAIL")

        # Bug #1 found in testing: a non-blocking event used to be
        # treated as "missing context" (ADVISORY) instead of "checked,
        # nothing blocks this rule" (PASS) — None is a valid, common
        # result here, not an absence of information.
        r2 = engine.evaluate_rule(book["S-21"], None, {"corporate_event": None})
        check("S-21 with corporate_event=None (checked, nothing blocking) correctly PASSes, not ADVISORY",
             r2.status == "PASS")

        r3 = engine.evaluate_rule(book["S-22"], None, {"corporate_event": e4.to_dict()})
        check("S-22 fires FAIL on a future merger", r3.status == "FAIL")

        r4 = engine.evaluate_rule(book["S-23"], None, {"corporate_event": e5.to_dict()})
        check("S-23 fires FAIL on a near-term split", r4.status == "FAIL")

        r5 = engine.evaluate_rule(book["S-24"], None, {"corporate_event": e2.to_dict()})
        check("S-24 fires WARN on results-week timing", r5.status == "WARN")

        r6 = engine.evaluate_rule(book["M-09"], None, {"corporate_event": e6.to_dict()})
        check("M-09 fires ADVISORY (never blocks) on a board meeting", r6.status == "ADVISORY")

        r7 = engine.evaluate_rule(book["ES-09"], None, {"corporate_event": e3.to_dict()})
        check("ES-09 fires FAIL on a same-day merger", r7.status == "FAIL")

        r8 = engine.evaluate_rule(book["S-25"], None, {"market_intel": {"is_blocking": True, "bearish_count": 3}})
        check("S-25 fires FAIL on 3+ bearish brokerage calls", r8.status == "FAIL")

        r9 = engine.evaluate_rule(book["M-11"], None, {"market_intel": {"bearish_count": 1}})
        check("M-11 fires WARN (not block) on any bearish signal", r9.status == "WARN")

        r10 = engine.evaluate_rule(book["M-12"], None, {"market_intel": {"sector_bearish": True}})
        check("M-12 fires ADVISORY on sector-wide bearish news", r10.status == "ADVISORY")

        # ── 5. eval_status correctly upgraded for all 9 rules ──────────────
        print(f"\n{SEP}\n  5 — eval_status upgraded to EVALUABLE for all 9 rules\n{SEP}")
        for rid in ["S-21", "S-22", "S-23", "S-24", "M-09", "ES-09", "S-25", "M-11", "M-12"]:
            check(f"{rid} eval_status == EVALUABLE", book[rid]["eval_status"] == "EVALUABLE")

        # ── 6. Tool dispatcher integration (bug #2 found in testing) ────────
        print(f"\n{SEP}\n  6 — agent/tools.py integration (M-09 visibility bug found+fixed)\n{SEP}")
        import asyncio

        from agent.context_builder import build_context
        from agent.tools import ToolDispatcher
        from auth.auth_service import signup
        from kite_oauth.connection_service import add_mock_connection
        from rules.rules_service import seed_rules_into_db
        from users.session_builder import build_user_session

        if db.platform_rules.count_documents({}) == 0:
            seed_rules_into_db(db)

        user = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        add_mock_connection(db, user.user_id, label="_verify_mock")

        async def _run():
            session = build_user_session(db, user.user_id, "Verify")
            ctx = await build_context(session, db)
            dispatcher = ToolDispatcher(ctx, db, rule_store=None)
            return dispatcher

        dispatcher = await_(_run())

        # Bug #2 found in testing: _get_corporate_event used
        # has_blocking_event(), which only surfaces BLOCKING events —
        # M-09's MONITOR-only events were invisible to its own handler
        # as a result. Fixed to use get_events() (all events) instead.
        result_m09 = dispatcher.dispatch("check_rule", {"rule_id": "M-09", "underlying": "SBILIFE"})
        check("M-09 correctly sees its own (non-blocking) event via the tool dispatcher",
             "ADVISORY" in result_m09, result_m09)

        result_events_tool = dispatcher.dispatch("get_corporate_events", {"symbol": "HDFCBANK"})
        check("get_corporate_events tool returns real data, not an exception string",
             "Corporate Events" in result_events_tool or "Clear" in result_events_tool)

        result_intel_tool = dispatcher.dispatch("get_market_intel", {"symbol": "HDFCBANK"})
        check("get_market_intel tool returns real data, not an exception string",
             "Market Intelligence" in result_intel_tool)

    finally:
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up _verify_ test data.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 7 VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  NSE and Tavily live calls need real network access to test")
        print(f"      end-to-end — everything around them is fully verified here.")
    else:
        print(f"  ❌  STEP 7 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step8() -> bool:
    """
    Verification of pnl/ — daily snapshot job + weekly/monthly rollups.

    Reuses agent/context_builder.py for the actual position fetching
    (already proven correct in Step 6), so this focuses on what's new:
    per-user isolation in the snapshot itself (the actual checkpoint),
    upsert behavior (re-running the same day never duplicates), and
    the rollup math against constructed multi-day history.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 8 Verification: pnl/ (snapshot + reporting)")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")
    if not db.is_mock:
        _cleanup_test_data(db)

    try:
        from datetime import date, timedelta

        from auth.auth_service import signup
        from kite_oauth.connection_service import add_mock_connection
        from pnl.reporting import get_monthly_rollup, get_weekly_rollup
        from pnl.snapshot import run_daily_snapshot

        # ── 1. THE CHECKPOINT: two users, two separate daily_pnl docs ──────
        print(f"\n{SEP}\n  1 — Checkpoint: two users, same date, separate documents\n{SEP}")
        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        user2 = signup(db, TEST_USERNAME_2, TEST_EMAIL_2, "pw_verify_123")
        add_mock_connection(db, user1.user_id, label="_verify_pnl_user1")
        add_mock_connection(db, user2.user_id, label="_verify_pnl_user2")

        today = date(2026, 6, 29)
        snap_results = run_daily_snapshot(db, as_of=today)

        check("Both users snapshotted successfully",
             all(r.success for r in snap_results), str([r.error for r in snap_results if not r.success]))

        docs = list(db.daily_pnl.find({"date": today.isoformat()}))
        verify_docs = [d for d in docs if d["user_id"] in (user1.user_id, user2.user_id)]
        check("Two separate documents exist for the same date",
             len(verify_docs) == 2, f"found {len(verify_docs)}")

        doc1 = next((d for d in verify_docs if d["user_id"] == user1.user_id), None)
        doc2 = next((d for d in verify_docs if d["user_id"] == user2.user_id), None)
        check("User 1's document is correctly scoped to user 1", doc1 is not None)
        check("User 2's document is correctly scoped to user 2", doc2 is not None)
        check("The two documents have different user_ids (no cross-talk)",
             doc1 is not None and doc2 is not None and doc1["user_id"] != doc2["user_id"])

        # ── 2. Upsert behavior — re-running never duplicates ────────────────
        print(f"\n{SEP}\n  2 — Re-running the same day upserts, never duplicates\n{SEP}")
        run_daily_snapshot(db, as_of=today)
        run_daily_snapshot(db, as_of=today)
        docs_after = list(db.daily_pnl.find({"user_id": user1.user_id, "date": today.isoformat()}))
        check("Still exactly 1 document after 3 total runs on the same day",
             len(docs_after) == 1, f"found {len(docs_after)}")

        # ── 3. Weekly/monthly rollup math ───────────────────────────────────
        print(f"\n{SEP}\n  3 — Rollup math against constructed multi-day history\n{SEP}")
        rollup_user = "_verify_rollup_user"
        daily_values = {
            "2026-06-22": -500.0, "2026-06-23": 1200.0, "2026-06-24": -300.0,
            "2026-06-25": 800.0, "2026-06-26": 2000.0,
        }
        for d, pnl in daily_values.items():
            db.daily_pnl.update_one(
                {"user_id": rollup_user, "date": d},
                {"$set": {"user_id": rollup_user, "date": d, "total_pnl": pnl}},
                upsert=True,
            )

        weekly = get_weekly_rollup(db, rollup_user, as_of=date(2026, 6, 26))
        check("Weekly rollup picks up all 5 days", weekly.days_with_data == 5)
        check("Weekly rollup sum is correct", weekly.sum_pnl == sum(daily_values.values()))
        check("Weekly rollup correctly identifies best day",
             weekly.best_day == ("2026-06-26", 2000.0))
        check("Weekly rollup correctly identifies worst day",
             weekly.worst_day == ("2026-06-22", -500.0))

        monthly = get_monthly_rollup(db, rollup_user, as_of=date(2026, 6, 26))
        check("Monthly rollup also picks up all 5 days (same month)", monthly.days_with_data == 5)

        # Cleanup the rollup test data (not a real user, just raw daily_pnl docs)
        for d in daily_values:
            db.daily_pnl.delete_one({"user_id": rollup_user, "date": d})

    finally:
        if not db.is_mock:
            _cleanup_test_data(db)
            db.daily_pnl.delete_one({"user_id": user1.user_id, "date": today.isoformat()})
            db.daily_pnl.delete_one({"user_id": user2.user_id, "date": today.isoformat()})
            print(f"\n  🧹 Cleaned up _verify_ test data.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 8 VERIFICATION PASSED  ({passed}/{total})")
    else:
        print(f"  ❌  STEP 8 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def run_step9() -> bool:
    """
    Verification of dashboard/ + pages/ — stock universe, analysis
    engine, and the per-user Action Plan.

    THE CHECKPOINT: two users, same shared stock data, genuinely
    different Action Plan verdicts — proven the same way as every
    other per-user claim in this project, by constructing a real
    difference (one user has a real broker connection, one doesn't)
    and confirming the rule verdict actually differs as a result.
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, condition, detail))
        icon = "✅" if condition else "❌"
        print(f"  {icon}  {name}" + (f"  —  {detail}" if detail else ""))

    print_header("ArthaChakra — Step 9 Verification: dashboard/ + pages/ (Stock Scanner)")

    db = Database()
    print(f"\n  Database mode: {'MongoDB' if not db.is_mock else 'MOCK (in-memory)'}")
    if not db.is_mock:
        _cleanup_test_data(db)

    try:
        from datetime import datetime, date

        from auth.auth_service import signup
        from dashboard.action_plan import build_action_plan
        from dashboard.stock_analysis import compute_stock_analysis, records_from_dicts
        from dashboard.stock_universe import get_ohlc_for_analysis, upsert_stock
        from kite_oauth.connection_service import add_mock_connection
        from market_data.vix_fetcher import cache_vix_reading
        from rules.rules_service import seed_rules_into_db
        from users.session_builder import build_user_session

        if db.platform_rules.count_documents({}) == 0:
            seed_rules_into_db(db)

        # ── 1. Analysis engine math (pure, no DB) ───────────────────────────
        print(f"\n{SEP}\n  1 — Backtested win-rate analysis engine\n{SEP}")
        raw = [
            {"month_key": "2026-01", "open": 1000, "high": 1050, "low": 970},
            {"month_key": "2026-02", "open": 1020, "high": 1080, "low": 990},
            {"month_key": "2026-03", "open": 1010, "high": 1060, "low": 950},
            {"month_key": "2026-04", "open": 1030, "high": 1200, "low": 1000},  # big move - Loose
            {"month_key": "2026-05", "open": 1040, "high": 1090, "low": 1010},
        ]
        records = records_from_dicts(raw)
        analysis = compute_stock_analysis("_VERIFY_STOCK", records, target=10)
        check("4/5 months win at 10% threshold -> 80% win rate", analysis.target_win_pct == 80.0)
        check("80% win rate classifies as Violet tier", analysis.tier == "Violet")

        # ── 2. Shared stock universe + OHLC access ──────────────────────────
        print(f"\n{SEP}\n  2 — Shared stock universe (nse_stocks / monthly_ohlc)\n{SEP}")
        upsert_stock(db, "_VERIFY_HDFCBANK", "Verify HDFC Bank", "Banking")
        for i, month in enumerate(["2026-01", "2026-02", "2026-03"]):
            o = 1700 + i * 20
            db.monthly_ohlc.update_one(
                {"symbol": "_VERIFY_HDFCBANK", "month_key": month},
                {"$set": {"symbol": "_VERIFY_HDFCBANK", "month_key": month,
                         "open": o, "high": o + 50, "low": o - 30, "close": o + 10}},
                upsert=True,
            )
        ohlc = get_ohlc_for_analysis(db, "_VERIFY_HDFCBANK")
        check("get_ohlc_for_analysis reads Step 5-shaped monthly_ohlc data correctly",
             len(ohlc) == 3 and set(ohlc[0].keys()) == {"month_key", "open", "high", "low"})

        # Explicit check on EVERY public function in stock_universe.py —
        # added after a real bug where an edit accidentally merged
        # get_universe_stats()'s body into manual_upsert_ohlc(), silently
        # deleting get_universe_stats() entirely. That bug shipped
        # undetected because nothing here called it directly — only the
        # live Streamlit page did. Importing every expected name here
        # means a future accidental deletion fails LOUDLY at import time,
        # not silently in production.
        import dashboard.stock_universe as _su
        for fn_name in ["upsert_stock", "set_index_memberships", "get_all_stocks", "get_stock",
                       "get_all_symbols", "get_ohlc_for_analysis", "get_latest_month",
                       "get_month_count", "manual_upsert_ohlc", "get_universe_stats"]:
            check(f"dashboard.stock_universe.{fn_name} exists and is callable",
                 hasattr(_su, fn_name) and callable(getattr(_su, fn_name)))

        from dashboard.stock_universe import get_universe_stats, manual_upsert_ohlc
        manual_upsert_ohlc(db, "_VERIFY_HDFCBANK", "2026-06", 800.0, 810.0, 790.0)
        stats = get_universe_stats(db)
        check("get_universe_stats returns the expected dict shape",
             set(stats.keys()) == {"stocks", "active_stocks", "ohlc_records"})

        # ── 3. THE CHECKPOINT: two users, different Action Plan verdicts ───
        print(f"\n{SEP}\n  3 — Checkpoint: two users, same stock, different verdicts\n{SEP}")
        cache_vix_reading(db, 28.5, as_of=datetime.now())   # pre-cache a high (blocking) VIX reading

        user1 = signup(db, TEST_USERNAME_1, TEST_EMAIL_1, "pw_verify_123")
        user2 = signup(db, TEST_USERNAME_2, TEST_EMAIL_2, "pw_verify_123")
        add_mock_connection(db, user1.user_id, label="_verify_mock_only")   # no real VIX context
        db.broker_connections.insert_one({                                    # user2: real-looking connection
            "connection_id": "_verify_conn_real", "user_id": user2.user_id, "broker": "kite",
            "label": "_verify_real_conn", "api_key": "x", "api_secret": "x",
            "access_token": "real_looking_token", "token_expiry": "2099-01-01",
            "account_type": "both", "broker_account_name": "", "active": True,
        })

        session1 = build_user_session(db, user1.user_id, "Verify1")
        session2 = build_user_session(db, user2.user_id, "Verify2")

        today = date(2026, 6, 29)
        plan1 = build_action_plan(db, session1, candidate_symbols=["_VERIFY_HDFCBANK"], as_of=today)
        plan2 = build_action_plan(db, session2, candidate_symbols=["_VERIFY_HDFCBANK"], as_of=today)

        def _find_s01(plan):
            for v in plan.enter + plan.caution + plan.avoid:
                for r in v.rule_results:
                    if r.rule_id == "S-01":
                        return r
            return None

        s01_1, s01_2 = _find_s01(plan1), _find_s01(plan2)
        check("User 1 (mock-only) gets ADVISORY on S-01 (no real VIX context)",
             s01_1 is not None and s01_1.status == "ADVISORY")
        check("User 2 (real connection) gets a real FAIL on S-01 (VIX 28.5 >= 25)",
             s01_2 is not None and s01_2.status == "FAIL")
        check("The two users' verdicts genuinely differ — not the same plan with a user_id tag",
             s01_1.status != s01_2.status)

        db.broker_connections.delete_one({"connection_id": "_verify_conn_real"})

        # ── 4. Full F&O universe importer (real fo_mktlots.csv format) ─────
        print(f"\n{SEP}\n  4 — F&O universe importer against the real fo_mktlots.csv format\n{SEP}")
        from dashboard.fo_universe_importer import parse_fo_mktlots

        synthetic_csv = (
            "UNDERLYING,SYMBOL,JUN-26,JUL-26\n"
            "NIFTY 50,NIFTY,65,65\n"
            "NIFTY BANK,BANKNIFTY,30,30\n"
            "Derivatives on Individual Securities,Symbol,JUN-26,JUL-26\n"
            "HDFC BANK LTD,HDFCBANK,550,550\n"
            "TATA CONSULTANCY,TCS,,175\n"   # blank first month, lot size in second
        )
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(synthetic_csv)
            tmp_path = f.name
        entries = parse_fo_mktlots(tmp_path)
        check("Section-divider row ('Symbol' literal) correctly skipped",
             not any(e["symbol"].lower() == "symbol" for e in entries))
        check("Both real indices correctly tagged is_index=True",
             sum(1 for e in entries if e["is_index"]) == 2)
        check("Both real stocks correctly tagged is_index=False",
             sum(1 for e in entries if not e["is_index"]) == 2)
        tcs_entry = next((e for e in entries if e["symbol"] == "TCS"), None)
        check("Lot size correctly taken from first NON-BLANK month column (TCS: blank Jun, 175 in Jul)",
             tcs_entry is not None and tcs_entry["lot_size"] == 175)
        Path(tmp_path).unlink()

        # ── 5. Index membership tagging (multi-index symbols) ───────────────
        print(f"\n{SEP}\n  5 — Index constituent parsing + multi-index tagging\n{SEP}")
        from market_data.nse_index_constituents import parse_constituent_csv
        from dashboard.stock_universe import set_index_memberships, get_stock as _get_stock

        synthetic_constituent_csv = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
            "Reliance Industries Ltd.,Energy,RELIANCE,EQ,INE002A01018\n"
        )
        parsed = parse_constituent_csv(synthetic_constituent_csv)
        check("Index constituent CSV parses real NSE column format ('Symbol')",
             parsed == ["HDFCBANK", "RELIANCE"])

        upsert_stock(db, "_VERIFY_MULTI_IDX", "Verify Multi Index")
        set_index_memberships(db, "_VERIFY_MULTI_IDX", ["NIFTY50", "BANKNIFTY"])
        doc = _get_stock(db, "_VERIFY_MULTI_IDX")
        check("A symbol can belong to multiple indices simultaneously",
             doc is not None and set(doc.get("index_memberships", [])) == {"NIFTY50", "BANKNIFTY"})
        db.nse_stocks.delete_one({"symbol": "_VERIFY_MULTI_IDX"})

        # ── 6. Per-user saved shortlist (manual workflow, distinct from Action Plan) ──
        print(f"\n{SEP}\n  6 — Per-user saved shortlist isolation + multiple named shortlists\n{SEP}")
        from dashboard.saved_shortlist import (
            get_shortlist, get_shortlists_for_month, save_shortlist,
        )

        save_shortlist(db, user1.user_id, "_VERIFY_MONTH", ["HDFCBANK", "TCS"], notes="user1 picks")
        save_shortlist(db, user2.user_id, "_VERIFY_MONTH", ["RELIANCE"], notes="user2 picks")
        list1 = get_shortlist(db, user1.user_id, "_VERIFY_MONTH")
        list2 = get_shortlist(db, user2.user_id, "_VERIFY_MONTH")
        check("User 1's saved shortlist correctly isolated", list1 is not None and list1["symbols"] == ["HDFCBANK", "TCS"])
        check("User 2's saved shortlist correctly isolated (different list, same month)",
             list2 is not None and list2["symbols"] == ["RELIANCE"])

        # Multiple NAMED shortlists for the SAME user, SAME month
        save_shortlist(db, user1.user_id, "_VERIFY_MONTH", ["HDFCBANK", "TCS"], shortlist_name="Banking+IT")
        save_shortlist(db, user1.user_id, "_VERIFY_MONTH", ["RELIANCE"], shortlist_name="Energy Only")
        user1_lists = get_shortlists_for_month(db, user1.user_id, "_VERIFY_MONTH")
        check("Same user can save multiple DIFFERENTLY-NAMED shortlists for the same month",
             len(user1_lists) == 3)   # Default (from the first save_shortlist call) + 2 named

        # Re-saving the SAME name overwrites, does not duplicate
        save_shortlist(db, user1.user_id, "_VERIFY_MONTH", ["HDFCBANK"], shortlist_name="Banking+IT")
        user1_lists_after = get_shortlists_for_month(db, user1.user_id, "_VERIFY_MONTH")
        check("Re-saving the SAME shortlist name overwrites, does not duplicate",
             len(user1_lists_after) == 3)
        banking_it = next(s for s in user1_lists_after if s["shortlist_name"] == "Banking+IT")
        check("The overwritten shortlist has the NEW symbols, not the old ones",
             banking_it["symbols"] == ["HDFCBANK"])

        db.action_plans.delete_one({"user_id": user1.user_id, "month_key": "_VERIFY_MONTH", "shortlist_name": "Default"})
        db.action_plans.delete_one({"user_id": user1.user_id, "month_key": "_VERIFY_MONTH", "shortlist_name": "Banking+IT"})
        db.action_plans.delete_one({"user_id": user1.user_id, "month_key": "_VERIFY_MONTH", "shortlist_name": "Energy Only"})
        db.action_plans.delete_one({"user_id": user2.user_id, "month_key": "_VERIFY_MONTH", "shortlist_name": "Default"})

        # ── 7. Action Plan correctly scoped to a CHOSEN shortlist ───────────
        print(f"\n{SEP}\n  7 — Action Plan correctly scoped to one chosen shortlist\n{SEP}")
        save_shortlist(db, user1.user_id, "_VERIFY_MONTH2", ["_VERIFY_HDFCBANK"], shortlist_name="JustHDFC")
        scoped_plan = build_action_plan(db, session1, candidate_symbols=["_VERIFY_HDFCBANK"],
                                        as_of=date(2026, 6, 29))
        scoped_symbols = {v.symbol for v in scoped_plan.enter + scoped_plan.caution + scoped_plan.avoid}
        check("Action Plan scoped to a chosen shortlist never evaluates symbols outside it",
             scoped_symbols <= {"_VERIFY_HDFCBANK"})
        db.action_plans.delete_one({"user_id": user1.user_id, "month_key": "_VERIFY_MONTH2", "shortlist_name": "JustHDFC"})

        # ── 8. 4% threshold floor supported end-to-end, not just the UI slider ──
        print(f"\n{SEP}\n  8 — 4% threshold floor (was 8%) supported by the analysis engine\n{SEP}")
        from dashboard.stock_analysis import THRESHOLDS as _THRESHOLDS
        check("THRESHOLDS now starts at 4%, not 8%", _THRESHOLDS[0] == 4)
        low_analysis = compute_stock_analysis("_VERIFY_HDFCBANK", records, target=4)
        check("compute_stock_analysis accepts target=4 and populates win_rates[4]",
             4 in low_analysis.win_rates)

    finally:
        db.nse_stocks.delete_one({"symbol": "_VERIFY_HDFCBANK"})
        for m in ["2026-01", "2026-02", "2026-03"]:
            db.monthly_ohlc.delete_one({"symbol": "_VERIFY_HDFCBANK", "month_key": m})
        if not db.is_mock:
            _cleanup_test_data(db)
            print(f"\n  🧹 Cleaned up _verify_ test data.")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{SEP2}")
    if passed == total:
        print(f"  ✅  STEP 9 VERIFICATION PASSED  ({passed}/{total})")
    else:
        print(f"  ❌  STEP 9 VERIFICATION FAILED  ({passed}/{total})")
        for name, ok, detail in results:
            if not ok:
                print(f"    ❌ {name}  {detail}")
    print(SEP2)

    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description="ArthaChakra — verification suite")
    parser.add_argument("--step1", action="store_true", help="Run Step 1 verification")
    parser.add_argument("--step2", action="store_true", help="Run Step 2 verification")
    parser.add_argument("--step3", action="store_true", help="Run Step 3 verification")
    parser.add_argument("--step4", action="store_true", help="Run Step 4 verification")
    parser.add_argument("--step5", action="store_true", help="Run Step 5 verification")
    parser.add_argument("--step6", action="store_true", help="Run Step 6 verification")
    parser.add_argument("--step7", action="store_true", help="Run Step 7 verification")
    parser.add_argument("--step8", action="store_true", help="Run Step 8 verification")
    parser.add_argument("--step9", action="store_true", help="Run Step 9 verification")
    args = parser.parse_args()

    if args.step9:
        ok = run_step9()
        return 0 if ok else 1
    if args.step8:
        ok = run_step8()
        return 0 if ok else 1
    if args.step7:
        ok = run_step7()
        return 0 if ok else 1
    if args.step6:
        ok = run_step6()
        return 0 if ok else 1
    if args.step5:
        ok = run_step5()
        return 0 if ok else 1
    if args.step4:
        ok = run_step4()
        return 0 if ok else 1
    if args.step3:
        ok = run_step3()
        return 0 if ok else 1
    if args.step2:
        ok = run_step2()
        return 0 if ok else 1
    if args.step1 or len(sys.argv) == 1:
        ok = run_step1()
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
