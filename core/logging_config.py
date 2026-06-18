"""
core/logging_config.py
─────────────────────────
One shared logging setup. Every module calls setup_logging(__name__)
and gets a logger that writes to both console and logs/arthachakra.log
with a consistent format.

ALSO FIXES A WINDOWS-SPECIFIC CRASH:
  Redirecting output to a file (python script.py > out.txt) makes
  Windows use the legacy cp1252 code page instead of the console's
  UTF-8 code page — even though printing directly to the console works
  fine. cp1252 doesn't include box-drawing characters (═, ─) or most
  emoji used throughout this project's output, so the first print()
  containing one crashes with UnicodeEncodeError the moment output is
  piped or redirected. Reconfiguring stdout/stderr to UTF-8 here, once,
  the first time any module sets up logging, fixes this for the whole
  process — every entry-point script imports core.database, which
  calls setup_logging() at import time, before any of its own prints run.

PROJECT PATH:  core/logging_config.py
"""

from __future__ import annotations

import logging
import os
import sys

from config import settings

_console_fixed = False


def _ensure_utf8_console() -> None:
    """
    Make stdout/stderr safe for Unicode regardless of platform or
    whether output is piped/redirected to a file. Idempotent — safe
    to call multiple times.
    """
    global _console_fixed
    if _console_fixed:
        return

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        encoding = getattr(stream, "encoding", None)
        if encoding and encoding.lower() not in ("utf-8", "utf8"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass  # stream doesn't support reconfigure — leave as-is

    _console_fixed = True


def setup_logging(name: str = "arthachakra") -> logging.Logger:
    """
    Return a configured logger. Safe to call repeatedly with the same
    name — won't add duplicate handlers on re-import.
    """
    _ensure_utf8_console()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = logging.FileHandler(
            os.path.join(settings.log_dir, "arthachakra.log"), encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # Read-only filesystem or permissions issue — console logging still works
        pass

    return logger
