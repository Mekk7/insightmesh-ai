# backend/api/insightmesh/orchestrator.py

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Tuple
import os
import logging
import requests
import re
import pandas as pd

# In-process categorize (+ path resolver)
from backend.api.insightmesh.categorize import categorize_service, _resolve_path as _resolve_dataset_path

# Column detection for time inference
from backend.utils.column_guesser import guess_columns

# Plugin registry (ctx-aware)
from .plugins import PLUGINS

# ---- Env / URL joining (mirror pipeline’s behavior) ----
BASE_URL   = os.getenv("BASE_URL", "http://127.0.0.1:8000")
API_PREFIX = os.getenv("API_PREFIX", "/api")  # set to "" to mount at root
DEFAULT_STRICTNESS = (os.getenv("FILTER_STRICTNESS", "normal") or "normal").strip().lower()
DEFAULT_STRICTNESS = DEFAULT_STRICTNESS if DEFAULT_STRICTNESS in {"low", "normal", "high"} else "normal"

def _join(a: str, b: str) -> str:
    if a.endswith("/") and b.startswith("/"):
        return a[:-1] + b
    if not a.endswith("/") and not b.startswith("/"):
        return a + "/" + b
    return a + b

def _candidate_urls(suffix: str) -> List[str]:
    candidates: List[str] = []
    candidates.append(_join(BASE_URL, _join("/api", suffix)))
    if API_PREFIX and API_PREFIX != "/api":
        candidates.append(_join(BASE_URL, _join(API_PREFIX, suffix)))
    candidates.append(_join(BASE_URL, suffix))
    seen, uniq = set(), []
    for u in candidates:
        if u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

# ---- Time inference (lightweight, memory-friendly) -------------------------

