"""
rag/rule_store.py
─────────────────────
ChromaDB-backed Rule Book store — Step 4.

KEY DIFFERENCE FROM THE EARLIER POC-03 SPIKE:
  POC-03 embedded rules straight from a static rules.yaml file. This
  version embeds from MongoDB's platform_rules collection instead —
  the actual database Step 3 seeds and maintains. Editing a rule's
  wording now means: edit rules/seed_rules.py -> re-run
  rules_service.seed_rules_into_db() -> re-run embed_rule_book() here.
  The YAML file no longer exists anywhere in this pipeline.

WHAT GETS EMBEDDED vs WHAT'S JUST METADATA:
  The embedded TEXT is name + description + group + severity — the
  semantically meaningful part a natural-language query should match
  against. Administrative fields (handler, eval_status, notes) are
  stored as METADATA only, not embedded — they're not what a user's
  question would semantically resemble, and stuffing them into the
  embedded text would dilute the match quality.

  eval_status IS carried in metadata (not embedded) specifically so
  the agent (Step 6) can tell, after retrieval, whether a rule is
  actually checkable against live data right now — Step 3's whole
  point was making that distinction explicit; RAG must not lose it.

PROJECT PATH:  rag/rule_store.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import chromadb

from core.database import Database
from core.logging_config import setup_logging
from rag.embedder import DEFAULT_MODEL, RuleEmbedder

logger = setup_logging(__name__)

COLLECTION_NAME = "rule_book_v2"   # v2: schema changed from POC-03 (eval_status added)
DEFAULT_PERSIST = "data/chroma_db"


def _build_document(rule: dict) -> str:
    """
    The text that actually gets embedded for semantic search. Kept to
    the semantically meaningful fields only — see module docstring.
    """
    return (
        f"{rule['name']}. {rule['description']} "
        f"Category: {rule.get('group', 'General')}. "
        f"Severity: {rule.get('severity', 'MEDIUM')}."
    )


class RuleStore:
    """
    ChromaDB vector store for the ArthaChakra rule book, sourced from
    MongoDB (not YAML, not a static Python list) — see module docstring.
    """

    def __init__(
        self,
        persist_dir: str = DEFAULT_PERSIST,
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._embedder = RuleEmbedder(model_name=model_name)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedder.as_chromadb_fn(),
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "RuleStore ready | persist=%s | collection=%s | rules=%d",
            persist_dir, COLLECTION_NAME, self.count(),
        )

    # ── Write operations ───────────────────────────────────────────────

    def embed_from_mongo(self, db: Database, overwrite: bool = False) -> int:
        """
        Reads every rule currently in db.platform_rules and embeds it.
        This is THE Step 4 entry point — Mongo is the source of truth,
        not seed_rules.py directly (seed_rules.py is what populates
        Mongo in the first place, via rules_service.seed_rules_into_db).

        If overwrite=True, deletes everything currently stored first —
        use this after editing rule wording, so stale embeddings don't
        linger alongside updated ones.

        Returns the number of rules embedded.
        """
        rules = db.platform_rules.find({})
        return self._embed_rules(rules, overwrite=overwrite)

    def _embed_rules(self, rules: list[dict], overwrite: bool = False) -> int:
        if overwrite:
            existing = self._collection.get()["ids"]
            if existing:
                self._collection.delete(ids=existing)
                logger.info("Deleted %d existing embeddings before re-embedding.", len(existing))

        if not rules:
            logger.warning("embed_from_mongo: platform_rules is empty — nothing to embed. "
                           "Run rules_service.seed_rules_into_db() first.")
            return 0

        self._collection.add(
            ids=[r["rule_id"] for r in rules],
            documents=[_build_document(r) for r in rules],
            metadatas=[
                {
                    "rule_id": r["rule_id"],
                    "name": r["name"],
                    "category": r.get("category", "MANDATORY"),
                    "group": r.get("group", "General"),
                    "severity": r.get("severity", "MEDIUM"),
                    "eval_status": r.get("eval_status", "NOT_YET_EVALUABLE"),
                    "description": r["description"][:300],
                }
                for r in rules
            ],
        )
        logger.info("Embedded %d rules from MongoDB into ChromaDB.", len(rules))
        return len(rules)

    # ── Query operations ────────────────────────────────────────────────

    def query(
        self,
        text: str,
        n_results: int = 4,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        eval_status: Optional[str] = None,
    ) -> list[dict]:
        """
        Retrieve the most relevant rules for a natural language query.

        Returns a list of dicts: rule_id, name, category, severity,
        eval_status, description, distance, score (higher = better).
        """
        if self.count() == 0:
            raise RuntimeError(
                "RuleStore is empty. Run embed_from_mongo() first.\n"
                "  python scripts/embed_rulebook.py"
            )

        where = self._build_filter(category, severity, eval_status)
        raw = self._collection.query(
            query_texts=[text],
            n_results=min(n_results, self.count()),
            include=["documents", "metadatas", "distances"],
            where=where,
        )

        results = []
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i]
            dist = raw["distances"][0][i]
            results.append({
                "rule_id": meta.get("rule_id", ""),
                "name": meta.get("name", ""),
                "category": meta.get("category", ""),
                "severity": meta.get("severity", ""),
                "eval_status": meta.get("eval_status", ""),
                "description": meta.get("description", ""),
                "distance": round(dist, 4),
                "score": round(max(0.0, 1.0 - dist / 2.0), 4),
            })
        return results

    def query_for_prompt(self, text: str, n_results: int = 4) -> str:
        """
        Retrieved rules formatted ready to inject into an LLM system
        prompt (Step 6's agent will call this). Flags any retrieved
        rule that isn't actually checkable yet, rather than letting
        the agent confidently cite something with no real data behind
        it.
        """
        results = self.query(text, n_results=n_results)
        if not results:
            return "No relevant rules found."

        lines = ["RELEVANT RULES FROM RULE BOOK (retrieved for this query):"]
        for r in results:
            flag = "" if r["eval_status"] == "EVALUABLE" else f"  [{r['eval_status']} — not live-checkable yet]"
            lines.append(
                f"\n[{r['rule_id']} — {r['severity']}] {r['name']}{flag}\n"
                f"  {r['description']}"
            )
        return "\n".join(lines)

    # ── Utility ─────────────────────────────────────────────────────────

    def count(self) -> int:
        return self._collection.count()

    def list_rule_ids(self) -> list[str]:
        return self._collection.get()["ids"]

    def get_rule(self, rule_id: str) -> Optional[dict]:
        result = self._collection.get(ids=[rule_id], include=["metadatas", "documents"])
        if not result["ids"]:
            return None
        meta = result["metadatas"][0]
        return {**meta, "document": result["documents"][0]}

    def reset(self) -> None:
        """Delete everything from the store. Requires re-embedding."""
        all_ids = self._collection.get()["ids"]
        if all_ids:
            self._collection.delete(ids=all_ids)
            logger.warning("RuleStore reset: deleted %d embeddings.", len(all_ids))

    @staticmethod
    def _build_filter(
        category: Optional[str], severity: Optional[str], eval_status: Optional[str],
    ) -> Optional[dict]:
        conditions = []
        if category:
            conditions.append({"category": {"$eq": category}})
        if severity:
            conditions.append({"severity": {"$eq": severity}})
        if eval_status:
            conditions.append({"eval_status": {"$eq": eval_status}})
        if not conditions:
            return None
        return {"$and": conditions} if len(conditions) > 1 else conditions[0]
