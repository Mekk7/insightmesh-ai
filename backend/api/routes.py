# backend/api/routes.py

from fastapi import APIRouter, Request

# Core endpoints
from backend.api.endpoints import (
    understand,       # /understand/upload
    forecast,         # /forecast/predict
    analyze_reviews,  # /reviews/analyze
    scrape_reviews,   # /reviews/scrape/youtube
    reddit_scraper,   # /reviews/scrape/reddit
)

# App-store (Apple + Google Play) review source — optional, never crash boot
try:
    from backend.api.endpoints import appstore_scraper  # /reviews/scrape/appstore
except Exception:  # pragma: no cover
    appstore_scraper = None

# InsightMesh endpoints
from backend.api.insightmesh.categorize   import router as categorize_router
from backend.api.insightmesh.run_pipeline import router as pipeline_router
from backend.api.insightmesh.history     import router as history_router
from backend.api.insightmesh.export      import router as export_router
from backend.api.insightmesh.stream      import router as stream_router
from backend.api.insightmesh.demo        import router as demo_router
from backend.api.insightmesh.discover    import router as discover_router
from backend.api.insightmesh.ask         import router as ask_router
from backend.api.insightmesh.priorities  import router as priorities_router
from backend.api.insightmesh.debate      import router as debate_router
from backend.api.insightmesh.paste       import router as paste_router

# Orchestrator is optional; don't crash if missing
try:
    from backend.api.insightmesh.orchestrator import router as orchestrator_router
except Exception:  # pragma: no cover
    orchestrator_router = None

router = APIRouter()

# --- Diagnostics ------------------------------------------------------------
@router.get("/__routes__", tags=["Diagnostics"])
def __routes__(request: Request):
    out = []
    for r in request.app.routes:
        path = getattr(r, "path", None)
        methods = list(getattr(r, "methods", []) or [])
        if path:
            out.append({"path": path, "methods": methods})
    return out


@router.get("/llm/status", tags=["Diagnostics"])
def llm_status():
    """Quick check: which LLM backend (if any) is live right now.
    The frontend polls this to show a 'brain online/offline' indicator."""
    from backend.utils.llm import status
    return status()

# --- Module 1: Data Understanding ------------------------------------------
router.include_router(understand.router, prefix="/understand", tags=["Understand"])

# --- Module 2: Forecasting --------------------------------------------------
router.include_router(forecast.router, prefix="/forecast", tags=["Forecast"])

# --- Module 3: Review Intelligence -----------------------------------------
# All review-related routes live under /api/reviews/*
router.include_router(scrape_reviews.router, prefix="/reviews", tags=["Reviews"])   # /scrape/youtube
router.include_router(reddit_scraper.router,  prefix="/reviews", tags=["Reviews"])  # /scrape/reddit
router.include_router(analyze_reviews.router, prefix="/reviews", tags=["Reviews"])  # /analyze
if appstore_scraper is not None:
    router.include_router(appstore_scraper.router, prefix="/reviews", tags=["Reviews"])  # /scrape/appstore

# --- InsightMesh umbrella ---------------------------------------------------
# All insightmesh routes live under /api/insightmesh/*
router.include_router(categorize_router,   prefix="/insightmesh", tags=["InsightMesh"])
router.include_router(pipeline_router,     prefix="/insightmesh", tags=["InsightMesh"])
router.include_router(history_router,      prefix="/insightmesh", tags=["InsightMesh"])
router.include_router(export_router,       prefix="/insightmesh", tags=["InsightMesh"])
router.include_router(stream_router,       prefix="/insightmesh", tags=["InsightMesh"])
router.include_router(demo_router,         prefix="",              tags=["Demo"])      # /demo/report, /demo/products
router.include_router(discover_router,     prefix="",              tags=["Discover"])  # /discover/feed, /discover/related
router.include_router(ask_router,          prefix="/insightmesh", tags=["Ask"])       # /insightmesh/ask, /insightmesh/ask/suggestions
router.include_router(priorities_router,   prefix="/insightmesh", tags=["Priorities"])  # /insightmesh/priorities/reweight
router.include_router(debate_router,       prefix="/insightmesh", tags=["Debate"])       # /insightmesh/debate
router.include_router(paste_router,        prefix="/insightmesh", tags=["Paste"])        # /insightmesh/paste

# Optional orchestrator (if present)
if orchestrator_router is not None:
    router.include_router(orchestrator_router, prefix="/insightmesh", tags=["InsightMesh"])
