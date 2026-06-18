"""
core/ids.py
─────────────
Shared id-generation and timestamp helpers.

Every module that needs a unique id (users, broker connections, custom
rules, P&L records, etc.) imports new_id() from here instead of each
defining its own — keeps id format consistent across the whole project.

PROJECT PATH:  core/ids.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str = "") -> str:
    """Generate a short unique id, optionally prefixed (e.g. 'usr_a1b2c3d4e5')."""
    raw = uuid.uuid4().hex[:10]
    return f"{prefix}_{raw}" if prefix else raw


def now_utc() -> datetime:
    """Timezone-aware UTC timestamp — use this everywhere, never naive datetime.now()."""
    return datetime.now(tz=timezone.utc)
