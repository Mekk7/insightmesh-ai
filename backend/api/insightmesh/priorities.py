# backend/api/insightmesh/priorities.py
"""
Personal Priorities endpoint.

POST /api/insightmesh/priorities/reweight
Body: {"overview": {...}, "priorities": ["battery", "price"]}
Returns: {personalized_trust_score, decision_flip, matters_to_you, missing_priorities}

The frontend calls this when the consumer picks/changes their priorities,
re-renders the dashboard with the new personalized numbers without re-running
the whole pipeline.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.insight.priorities.reweight import reweight_for_priorities

router = APIRouter()


class PrioritiesInput(BaseModel):
    overview: Dict[str, Any]
    priorities: List[str]


@router.post("/priorities/reweight", summary="Recompute TrustScore + verdict for the user's priorities")
def reweight(payload: PrioritiesInput) -> Dict[str, Any]:
    if not payload.priorities:
        raise HTTPException(400, "At least one priority required.")
    if len(payload.priorities) > 8:
        raise HTTPException(400, "Maximum 8 priorities at a time.")
    return reweight_for_priorities(payload.overview, payload.priorities)
