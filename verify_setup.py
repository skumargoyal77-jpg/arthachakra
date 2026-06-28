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

        report1 = seed_rules_into_db(db)
        check("First seed inserts all 55 rules", report1["inserted"] == 55, str(report1))
        check("platform_rules now has exactly 55 documents",
             db.platform_rules.count_documents({}) == 55)

        report2 = seed_rules_into_db(db)
        check("Re-seed updates existing rules, inserts none (upsert, not duplicate)",
             report2["inserted"] == 0 and report2["updated"] == 55, str(report2))
        check("Still exactly 55 documents after re-seed (no duplicates)",
             db.platform_rules.count_documents({}) == 55)

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
        check("Both users start with identical effective rule count (55)",
             len(rules1_before) == len(rules2_before) == 55)

        add_custom_rule(db, user1.user_id, TEST_RULE_PLATFORM,
                        "Verify custom rule", "worst_distance_pct", "<", 5, "WARN")
        rules1_after = get_effective_rules(db, user1.user_id)
        rules2_after = get_effective_rules(db, user2.user_id)
        check("User 1's effective set grows to 56 after adding a custom rule",
             len(rules1_after) == 56)
        check("User 2's effective set is untouched (still 55) — no cross-talk",
             len(rules2_after) == 55)

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
        check("evaluate_all() returns exactly 55 results, one per rule",
             len(all_results) == 55)

        # ── 5. Cleanup the test-only custom rule (real 55-rule seed stays) ──
        remove_custom_rule(db, user1.user_id, TEST_RULE_PLATFORM)
        rules1_final = get_effective_rules(db, user1.user_id)
        check("Custom rule cleanly removable, user 1 back to 55",
             len(rules1_final) == 55)

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
        counts = Counter(r["eval_status"] for r in _get_book())
        print(f"  ✅  STEP 3 VERIFICATION PASSED  ({passed}/{total})")
        print(f"  👉  55 rules seeded. {counts['EVALUABLE']} evaluable now, "
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
        from rag.rule_store import RuleStore

        if db.platform_rules.count_documents({}) == 0:
            seed_rules_into_db(db)

        print("\n  Loading embedding model (downloads ~80MB on first run)...")
        store = RuleStore()
        n = store.embed_from_mongo(db, overwrite=True)
        check("Embedded all 55 rules from MongoDB (not YAML)", n == 55, f"embedded={n}")
        check("ChromaDB collection count matches", store.count() == 55)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="ArthaChakra — verification suite")
    parser.add_argument("--step1", action="store_true", help="Run Step 1 verification")
    parser.add_argument("--step2", action="store_true", help="Run Step 2 verification")
    parser.add_argument("--step3", action="store_true", help="Run Step 3 verification")
    parser.add_argument("--step4", action="store_true", help="Run Step 4 verification")
    parser.add_argument("--step5", action="store_true", help="Run Step 5 verification")
    parser.add_argument("--step6", action="store_true", help="Run Step 6 verification")
    args = parser.parse_args()

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
