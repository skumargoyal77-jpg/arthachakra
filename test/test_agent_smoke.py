"""
Real smoke test for the IntegrationAgent - needs a real ANTHROPIC_API_KEY
in your .env, and at least one user with a real (or mock) broker connection.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

import asyncio
from core.database import Database
from users.user_repository import list_users, get_user_by_username
from users.session_builder import build_user_session
from agent.integration_agent import IntegrationAgent

TARGET_USERNAME = "sandykgoyal"  # change this if you want to test a different account

db = Database()
print(f"DB mode: {'mock' if db.is_mock else 'real MongoDB'}\n")

user = get_user_by_username(db, TARGET_USERNAME)
if user is None:
    print(f"No user found with username '{TARGET_USERNAME}'.")
    print("Available active users:")
    for u in list_users(db, active_only=True):
        print(f"  - {u.username} ({u.display_name})")
    raise SystemExit(1)

print(f"Using user: {user.display_name} ({user.username})\n")

session = build_user_session(db, user.user_id, user.display_name)
agent = IntegrationAgent(session, db, verbose=True)


async def main():
    print("Asking: 'What is the VIX right now?'\n")
    result = await agent.ask("What is the VIX right now?")

    print(f"\nAnswer: {result.answer}")
    print(f"Model used: {result.model}")
    print(f"Tool calls: {result.tools_called}")
    print(f"Latency: {result.latency_secs:.2f}s")
    if result.error:
        print(f"\n❌ Error: {result.error}")


asyncio.run(main())