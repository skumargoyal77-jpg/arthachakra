"""
agent/integration_agent.py
───────────────────────────────
The main agent entry point — ported from POC-02's TradingAgent, but
rebuilt around two requirements that didn't exist when POC-02 was
written:

  1. EVERYTHING IS BUILT FRESH PER UserSession, NOT GLOBALLY.
     POC-02 built one `tools` list once and reused it for every
     query. IntegrationAgent is instantiated per-call with a
     UserSession; its tools, rule context, and broker clients all
     derive from that session and nothing else. Two IntegrationAgent
     instances for two different users share no state — verified
     directly in verify_setup.py's run_step6().

  2. THE LLM NO LONGER DOES THE RULE MATH ITSELF.
     POC-02's system prompt asked Claude to compare VIX against 25
     itself. Now rules/engine.py does that deterministically — the
     agent's job is to call tools, then explain the verdict in plain
     language, not to re-derive the threshold logic from a prompt.

NO LANGCHAIN — see tools.py's docstring for why.

PROJECT PATH:  agent/integration_agent.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from agent.context_builder import AgentContext, build_context
from agent.router import route
from agent.tools import TOOL_SCHEMAS, ToolDispatcher
from config import settings
from core.database import Database
from core.logging_config import setup_logging
from rag.rule_store import RuleStore
from users.models import UserSession

logger = setup_logging(__name__)

SYSTEM_PROMPT = """You are an NSE options trading assistant for a monthly short strangle strategy.

Use the available tools to gather live data and check specific rules before
answering. Never assume VIX, position, or rule-check values — always call a
tool to get them.

When asked whether to enter a position or for a recommendation:
  1. Call search_rules to find which specific rules are relevant.
  2. Call check_rule for each relevant rule_id to get its actual verdict.
  3. Call get_vix and get_positions if the rules need that context.
  4. Synthesize a clear answer citing the specific rule_ids you checked.

If a rule's check_rule result is NOT_YET_EVALUABLE, say so explicitly rather
than guessing — that data source genuinely doesn't exist yet (see the
rule's message for why).

Keep answers concise. Traders need clear decisions, not essays.
"""


@dataclass
class AgentResponse:
    """Structured response from one agent query."""
    question:     str
    answer:       str
    tool_calls:   list[dict] = field(default_factory=list)
    latency_secs: float = 0.0
    model:        str = ""
    error:        Optional[str] = None

    @property
    def num_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def tools_called(self) -> list[str]:
        return [tc["tool"] for tc in self.tool_calls]


class IntegrationAgent:
    """
    Built fresh per UserSession — never shared between users. Holds a
    reference to one user's session, builds its own AgentContext and
    ToolDispatcher; nothing here is module-level or cached across
    instances except the read-only RuleStore (shared rule-book text,
    not per-user state).
    """

    _shared_rule_store: Optional[RuleStore] = None  # lazy, shared (read-only data)

    def __init__(self, session: UserSession, db: Database, verbose: bool = False) -> None:
        self.session = session
        self.db = db
        self.verbose = verbose

    @classmethod
    def _get_rule_store(cls) -> Optional[RuleStore]:
        """
        Lazily build the shared RuleStore once. Safe to share — it's
        read-only rule-book TEXT (Step 4), never user-specific data.
        Returns None if it can't be built (e.g. no embedding model
        available) rather than crashing every agent call.
        """
        if cls._shared_rule_store is None:
            try:
                cls._shared_rule_store = RuleStore()
            except Exception as e:
                logger.warning("RuleStore unavailable, search_rules tool will be degraded: %s", e)
        return cls._shared_rule_store

    async def ask(self, question: str) -> AgentResponse:
        """
        Ask one trading question. Builds this user's context fresh,
        runs the tool-use loop, returns a structured response.
        """
        logger.info("Agent query for user=%s: %s", self.session.user_id, question)
        start = time.perf_counter()

        try:
            import anthropic
        except ImportError:
            return AgentResponse(
                question=question, answer="", latency_secs=0.0,
                error="anthropic SDK not installed — run: pip install anthropic",
            )

        if not settings.anthropic_api_key:
            return AgentResponse(
                question=question, answer="", latency_secs=0.0,
                error="ANTHROPIC_API_KEY not configured in .env",
            )

        model_choice = route(question)
        model = settings.haiku_model if model_choice == "haiku" else settings.sonnet_model

        try:
            ctx = await build_context(self.session, self.db)
            dispatcher = ToolDispatcher(ctx, self.db, rule_store=self._get_rule_store())

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            messages = [{"role": "user", "content": question}]
            tool_calls_log: list[dict] = []

            for _ in range(6):  # max_iterations, same cap as POC-02
                response = client.messages.create(
                    model=model, max_tokens=1024, system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS, messages=messages,
                )

                if response.stop_reason != "tool_use":
                    answer = "".join(
                        block.text for block in response.content if block.type == "text"
                    )
                    elapsed = time.perf_counter() - start
                    if self.verbose and tool_calls_log:
                        for tc in tool_calls_log:
                            output_str = str(tc['output'])
                            preview = output_str if len(output_str) <= 300 else output_str[:300] + " ...[truncated]"
                            print(f"  [tool] {tc['tool']}({tc['input']}) -> {preview}")
                    return AgentResponse(
                        question=question, answer=answer, tool_calls=tool_calls_log,
                        latency_secs=elapsed, model=model,
                    )

                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    output = dispatcher.dispatch(block.name, block.input)
                    tool_calls_log.append({"tool": block.name, "input": block.input, "output": output})
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": output,
                    })
                messages.append({"role": "user", "content": tool_results})

            elapsed = time.perf_counter() - start
            return AgentResponse(
                question=question, answer="", tool_calls=tool_calls_log,
                latency_secs=elapsed, model=model,
                error="Max tool-use iterations reached without a final answer.",
            )

        except Exception as e:
            elapsed = time.perf_counter() - start
            logger.exception("Agent error for user=%s query='%s'", self.session.user_id, question)
            return AgentResponse(
                question=question, answer="", latency_secs=elapsed, model=model, error=str(e),
            )