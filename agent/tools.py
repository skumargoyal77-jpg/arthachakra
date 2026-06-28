"""
agent/tools.py
───────────────────
Tool definitions for Claude's native tool-calling. Every tool is a
thin wrapper around code that already exists from Steps 1-5 — the
agent's job is to decide WHEN to call these and HOW to explain the
result, not to reimplement any of the underlying logic.

WHY NO LANGCHAIN:
  POC-02 used LangGraph's create_react_agent. This uses Anthropic's
  native tool-use API directly instead — one fewer dependency, and
  more direct control over per-user tool construction (LangGraph's
  tool objects are typically built once at import time; here, tools
  are built fresh per AgentContext, matching the per-user requirement).

PROJECT PATH:  agent/tools.py
"""

from __future__ import annotations

from agent.context_builder import AgentContext
from rag.rule_store import RuleStore
from rules.engine import RuleEngine
from rules.rules_service import get_effective_rules

# ── Tool schemas (Anthropic tool-use format) ──────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_vix",
        "description": (
            "Get the current India VIX level and its 5-day trend. Use this "
            "before any entry decision — VIX gates several rules (S-01, S-02, S-15)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": (
            "Get the user's current open strangle positions across all their "
            "connected broker accounts, with delta, P&L, and leg counts."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_rules",
        "description": (
            "Semantic search over the full rule book to find rules relevant "
            "to a natural-language question. Returns rule_id, name, "
            "description, and whether each rule can actually be checked "
            "right now (eval_status)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language question"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_rule",
        "description": (
            "Run one specific rule (by rule_id) against the user's current "
            "context (VIX, positions). Returns PASS/FAIL/WARN/ADVISORY/"
            "NOT_YET_EVALUABLE with an explanation. Use this AFTER "
            "search_rules identifies which rule_ids are relevant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "e.g. 'S-01', 'A-10'"},
                "underlying": {
                    "type": "string",
                    "description": "Symbol to check against, if the rule needs an existing position (optional)",
                },
            },
            "required": ["rule_id"],
        },
    },
]


# ── Dispatch ───────────────────────────────────────────────────────────────

class ToolDispatcher:
    """
    Built fresh per AgentContext — holds no state shared between users.
    rule_store is the one exception: ChromaDB is read-only shared
    reference data (the rule book text), not per-user state, so reusing
    one instance across users is safe and avoids reloading the
    embedding model on every call.
    """

    def __init__(self, ctx: AgentContext, db, rule_store: RuleStore | None = None) -> None:
        self.ctx = ctx
        self.db = db
        self.rule_store = rule_store
        self.engine = RuleEngine()
        self._effective_rules_cache: list[dict] | None = None

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        handler = {
            "get_vix": self._get_vix,
            "get_positions": self._get_positions,
            "search_rules": self._search_rules,
            "check_rule": self._check_rule,
        }.get(tool_name)
        if handler is None:
            return f"Unknown tool: {tool_name}"
        try:
            return handler(tool_input)
        except Exception as e:
            return f"Tool '{tool_name}' failed: {e}"

    # ── Individual tools ───────────────────────────────────────────────

    def _get_vix(self, _: dict) -> str:
        if self.ctx.vix is None:
            return "VIX unavailable — no real broker connection found, or fetch failed."
        trend = ""
        if len(self.ctx.vix_5day_readings) >= 2:
            change = self.ctx.vix_5day_readings[-1] - self.ctx.vix_5day_readings[0]
            trend = f" (5-day change: {change:+.1f})"
        return f"India VIX: {self.ctx.vix:.2f}{trend}"

    def _get_positions(self, _: dict) -> str:
        if not self.ctx.strangles:
            return "No open strangle positions found."
        lines = []
        for s in self.ctx.strangles:
            lines.append(
                f"{s.underlying} {s.expiry} ({s.connection_label}): "
                f"spot={s.spot}, P&L={s.total_pnl:+.0f}, "
                f"delta_status={s.delta_status}, "
                f"{len(s.ce_legs)} CE + {len(s.pe_legs)} PE legs"
            )
        return "\n".join(lines)

    def _search_rules(self, tool_input: dict) -> str:
        if self.rule_store is None:
            return "Rule search unavailable (RuleStore not configured)."
        query = tool_input.get("query", "")
        return self.rule_store.query_for_prompt(query, n_results=4)

    def _check_rule(self, tool_input: dict) -> str:
        rule_id = tool_input.get("rule_id", "")
        underlying = tool_input.get("underlying")

        if self._effective_rules_cache is None:
            self._effective_rules_cache = get_effective_rules(self.db, self.ctx.session.user_id)
        rule = next((r for r in self._effective_rules_cache if r["rule_id"] == rule_id), None)
        if rule is None:
            return f"Rule '{rule_id}' not found in this user's effective rule set."

        strangle = None
        if underlying:
            strangle = next(
                (s for s in self.ctx.strangles if s.underlying.upper() == underlying.upper()),
                None,
            )

        context = {
            "as_of": self.ctx.as_of,
            "as_of_datetime": self.ctx.as_of_datetime,
            "vix": self.ctx.vix,
            "vix_5day_readings": self.ctx.vix_5day_readings,
        }
        result = self.engine.evaluate_rule(rule, strangle, context)
        return f"[{result.rule_id}] {result.status}: {result.message}"
