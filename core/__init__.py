"""core/__init__.py — shared infrastructure: database, logging, ids."""

from core.database import Database, InMemoryCollection
from core.ids import new_id, now_utc
from core.logging_config import setup_logging

__all__ = ["Database", "InMemoryCollection", "new_id", "now_utc", "setup_logging"]
