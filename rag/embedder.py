"""
rag/embedder.py
───────────────────
Wraps the sentence-transformers embedding model for ChromaDB.

Model choice: all-MiniLM-L6-v2 — same choice validated in the earlier
POC-03 spike (87% average Precision@3 across 5 test queries, ~11ms
per query). No reason to change a model that already worked well.

  • ~80 MB download on first use (cached in ~/.cache/huggingface)
  • 22M parameters — fast CPU inference, no GPU required
  • 384-dimensional embeddings
  • Runs entirely locally — no API key, no per-query cost

PROJECT PATH:  rag/embedder.py
"""

from __future__ import annotations

from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from core.logging_config import setup_logging

logger = setup_logging(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class RuleEmbedder:
    """
    Wrapper around sentence-transformers for ChromaDB embedding.

    Usage:
        embedder = RuleEmbedder()
        chroma_fn = embedder.as_chromadb_fn()
        collection = client.create_collection("rules", embedding_function=chroma_fn)
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        logger.info("Loading embedding model: %s", model_name)
        self._fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
        logger.info("Embedding model ready: %s", model_name)

    def as_chromadb_fn(self) -> SentenceTransformerEmbeddingFunction:
        """Return the ChromaDB-compatible embedding function."""
        return self._fn

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string. Returns a list of floats."""
        return self._fn([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of text strings."""
        return self._fn(texts)

    @property
    def dimensions(self) -> int:
        """Embedding vector size for this model."""
        dims = {"all-MiniLM-L6-v2": 384, "all-mpnet-base-v2": 768}
        return dims.get(self.model_name, 384)
