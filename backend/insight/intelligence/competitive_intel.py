"""
Competitive Intelligence Extractor.

Scans per_review data for competitor mentions, extracts comparison dimensions,
and builds a structured comparison matrix (rows = competitors, columns =
dimensions like price / performance / features). This is the most commercially
valuable intelligence feature — it tells a product team WHERE they lose and win
versus named rivals, grounded in real reviewer quotes.

Design (see CLAUDE.md "FEATURE 1"):
  • ONE gpt-4o-mini call, NOT per-review. We pre-filter (cheap regex) to the
    reviews that actually mention/compare a competitor, bundle them into a single
    prompt, and let the model emit a flat list of extractions.
  • Pure fail-open: missing LLM / no comparisons / bad JSON → returns an empty
    result (total_comparisons == 0) and the dashboard renders without the card.
  • The aggregation into the matrix is plain Python — no second LLM call.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List

log = logging.getLogger("insightmesh.competitive_intel")

_MAX_REVIEWS_IN_PROMPT = 60      # bound the single prompt
_MAX_QUOTE = 200
_MAX_EVIDENCE_PER_DIM = 3

# Comparison cue words — a review with one of these (and a proper-noun-ish token)
# is worth sending even if it names a competitor we didn't know about.
_CMP_CUE = re.compile(
    r"(compared to|better than|worse than|\bvs\.?\b|\bversus\b|instead of|"
    r"switch(ed|ing)? (to|from)|over the|rather than|as good as|beats?\b|"
    r"prefer .+ over|went (back )?to|moved to)",
    re.I,
)


def _text_of(review: Dict[str, Any]) -> str:
    return (review.get("translated_text") or review.get("original") or "").strip()


def _competitor_regex(known_competitors: List[str]):
    names = [re.escape(c.strip()) for c in (known_competitors or []) if c and c.strip()]
    if not names:
        return None
    return re.compile(r"\b(" + "|".join(names) + r")\b", re.I)


def _select_comparison_reviews(per_review: List[Dict[str, Any]],
                               known_competitors: List[str]) -> List[Dict[str, Any]]:
    """Reviews that mention a known competitor OR carry comparison language."""
    comp_re = _competitor_regex(known_competitors)
    selected: List[Dict[str, Any]] = []
    for r in per_review:
        if not isinstance(r, dict):
            continue
        text = _text_of(r)
        if not text:
            continue
        if (comp_re and comp_re.search(text)) or _CMP_CUE.search(text):
            selected.append(r)
        if len(selected) >= _MAX_REVIEWS_IN_PROMPT:
            break
    return selected


def _build_prompt(reviews: List[Dict[str, Any]], product_name: str,
                  known_competitors: List[str]) -> str:
    numbered = []
    for i, r in enumerate(reviews):
        numbered.append(f"[{i}] {_text_of(r)[:400]}")
    return f"""Analyze these {len(reviews)} reviews about {product_name or 'the product'}.
For each review that compares {product_name or 'the product'} to a competitor, extract:
- competitor_name: which competitor (use the name as written by the reviewer)
- dimension: the aspect being compared, as ONE short lowercase noun phrase
  (e.g. price, performance, features, build, ecosystem, game library, battery, comfort)
- winner: "target" if {product_name or 'the product'} wins, "competitor" if the competitor wins, "tie" if equal
- quote: the short key phrase showing the comparison (verbatim, < 200 chars)

Known competitors: {', '.join(known_competitors) or '(none — detect from the text)'}

Reviews:
{chr(10).join(numbered)}

