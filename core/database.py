"""
core/database.py
───────────────────
The single Mongo connection point for the entire project.

Every module gets collections via:
    from core.database import Database
    db = Database()
    db.users.find_one({...})
    db.broker_connections.insert_one({...})

Dynamic attribute access (db.users, db.daily_pnl, ...) means adding a
new collection later never requires touching this file — just add it
to users/schema.py's COLLECTION_SCHEMA and start using db.<name>.

MOCK MODE:
  If MongoDB is unreachable, transparently falls back to an in-memory
  store with the same method signatures (find_one, find, insert_one,
  update_one with upsert, delete_one, count_documents). This lets every
  later step's --dod / verify script run without requiring MongoDB to
  be installed first.

PROJECT PATH:  core/database.py
"""

from __future__ import annotations

from typing import Any, Optional

from config import settings
from core.logging_config import setup_logging

logger = setup_logging(__name__)


# ── In-memory fallback collection ───────────────────────────────────────

class InMemoryCollection:
    """
    Minimal pymongo-compatible collection backed by a Python list.
    Supports only what this project needs: find_one, find, insert_one,
    update_one (with upsert), delete_one, count_documents.

    Filters support simple equality matching: {"field": value}.
    Sufficient for dev/testing — NOT a general Mongo query engine.
    """

    def __init__(self) -> None:
        self._docs: list[dict] = []

    @staticmethod
    def _matches(doc: dict, filt: dict) -> bool:
        return all(doc.get(k) == v for k, v in filt.items())

    def find_one(self, filt: dict) -> Optional[dict]:
        for doc in self._docs:
            if self._matches(doc, filt):
                return dict(doc)
        return None

    def find(self, filt: dict | None = None) -> list[dict]:
        filt = filt or {}
        return [dict(d) for d in self._docs if self._matches(d, filt)]

    def insert_one(self, doc: dict) -> None:
        self._docs.append(dict(doc))

    def update_one(self, filt: dict, update: dict, upsert: bool = False) -> None:
        for doc in self._docs:
            if self._matches(doc, filt):
                if "$set" in update:
                    doc.update(update["$set"])
                return
        if upsert:
            new_doc = dict(filt)
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            self._docs.append(new_doc)

    def delete_one(self, filt: dict) -> bool:
        for i, doc in enumerate(self._docs):
            if self._matches(doc, filt):
                del self._docs[i]
                return True
        return False

    def count_documents(self, filt: dict | None = None) -> int:
        return len(self.find(filt))


# ── Real MongoDB adapter ─────────────────────────────────────────────────

class MongoCollectionAdapter:
    """
    Wraps a real pymongo Collection so it returns exactly the same types
    as InMemoryCollection — this is the fix for a real bug class, not
    just one call site: pymongo's .find() returns a lazy Cursor (no
    len(), single-pass iteration), while plain Python code expects a
    list. Every method here normalises the return type so calling code
    never needs to know or care which backend is active.
    """

    def __init__(self, collection) -> None:
        self._coll = collection

    def find_one(self, filt: dict) -> Optional[dict]:
        return self._coll.find_one(filt)

    def find(self, filt: dict | None = None) -> list[dict]:
        return list(self._coll.find(filt or {}))

    def insert_one(self, doc: dict) -> None:
        self._coll.insert_one(doc)

    def update_one(self, filt: dict, update: dict, upsert: bool = False) -> None:
        self._coll.update_one(filt, update, upsert=upsert)

    def delete_one(self, filt: dict) -> bool:
        result = self._coll.delete_one(filt)
        return result.deleted_count > 0

    def count_documents(self, filt: dict | None = None) -> int:
        return self._coll.count_documents(filt or {})

    def create_index(self, keys, unique: bool = False):
        return self._coll.create_index(keys, unique=unique)


# ── Database ───────────────────────────────────────────────────────────

