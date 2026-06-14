# backend/utils/logging_config.py
"""
Centralized logging configuration.

Two modes via env var `LOG_FORMAT`:
  - "text"  (default): human-readable, colored-ish console output
  - "json"           : single-line JSON per record, ideal for log aggregators

Other env vars:
  LOG_LEVEL          DEBUG | INFO | WARNING | ERROR (default: INFO)

Usage in main.py:

    from backend.utils.logging_config import setup_logging
    setup_logging()  # call once at startup

Modules then just use:

    import logging
    log = logging.getLogger("insightmesh.somecomponent")
    log.info("hello %s", "world")

Adds an `extras` mechanism for structured fields:

    log.info("scrape complete", extra={"platform": "youtube", "kept": 42})
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict


# Fields we always serialize at the top level when present on the record
_TOP_FIELDS = {"platform", "kept", "dropped", "elapsed_ms", "query", "user_mode",
               "cache_key", "run_id", "n_kept", "n_analyzed", "strictness"}

# LogRecord built-ins we shouldn't echo into the JSON payload
_BUILTIN_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    """JSON line formatter — one object per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Promote known fields if present; bundle the rest under "extra"
        extras: Dict[str, Any] = {}
        for k, v in record.__dict__.items():
            if k in _BUILTIN_ATTRS:
                continue
            if k.startswith("_"):
                continue
            if k in _TOP_FIELDS:
                payload[k] = v
            else:
                extras[k] = v
        if extras:
            payload["extra"] = extras

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            # Fallback to plain text if something weird is in the record
            return f"{payload['ts']} {payload['level']} {payload['logger']}: {payload['msg']}"


class TextFormatter(logging.Formatter):
    """Concise console format. Default if LOG_FORMAT != 'json'."""

    DEFAULT_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self.DEFAULT_FMT, datefmt=self.DEFAULT_DATEFMT)


def setup_logging(level: str | None = None, fmt: str | None = None) -> None:
    """
    Idempotent. Safe to call more than once (clears existing handlers).
    """
    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    out_fmt = (fmt or os.getenv("LOG_FORMAT", "text")).lower()

    root = logging.getLogger()
    # Remove any pre-existing handlers (uvicorn adds its own)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if out_fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())
    handler.setLevel(lvl)

    root.addHandler(handler)
    root.setLevel(lvl)

    # Tame noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "transformers", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