Return ONLY JSON of the form:
{{"extractions": [
  {{"competitor_name": "...", "dimension": "...", "winner": "target|competitor|tie", "quote": "..."}}
]}}
Only include reviews that contain an ACTUAL comparison to a competing product. An empty list is fine."""


def _norm_dim(dim: str) -> str:
    d = (dim or "").strip().lower()
    d = re.sub(r"[^a-z0-9 /+-]", "", d)
    return d[:40]


def _canon_competitor(name: str, known_competitors: List[str]) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    low = n.lower()
    for k in known_competitors or []:
        if k and (k.lower() in low or low in k.lower()):
            return k  # snap to the canonical known name
    return n


def _verdict_for(target_wins: int, competitor_wins: int) -> str:
    if competitor_wins > target_wins:
        return "competitor_advantage"
    if target_wins > competitor_wins:
        return "target_advantage"
    return "tie"


def _aggregate(extractions: List[Dict[str, Any]], product_name: str,
               known_competitors: List[str]) -> Dict[str, Any]:
    matrix: Dict[str, Any] = {}
    total = 0

    for ext in extractions:
        if not isinstance(ext, dict):
            continue
        comp = _canon_competitor(str(ext.get("competitor_name") or ""), known_competitors)
        dim = _norm_dim(str(ext.get("dimension") or ""))
        winner = str(ext.get("winner") or "").strip().lower()
        quote = str(ext.get("quote") or "").strip()[:_MAX_QUOTE]
        if not comp or not dim or winner not in ("target", "competitor", "tie"):
            continue

        total += 1
        slot = matrix.setdefault(comp, {"mention_count": 0, "dimensions": {}})
        slot["mention_count"] += 1
        dslot = slot["dimensions"].setdefault(
            dim, {"target_wins": 0, "competitor_wins": 0, "tie": 0, "evidence": []}
        )
        if winner == "target":
            dslot["target_wins"] += 1
        elif winner == "competitor":
            dslot["competitor_wins"] += 1
        else:
            dslot["tie"] += 1
        if quote and len(dslot["evidence"]) < _MAX_EVIDENCE_PER_DIM:
            dslot["evidence"].append(quote)

    # finalize per-dimension and per-competitor verdicts
    overall_target = 0
    overall_competitor = 0
    for comp, slot in matrix.items():
        comp_target = comp_competitor = 0
        for dim, dslot in slot["dimensions"].items():
            dslot["verdict"] = _verdict_for(dslot["target_wins"], dslot["competitor_wins"])
            if dslot["verdict"] == "target_advantage":
                comp_target += 1
            elif dslot["verdict"] == "competitor_advantage":
                comp_competitor += 1
        overall_target += comp_target
        overall_competitor += comp_competitor
        if comp_competitor > comp_target:
            slot["overall_verdict"] = "competitor_preferred"
        elif comp_target > comp_competitor:
            slot["overall_verdict"] = "target_preferred"
        else:
            slot["overall_verdict"] = "mixed"
        # confidence grows with evidence volume, capped
        slot["overall_confidence"] = round(min(0.9, 0.4 + 0.06 * slot["mention_count"]), 2)

    # overall competitive position
    decided = overall_target + overall_competitor
    if decided == 0:
        position = "competitive"
    else:
        win_ratio = overall_target / decided
        if win_ratio >= 0.7:
            position = "dominant"
        elif win_ratio >= 0.5:
            position = "competitive"
        elif win_ratio >= 0.3:
            position = "challenged"
        else:
            position = "trailing"

    key_finding = _key_finding(matrix, product_name)

    return {
        "competitors_found": list(matrix.keys()),
        "total_comparisons": total,
        "matrix": matrix,
        "competitive_position": position,
        "key_finding": key_finding,
    }


def _key_finding(matrix: Dict[str, Any], product_name: str) -> str:
    """Compose a one-line headline from where the target wins/loses (no LLM)."""
    if not matrix:
        return ""
    losing: List[str] = []
    winning: List[str] = []
    top_comp = max(matrix.items(), key=lambda kv: kv[1].get("mention_count", 0), default=None)
    if not top_comp:
        return ""
    comp_name, slot = top_comp
    for dim, dslot in slot.get("dimensions", {}).items():
        if dslot.get("verdict") == "competitor_advantage":
            losing.append(dim)
        elif dslot.get("verdict") == "target_advantage":
            winning.append(dim)
    name = product_name or "This product"
    parts = []
    if losing:
        parts.append(f"loses to {comp_name} on {', '.join(losing[:3])}")
    if winning:
        parts.append(f"wins on {', '.join(winning[:3])}")
    if not parts:
        return f"{name} is closely matched against {comp_name}."
    return f"{name} " + " but ".join(parts) + "."


def extract_competitive_intel(per_review: List[Dict[str, Any]],
                              product_name: str,
                              known_competitors: List[str],
                              product_aspects: List[str]) -> Dict[str, Any]:
    """Entry point. Returns the competitive matrix dict (or an empty-but-valid
    result with total_comparisons == 0). Never raises."""
    empty = {
        "competitors_found": [], "total_comparisons": 0, "matrix": {},
        "competitive_position": "competitive", "key_finding": "",
    }
    try:
        per_review = per_review or []
        if not per_review:
            return empty

        from backend.utils.llm import chat_json, available_backend
        if available_backend() == "none":
            return empty

        comparison_reviews = _select_comparison_reviews(per_review, known_competitors or [])
        if not comparison_reviews:
            return empty

        prompt = _build_prompt(comparison_reviews, product_name, known_competitors or [])
        result = chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1400,
        )
        extractions = []
        if isinstance(result, dict):
            extractions = result.get("extractions") or []
        elif isinstance(result, list):
            extractions = result
        if not isinstance(extractions, list) or not extractions:
            return empty

        agg = _aggregate(extractions, product_name, known_competitors or [])
        log.info("[competitive_intel] %d comparisons across %d competitors (position=%s)",
                 agg["total_comparisons"], len(agg["competitors_found"]),
                 agg["competitive_position"])
        return agg
    except Exception as e:
        log.warning("[competitive_intel] skipped (%s)", e)
        return empty
