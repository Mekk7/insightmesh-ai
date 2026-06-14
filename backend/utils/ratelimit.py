# backend/utils/ratelimit.py
"""
In-process per-IP rate limiter.

Why custom instead of slowapi/limits?
  - Zero new dependencies.
  - Same caveat as cache.py: per-worker only. For multi-worker production,
    swap the backing store for Redis (Lua INCR + EXPIRE).

Algorithm: fixed-window counter per (key, route_class). Resets at the top of
each window. Fast, predictable, no clock skew. Trades some burstiness for
simplicity; if you need a smooth sliding window, replace _count() with a
deque-based algorithm.

Tunables (env vars):
    RATE_LIMIT_ENABLED          default: 1
    RATE_LIMIT_DEFAULT          default: "120/minute"   (route_class: 'default')
    RATE_LIMIT_PIPELINE         default: "30/minute"    (run_pipeline / stream)
    RATE_LIMIT_SCRAPE           default: "60/minute"
    RATE_LIMIT_ANALYZE          default: "60/minute"
    RATE_LIMIT_EXPORT           default: "60/minute"
    RATE_LIMIT_TRUSTED_PROXIES  default: ""            (CSV of proxy IPs)

The middleware decides the route_class from the URL path; you can override
that mapping in `_classify_path`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("insightmesh.ratelimit")


# -------------------- Config --------------------

def _parse_spec(spec: str, default_n: int, default_window: int) -> Tuple[int, int]:
    """Parse '60/minute' or '100/10s' into (count, window_seconds). Tolerant."""
    try:
        s = (spec or "").strip().lower()
        if not s:
            return default_n, default_window
        n_str, unit = s.split("/")
        n = int(n_str.strip())
        unit = unit.strip()
        if unit.endswith("s"):
            try:
                w = int(unit[:-1])
            except Exception:
                w = 1
        elif unit in {"min", "minute"}:
            w = 60
        elif unit in {"hour", "hr", "h"}:
            w = 3600
        elif unit in {"day", "d"}:
            w = 86400
        else:
            w = default_window
        return n, w
    except Exception:
        return default_n, default_window


CLASS_DEFAULTS = {
    "default":  _parse_spec(os.getenv("RATE_LIMIT_DEFAULT",  "120/minute"), 120, 60),
    "pipeline": _parse_spec(os.getenv("RATE_LIMIT_PIPELINE", "30/minute"),   30, 60),
    "scrape":   _parse_spec(os.getenv("RATE_LIMIT_SCRAPE",   "60/minute"),   60, 60),
    "analyze":  _parse_spec(os.getenv("RATE_LIMIT_ANALYZE",  "60/minute"),   60, 60),
    "export":   _parse_spec(os.getenv("RATE_LIMIT_EXPORT",   "60/minute"),   60, 60),
}

ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1") in {"1", "true", "True"}
TRUSTED_PROXIES = {p.strip() for p in (os.getenv("RATE_LIMIT_TRUSTED_PROXIES") or "").split(",") if p.strip()}


# -------------------- Storage --------------------

_store: Dict[Tuple[str, str, int], int] = {}   # (ip, klass, window_id) -> count
_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For only from configured proxies."""
    client_host = request.client.host if request.client else "?"
    if TRUSTED_PROXIES and client_host in TRUSTED_PROXIES:
        xff = request.headers.get("x-forwarded-for") or ""
        first = xff.split(",")[0].strip()
        if first:
            return first
    return client_host


def _classify_path(path: str) -> str:
    """Map a URL path to a rate-limit class."""
    # Keep this list small and explicit; everything else is "default"
    if path.startswith("/api/insightmesh/run_pipeline"):
        return "pipeline"
    if path.startswith("/api/reviews/scrape/"):
        return "scrape"
    if path.startswith("/api/reviews/analyze") or path.startswith("/api/insightmesh/categorize"):
        return "analyze"
    if path.startswith("/api/insightmesh/export") or path.startswith("/api/understand") or path.startswith("/api/forecast"):
        return "export"
    return "default"


def _count(ip: str, klass: str, now: float) -> Tuple[int, int, int]:
    """
    Increment and return (current_count, limit_n, window_seconds).
    """
    n, w = CLASS_DEFAULTS.get(klass, CLASS_DEFAULTS["default"])
    window_id = int(now // w)
    key = (ip, klass, window_id)
    with _lock:
        # Trim old windows occasionally
        if len(_store) > 5000:
            keep = {k: v for k, v in _store.items() if k[2] >= window_id - 1}
            _store.clear()
            _store.update(keep)
        _store[key] = _store.get(key, 0) + 1
        cur = _store[key]
    return cur, n, w


# -------------------- Middleware --------------------

async def rate_limit_middleware(request: Request, call_next):
    if not ENABLED:
        return await call_next(request)

    # Health / docs are not rate-limited
    path = request.url.path
    if path in {"/ping", "/", "/docs", "/redoc", "/openapi.json", "/__routes__"}:
        return await call_next(request)

    ip = _client_ip(request)
    klass = _classify_path(path)
    now = time.time()
    count, limit, window = _count(ip, klass, now)

    if count > limit:
        retry_after = int(window - (now % window)) + 1
        log.info("[ratelimit] %s %s %s exceeded (%d/%d in %ds window)", ip, klass, path, count, limit, window)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Too Many Requests",
                "class": klass,
                "limit": limit,
                "window_seconds": window,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Class": klass,
            },
        )

    resp = await call_next(request)
    # Annotate successful responses too (helpful for debugging)
    resp.headers["X-RateLimit-Limit"] = str(limit)
    resp.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
    resp.headers["X-RateLimit-Class"] = klass
    return resp


def status() -> Dict[str, Dict[str, int]]:
    """Diagnostic — return active classes + their limits."""
    return {
        k: {"limit": n, "window_seconds": w}
        for k, (n, w) in CLASS_DEFAULTS.items()
    }
