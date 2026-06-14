# backend/main.py

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router as api_router  # aggregator of all endpoint routers
from backend.utils.auth import auth_middleware, is_auth_enabled
from backend.utils.logging_config import setup_logging
from backend.utils.ratelimit import (
    CLASS_DEFAULTS as RATE_LIMIT_CLASSES,
    ENABLED as RATE_LIMIT_ENABLED,
    rate_limit_middleware,
)


def _ensure_leading_slash(p: str) -> str:
    p = (p or "/api").strip()
    return p if p.startswith("/") else f"/{p}"


API_PREFIX = _ensure_leading_slash(os.getenv("API_PREFIX", "/api"))

# Configure logging FIRST so startup events come through formatted
setup_logging()
log = logging.getLogger("insightmesh")


# -------------------- Lifespan (startup/shutdown) --------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- Startup ----
    # Ensure SQLite schema exists for run history (best-effort, never crashes).
    try:
        from backend.utils.db import init_db
        init_db()
        log.info("[startup] run-history DB ready")
    except Exception as e:
        log.warning("[startup] run-history DB init failed (continuing): %s", e)

    # Pre-warm caches (no-op if unused)
    try:
        from backend.utils.cache import scraper_cache, pipeline_cache
        scraper_cache(); pipeline_cache()
        log.info("[startup] caches initialized")
    except Exception as e:
        log.warning("[startup] cache init failed (continuing): %s", e)

    # Auth + rate-limit status
    if is_auth_enabled():
        log.info("[startup] auth ENABLED (API key required)")
    else:
        log.info("[startup] auth DISABLED (no INSIGHTMESH_API_KEY set)")
    if RATE_LIMIT_ENABLED:
        log.info("[startup] rate limiting ENABLED: %s",
                 ", ".join(f"{k}={v[0]}/{v[1]}s" for k, v in RATE_LIMIT_CLASSES.items()))
    else:
        log.info("[startup] rate limiting DISABLED")

    # LLM brain status — show immediately so a dead key/missing Ollama is obvious
    try:
        from backend.utils.llm import available_backend, OLLAMA_MODEL, OPENAI_MODEL
        brain = available_backend()
        if brain == "ollama":
            log.info("[startup] LLM brain: OLLAMA (%s) \u2714", OLLAMA_MODEL)
        elif brain == "openai":
            log.info("[startup] LLM brain: OPENAI (%s) \u2714", OPENAI_MODEL)
        else:
            log.warning("[startup] LLM brain: NONE \u2718 \u2014 analysis will use dumb heuristics only!")
    except Exception as e:
        log.warning("[startup] LLM brain check failed: %s", e)

    yield

    # ---- Shutdown ----
    log.info("[shutdown] bye")


app = FastAPI(
    title="InsightMesh AI Backend",
    description="Backend engine for AI-driven sales forecasting and insight analysis.",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS for the frontend(s) — tighten via env in production
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",")] if _cors_origins_env != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- Middleware stack --------------------
# Order matters: middleware added LAST runs FIRST on the request.
# Desired request flow:
#   incoming → rate-limit → auth → handler
# So we add auth FIRST (inner), then rate-limit (outer).

@app.middleware("http")
async def _auth_layer(request: Request, call_next):
    return await auth_middleware(request, call_next)


@app.middleware("http")
async def _ratelimit_layer(request: Request, call_next):
    return await rate_limit_middleware(request, call_next)


# -------------------- Top-level routes --------------------

@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "InsightMesh AI backend is live.",
        "docs": ["/docs", "/redoc"],
        "api_prefix": API_PREFIX,
        "version": app.version,
        "auth_required": is_auth_enabled(),
    }


@app.get("/ping")
async def ping():
    return {"status": "online", "message": "pong"}


# Prefix-aligned API health (used by pipeline fallbacks)
@app.get(f"{API_PREFIX}/_ping")
async def api_ping():
    return {"ok": True, "prefix": API_PREFIX}


@app.get("/__routes__")
def list_routes():
    return [
        {"path": r.path, "methods": sorted(list(getattr(r, "methods", [])))}
        for r in app.routes
    ]


# Mount the unified API router under the env-configurable prefix
app.include_router(api_router, prefix=API_PREFIX)