def _infer_time_from_csv(path: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort global min/max date + granularity for the dataset.
    Returns:
      {
        "time_from": iso,
        "time_to": iso,
        "span_days": int,
        "granularity": "daily|weekly|monthly|quarterly|yearly|unknown",
      }
    or None if we can't infer.
    """
    try:
        # Peek to guess columns robustly
        head = pd.read_csv(path, nrows=5000, low_memory=True)
        roles = guess_columns(head)
        date_col = roles.get("date")
        if not date_col or date_col not in head.columns:
            return None

        global_min, global_max = None, None
        sample_dates: List[pd.Timestamp] = []

        for chunk in pd.read_csv(
            path,
            usecols=[date_col],
            parse_dates=[date_col],
            infer_datetime_format=True,
            chunksize=200_000
        ):
            s = pd.to_datetime(chunk[date_col], errors="coerce")
            s = s.dropna()
            if s.empty:
                continue
            cmin, cmax = s.min(), s.max()
            global_min = cmin if global_min is None else min(global_min, cmin)
            global_max = cmax if global_max is None else max(global_max, cmax)
            sample_dates.extend(s.head(200).tolist())
            if len(sample_dates) > 2000:
                sample_dates = sample_dates[:2000]

        if not global_min or not global_max:
            return None

        # Granularity (coarse)
        gran = "unknown"
        if sample_dates:
            ser = pd.Series(sorted(sample_dates))
            try:
                diffs = ser.diff().dropna().dt.days
                if not diffs.empty:
                    med = float(diffs.median())
                    if med <= 2:   gran = "daily"
                    elif med <= 10: gran = "weekly"
                    elif med <= 45: gran = "monthly"
                    elif med <= 120: gran = "quarterly"
                    else:           gran = "yearly"
            except Exception:
                pass

        span_days = max(1, (global_max - global_min).days or 1)
        return {
            "time_from": global_min.to_pydatetime().isoformat(),
            "time_to":   global_max.to_pydatetime().isoformat(),
            "span_days": int(span_days),
            "granularity": gran,
        }
    except Exception as e:
        logging.warning(f"[orchestrator:time_infer] failed: {e}")
        return None

# ---- Models -----------------------------------------------------------------

class OrchestrateInput(BaseModel):
    filepath: str
    # Keep this list in sync with PLUGINS by default
    platforms: List[str] = Field(default_factory=lambda: list(PLUGINS.keys()))
    # Optional strictness override for filtering (low|normal|high)
    strictness: Optional[str] = None

class OrchestrateOutput(BaseModel):
    job_id: str
    status: str

router = APIRouter()

@router.get("/orchestrate/_ping")
def orchestrate_ping() -> Dict[str, Any]:
    return {
        "ok": True,
        "available_platforms": list(PLUGINS.keys()),
        "base_url": BASE_URL,
        "api_prefix": API_PREFIX,
    }

@router.post(
    "/orchestrate",
    response_model=OrchestrateOutput,
    summary="Kick-off full scraping pipeline (dataset-first, background)"
)
async def orchestrate(payload: OrchestrateInput, background_tasks: BackgroundTasks):
    # 1) Categorize in-process (for tags/products/categories)
    try:
        result = categorize_service(payload.filepath)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected categorize failure: {e}")

    tags = result.get("search_tags") or []
    if not tags:
        raise HTTPException(status_code=400, detail="No search_tags returned from categorize.")

    # 2) Build context (company mode) with time inference
    dataset_path = _resolve_dataset_path(payload.filepath)
    inferred = _infer_time_from_csv(dataset_path) or {}
    strict = (payload.strictness or DEFAULT_STRICTNESS)
    if strict not in {"low", "normal", "high"}:
        strict = DEFAULT_STRICTNESS

    query = " ".join(tags)[:120] if tags else ""

    ctx: Dict[str, Any] = {
        "mode": "company",
        "query": query,
        "tags": tags[:8],
        "time_from": inferred.get("time_from"),
        "time_to": inferred.get("time_to"),
        "strictness": strict,
        "granularity": inferred.get("granularity"),
    }

    # 3) Queue each scraper
    job_id = os.urandom(8).hex()
    queued: List[str] = []
    skipped: List[str] = []

    for platform in payload.platforms:
        if platform not in PLUGINS:
            skipped.append(platform); continue
        queued.append(platform)
        background_tasks.add_task(_launch_scraper, job_id, platform, ctx)

    logging.info(f"[orchestrate] job_id={job_id} queued={queued} skipped={skipped} ctx={{'query': '{query}', 'tags': {tags}, 'tfrom': {ctx.get('time_from')}, 'tto': {ctx.get('time_to')}, 'strict': '{strict}'}}")

    return {"job_id": job_id, "status": "started"}

def _launch_scraper(job_id: str, platform: str, ctx: Dict[str, Any]) -> None:
    """
    Background worker: looks up plugin, builds suffix & payload from ctx,
    and POSTs to the best /api candidate URLs (with fallbacks).
    """
    try:
        plugin = PLUGINS.get(platform)
        if not plugin:
            logging.warning(f"[orchestrate:{job_id}] unknown platform '{platform}'")
            return

        # New-style: pass context; fallback to legacy tags if plugin expects list[str]
        try:
            suffix, payload = plugin["launch"](ctx)
        except TypeError:
            suffix, payload = plugin["launch"](ctx.get("tags", []))

        errors: List[str] = []
        for url in _candidate_urls(suffix):
            try:
                resp = requests.post(url, json=payload, timeout=30)
                if resp.ok:
                    logging.info(f"[orchestrate:{job_id}] {platform} → {url} [OK {resp.status_code}]")
                    return
                errors.append(f"{url} -> {resp.status_code}")
            except Exception as e:
                errors.append(f"{url} -> {type(e).__name__}: {e}")

        logging.error(f"[orchestrate:{job_id}] {platform} all candidates failed: {errors}")

    except Exception as e:
        logging.exception(f"[orchestrate:{job_id}] {platform} unexpected error: {e}")
