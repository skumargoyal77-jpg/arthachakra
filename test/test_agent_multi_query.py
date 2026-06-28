"""
Runs several test queries through the agent in one go - mix of
Haiku-routed (simple lookups) and Sonnet-routed (synthesis) questions.
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

QUERIES = [
    "What is the VIX right now?",                          # Haiku - simple
    "What are my current open positions?",                  # Haiku - simple
    "Should I enter HDFCBANK?",                              # Sonnet - synthesis
    "Is my position in NTPC delta neutral?",                 # Sonnet - synthesis
    "What does rule A-10 say about leg ratios?",              # Sonnet - rule lookup
    "Can I add another CE leg to my SBILIFE position right now?",  # Sonnet - rule check
]

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

user = get_user_by_username(db, TARGET_USERNAME)
if user is None:
    print(f"No user found with username '{TARGET_USERNAME}'.")
    raise SystemExit(1)

session = build_user_session(db, user.user_id, user.display_name)


async def main():
    for i, q in enumerate(QUERIES, 1):
        print(f"\n{'='*70}")
        print(f"[{i}/{len(QUERIES)}] {q}")
        print('='*70)

        agent = IntegrationAgent(session, db, verbose=True)
        result = await agent.ask(q)

        print(f"\nModel: {result.model}  |  Tools: {result.tools_called}  |  {result.latency_secs:.1f}s")
        print(f"\nAnswer:\n{result.answer}")
        if result.error:
            print(f"\n❌ Error: {result.error}")


asyncio.run(main())