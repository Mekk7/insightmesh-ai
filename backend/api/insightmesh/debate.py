# backend/api/insightmesh/debate.py
"""
Skeptic vs Advocate — debate API.

On-demand endpoint (NOT part of the main pipeline, so it never slows a normal
analysis). The frontend calls it when the user clicks "Start the debate" on a
finished report. Two AI agents argue FOR and AGAINST, each citing real reviews
by number, and a Judge returns an honestly-calibrated verdict.

The user can also pass `question` to steer the debate ("but I care about
back-seat space"), turning the static report into an interactive argument.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:
    from backend.insight.debate.engine import run_debate
except Exception:
    run_debate = None


router = APIRouter()


class DebateInput(BaseModel):
    report: Optional[Dict[str, Any]] = None          # the current final_report
    question: Optional[str] = Field(default=None)     # optional user steer


@router.post(
    "/debate",
    summary="Run the Skeptic-vs-Advocate evidence-grounded debate on a report",
)
def debate(payload: DebateInput) -> Dict[str, Any]:
    if run_debate is None:
        raise HTTPException(503, "Debate engine unavailable.")
    if not isinstance(payload.report, dict):
        raise HTTPException(400, "A finished analysis `report` is required.")

    analysis = payload.report.get("analysis") or {}
    overview = analysis.get("overview") or {}
    per_review = analysis.get("per_review") or []
    product = (payload.report.get("meta") or {}).get("query_used") or "this product"

    q = (payload.question or "").strip() or None
    if q and len(q) > 500:
        raise HTTPException(400, "Question is too long (max 500 chars).")

    try:
        result = run_debate(overview, per_review, product=product, user_question=q)
    except Exception as e:
        raise HTTPException(500, f"Debate failed: {e}")
    return result
