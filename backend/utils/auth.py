# backend/utils/auth.py
"""
Optional API-key authentication.

Behavior:
  - If env var INSIGHTMESH_API_KEY is unset/empty, auth is DISABLED.
    The backend behaves exactly as it does in dev. This is the default.
  - If INSIGHTMESH_API_KEY is set, every request must carry the key in one of:
      X-API-Key: <key>
      Authorization: Bearer <key>
    otherwise we return 401.
  - Paths in PUBLIC_PATHS bypass auth (so /docs and /ping keep working).

Why an opt-in API key and not full OAuth/JWT?
  - InsightMesh is typically deployed inside trusted networks or as a
    single-tenant service. A long random pre-shared key is enough at
    this scale, and one env var keeps the surface area tiny.
  - For multi-user / per-user scoping, swap in a JWT layer later — the
    middleware shape stays the same.

To enable:
    export INSIGHTMESH_API_KEY="$(openssl rand -hex 32)"
    # then in clients:  curl -H "X-API-Key: <that key>" ...

Multiple keys can be configured for rotation:
    INSIGHTMESH_API_KEYS="key1,key2,key3"     (comma-separated, takes precedence)
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional, Set

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("insightmesh.auth")

# Paths that should NEVER require auth (docs, health checks, root)
PUBLIC_PATHS: Set[str] = {
    "/",
    "/ping",
    "/__routes__",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
}

# Path prefixes that bypass auth (useful for docs subroutes etc.)
PUBLIC_PREFIXES = ("/docs/", "/redoc/")


def _load_keys() -> Set[str]:
    """Read keys from env. Supports a single key or a comma-separated list."""
    multi = (os.getenv("INSIGHTMESH_API_KEYS") or "").strip()
    if multi:
        return {k.strip() for k in multi.split(",") if k.strip()}
    single = (os.getenv("INSIGHTMESH_API_KEY") or "").strip()
    return {single} if single else set()


def _extract_key(request: Request) -> Optional[str]:
    """Pick the presented key from headers, in priority order."""
    # 1) X-API-Key
    k = request.headers.get("x-api-key")
    if k:
        return k.strip()
    # 2) Authorization: Bearer <key>
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    # 3) Query param fallback (?api_key=) — handy for browser-side SSE,
    #    since the EventSource API can't add custom headers.
    qk = request.query_params.get("api_key")
    if qk:
        return qk.strip()
    return None


def is_auth_enabled() -> bool:
    return len(_load_keys()) > 0


async def auth_middleware(request: Request, call_next):
    """
    FastAPI HTTP middleware. Mount with:
        @app.middleware("http")
        async def auth(request, call_next): return await auth_middleware(request, call_next)
    """
    keys = _load_keys()
    if not keys:
        # Auth disabled — pass through
        return await call_next(request)

    path = request.url.path
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)

    presented = _extract_key(request)
    if presented and presented in keys:
        return await call_next(request)

    log.warning("[auth] rejected request to %s from %s", path, request.client.host if request.client else "?")
    return JSONResponse(
        status_code=401,
        content={
            "error": "Unauthorized",
            "message": "Missing or invalid API key.",
            "hint": "Send X-API-Key header or Authorization: Bearer <key>.",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )
