# backend/api/insightmesh/history.py
"""
Run-history endpoints. Mounted under /api/insightmesh.

Endpoints:
    GET    /history                  — paginated list of past runs (summary fields)
    GET    /history/{run_id}         — full run including final_report
    GET    /history/search?q=...     — text-search by query/filepath
    GET    /history/stats            — aggregate counts & averages
    DELETE /history/{run_id}         — delete a single run
    POST   /history/clear            — delete ALL runs (irreversible)
    GET    /history/_ping            — health
    GET    /history/cache/stats      — cache stats
    POST   /history/cache/clear      — clear caches
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.utils.cache import all_caches_stats, clear_all_caches
from backend.utils.db import (
    clear_history,
    delete_run,
    get_run,
    history_stats,
    list_runs,
    search_runs,
)

router = APIRouter()


@router.get("/history/_ping", tags=["History"])
def history_ping() -> Dict[str, Any]:
    try:
        st = history_stats()
        return {"ok": True, "total_runs": st["total_runs"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/history", tags=["History"], summary="List past pipeline runs (newest first)")
def history_list(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_mode: Optional[str] = Query(None, pattern="^(consumer|company)$"),
    only_successful: bool = Query(False),
) -> Dict[str, Any]:
    items = list_runs(
        limit=limit,
        offset=offset,
        user_mode=user_mode,
        only_successful=only_successful,
    )
    return {"items": items, "limit": limit, "offset": offset, "count": len(items)}


@router.get("/history/search", tags=["History"], summary="Search runs by query or filepath")
def history_search(
    q: str = Query(..., min_length=1, description="Substring to match against query/filepath"),
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    items = search_runs(q, limit=limit)
    return {"items": items, "needle": q, "count": len(items)}


@router.get("/history/stats", tags=["History"], summary="Aggregate run statistics")
def history_stats_endpoint() -> Dict[str, Any]:
    return history_stats()


@router.get("/history/{run_id}", tags=["History"], summary="Get full run (incl. final_report)")
def history_get(run_id: int) -> Dict[str, Any]:
    row = get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")
    return row


@router.delete("/history/{run_id}", tags=["History"], summary="Delete a single run")
def history_delete(run_id: int) -> Dict[str, Any]:
    ok = delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")
    return {"ok": True, "deleted": run_id}


@router.post("/history/clear", tags=["History"], summary="Delete ALL run history (irreversible)")
def history_clear() -> Dict[str, Any]:
    n = clear_history()
    return {"ok": True, "deleted": n}


# -------------------- Cache management ---------------------------------

@router.get("/history/cache/stats", tags=["History"], summary="Cache statistics (hit rate, size)")
def cache_stats() -> Dict[str, Any]:
    return all_caches_stats()


@router.post("/history/cache/clear", tags=["History"], summary="Clear all in-process caches")
def cache_clear() -> Dict[str, Any]:
    return {"ok": True, "cleared": clear_all_caches()}
