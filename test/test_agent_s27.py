"""
Asks the LIVE agent about TCS for July's series, WITHOUT naming S-27
or "results before expiry" anywhere in the question - the real test
of whether the agent reasons its way to the right rule on its own,
not just whether the underlying code works (already proven directly).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from core.database import Database
from users.user_repository import get_user_by_username
from users.session_builder import build_user_session
from agent.integration_agent import IntegrationAgent

TARGET_USERNAME = "sandykgoyal"

# Deliberately does NOT mention "S-27", "results before expiry", or any
# rule_id - just a natural question a trader would actually ask.
QUESTION = (
    "I'm thinking about entering a TCS strangle, planning to hold it "
    "through the July expiry series. Is there anything that should "
    "stop me from doing that?"
)

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

user = get_user_by_username(db, TARGET_USERNAME)
if user is None:
    print(f"No user found with username '{TARGET_USERNAME}'.")
    raise SystemExit(1)

session = build_user_session(db, user.user_id, user.display_name)
agent = IntegrationAgent(session, db, verbose=True)


async def main():
    print(f"Question: {QUESTION}\n")
    print("-" * 70)
    result = await agent.ask(QUESTION)
    print("-" * 70)

    print(f"\nModel used: {result.model}")
    print(f"Tools called: {result.tools_called}")
    print(f"\nAnswer:\n{result.answer}")

    if result.error:
        print(f"\n❌ Error: {result.error}")

    print(f"\n{'='*70}")
    print("CHECK: did the agent call search_rules, find S-27 itself, then")
    print("call check_rule with rule_id=S-27, WITHOUT you ever naming it?")
    print("If 'S-27' appears in the tool trace above, that's confirmed.")
    print(f"{'='*70}")


asyncio.run(main())