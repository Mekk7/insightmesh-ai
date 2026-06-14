# backend/api/insightmesh/paste.py
"""
Paste-to-analyze — the universal, unblockable review source.

WHY THIS EXISTS
---------------
Amazon/Flipkart and many retailers actively block scrapers. Rather than ship a
fragile scraper that breaks in a day (and lies to the user when it does), we let
the user PASTE reviews directly — copied from any site, a spreadsheet column, an
email, anywhere. We split the blob into individual reviews and run the exact same
analyzer the live pipeline uses, so the full dashboard (incl. the debate) works
on real data the user already has.

This is the honest escape hatch: it works for ANY product, in ANY country, with
zero blocking. It also doubles as the ingestion path for the future paid-provider
integration (that provider would just hand us a list of review strings).
"""
from __future__ import annotations

import re
from time import perf_counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.endpoints.analyze_reviews import analyze_core

router = APIRouter()


class PasteInput(BaseModel):
    text: Optional[str] = Field(default=None, description="Raw pasted reviews (one per line or separated by blank lines)")
    reviews: Optional[List[str]] = Field(default=None, description="Pre-split list of reviews (alternative to text)")
    product: str = Field(..., description="Product name, used for relevance + naming")
    strictness: Optional[str] = Field(default="normal")


# Split on newlines, blank-line blocks, or common review separators.
_SPLIT_RE = re.compile(r"\n\s*\n|\r\n\r\n|\n[-=*•]{2,}\n", re.MULTILINE)


def _split_reviews(text: str) -> List[str]:
    """
    Turn a pasted blob into individual reviews. Handles three common shapes:
      1) blank-line-separated paragraphs (most copy-pastes)
      2) one-review-per-line
      3) a single block (kept as one)
    We pick whichever yields the most sensible count.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Try blank-line blocks first
    blocks = [b.strip() for b in _SPLIT_RE.split(text) if b.strip()]
    if len(blocks) >= 2:
        return blocks

    # Fall back to line-per-review (only if lines look substantive)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    substantive = [ln for ln in lines if len(ln) >= 15]
    if len(substantive) >= 2:
        return lines

    # Single block
    return [text]


@router.post(
    "/paste",
    summary="Analyze pasted reviews — universal, unblockable source for any product",
)
def paste(payload: PasteInput) -> Dict[str, Any]:
    t0 = perf_counter()
    product = (payload.product or "").strip()
    if not product:
        raise HTTPException(400, "A product name is required.")

    reviews: List[str] = []
    if payload.reviews:
        reviews = [str(r).strip() for r in payload.reviews if str(r).strip()]
    elif payload.text:
        reviews = _split_reviews(payload.text)

    # Light cleanup: drop trivially short fragments, cap to a sane batch size.
    reviews = [r for r in reviews if len(r) >= 8][:200]
    if not reviews:
        raise HTTPException(400, "Couldn't find any usable reviews in what you pasted. Paste at least a couple of sentences.")

    strictness = (payload.strictness or "normal").lower()

    try:
        analysis = analyze_core(reviews, query=product, strictness=strictness, meta=None)
    except Exception as e:
        raise HTTPException(500, f"Analyzer failed on pasted reviews: {e}")

    elapsed_ms = int((perf_counter() - t0) * 1000)

    # Shape the response to match run_pipeline's final_report so the SAME
    # dashboard renders it without any special-casing on the frontend.
    final = {
        "meta": {
            "user_mode": "consumer",
            "mode": "paste",
            "query_used": product,
            "source": "pasted",
            "elapsed_ms": elapsed_ms,
            "from_cache": False,
        },
        "platforms": {},
        "contributions": {"per_platform": []},
        "analysis": analysis,
    }
    return {"final_report": final}
