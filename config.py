"""
config.py
───────────
Central environment configuration for ArthaChakra.

Every other module imports `settings` from here instead of calling
os.getenv() directly — so there is exactly one place that knows about
environment variable names. Change a variable name once, here, and
nothing else needs touching.

PROJECT PATH:  config.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongo_uri:     str
    mongo_db_name: str
    log_level:     str
    log_dir:       str
    dhan_client_id:    str
    dhan_access_token: str
    anthropic_api_key: str
    haiku_model:       str
    sonnet_model:      str

def _load() -> Settings:
    return Settings(
        mongo_uri     = os.getenv("ARTHACHAKRA_MONGO_URI", "mongodb://localhost:27017"),
        mongo_db_name = os.getenv("ARTHACHAKRA_DB_NAME", "arthachakra"),
        log_level     = os.getenv("LOG_LEVEL", "INFO"),
        log_dir       = os.getenv("LOG_DIR", "logs/"),
        dhan_client_id    = os.getenv("DHAN_CLIENT_ID", ""),
        dhan_access_token = os.getenv("DHAN_ACCESS_TOKEN", ""),
        # Step 6 — one shared Anthropic account bills for every user.
        # No per-user keys; this is the simplest model and matches how
        # Dhan is already handled (one shared account, not per-user).
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", ""),
        haiku_model       = os.getenv("ARTHACHAKRA_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
        sonnet_model      = os.getenv("ARTHACHAKRA_SONNET_MODEL", "claude-sonnet-4-6"),
    )


settings = _load()