class Database:
    """
    Top-level database handle. Tries real MongoDB first (using
    settings.mongo_uri / settings.mongo_db_name); falls back to
    in-memory collections transparently if the connection fails or
    mock=True is passed explicitly.

    Usage:
        db = Database()                  # auto-detect
        db = Database(mock=True)         # force mock (e.g. unit tests)

        db.users.find_one({"username": "sandeep"})
        db.broker_connections.insert_one({...})
    """

    def __init__(self, mongo_uri: str | None = None, db_name: str | None = None,
                mock: bool = False) -> None:
        self.is_mock = mock
        self._collections: dict[str, Any] = {}
        self._mongo_db = None

        if not mock:
            try:
                from pymongo import MongoClient
                uri  = mongo_uri or settings.mongo_uri
                name = db_name or settings.mongo_db_name
                client = MongoClient(uri, serverSelectionTimeoutMS=2000)
                client.admin.command("ping")    # fail fast if unreachable
                self._mongo_db = client[name]
                # Some MongoDB setups allow unauthenticated 'ping' on admin
                # but require auth for real operations on the target db —
                # exercise an actual command here so we catch that now,
                # not later mid-way through index creation.
                self._mongo_db.command("ping")
                logger.info("Database: connected to MongoDB (%s / %s)", uri, name)
            except Exception as e:
                msg = str(e)
                if "Unauthorized" in msg or "requires authentication" in msg:
                    logger.warning(
                        "Database: MongoDB requires authentication — check that "
                        "ARTHACHAKRA_MONGO_URI includes username:password "
                        "(e.g. mongodb://admin:yourpass@localhost:27017/). "
                        "Falling back to in-memory mock for now."
                    )
                else:
                    logger.warning("Database: MongoDB unavailable (%s) — using in-memory mock", e)
                self.is_mock = True

        if self.is_mock:
            logger.info("Database: running in MOCK mode (in-memory, no persistence)")

    def get_collection(self, name: str):
        """Return a collection by name, creating it (or its mock) on first access."""
        if name not in self._collections:
            if self.is_mock:
                self._collections[name] = InMemoryCollection()
            else:
                self._collections[name] = MongoCollectionAdapter(self._mongo_db[name])
        return self._collections[name]

    def __getattr__(self, name: str):
        """Enables db.users, db.broker_connections, db.daily_pnl, etc."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get_collection(name)

    def ensure_indexes(self, schema: dict) -> int:
        """
        Create every index defined in a COLLECTION_SCHEMA dict (see
        users/schema.py). No-op in mock mode (in-memory store doesn't
        need indexes to behave correctly for the data volumes used in
        dev/testing).

        Failures on individual indexes (e.g. authentication, permissions)
        are logged as warnings and skipped — one bad index should never
        crash the whole verification run. Returns the number of indexes
        successfully created (0 in mock mode).
        """
        if self.is_mock:
            logger.info("ensure_indexes: skipped (mock mode)")
            return 0

        created = 0
        failed  = 0
        for coll_name, spec in schema.items():
            coll = self.get_collection(coll_name)
            for idx in spec.get("indexes", []):
                try:
                    coll.create_index(idx["keys"], unique=idx.get("unique", False))
                    created += 1
                except Exception as e:
                    failed += 1
                    msg = str(e)
                    if "Unauthorized" in msg or "requires authentication" in msg:
                        logger.warning(
                            "ensure_indexes: auth failed on '%s' — check ARTHACHAKRA_MONGO_URI "
                            "includes username:password (same as your other MongoDB-backed "
                            "projects). Skipping this index.", coll_name,
                        )
                    else:
                        logger.warning(
                            "ensure_indexes: failed on '%s' (%s) — skipping", coll_name, msg,
                        )

        if failed:
            logger.warning("ensure_indexes: %d created, %d failed — see warnings above", created, failed)
        else:
            logger.info("ensure_indexes: created %d indexes across %d collections",
                       created, len(schema))
        return created
