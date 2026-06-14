"""
Purchase Decision Engine (Customer View).

Generates personalized buy/wait/skip recommendations based on buyer personas
DETECTED FROM the reviews and the rest of the analysis context. This is what
turns the Customer toggle from a cosmetic switch into a genuinely different
intelligence layer for BUYERS (not product teams).

Design (see CLAUDE.md "FEATURE 3"):
  • ONE gpt-4o-mini call with structured JSON output. The prompt receives the
    aspect breakdown, competitive position, deal-breaker findings, trust /
    confidence, and the top praise + complaint themes.
  • Only PRODUCES data (stored on overview["purchase_advice"]). The Company view
    ignores it; the Customer view reads it.
  • Pure fail-open: missing LLM / bad JSON → returns None and the Customer view
    falls back to its existing rendering.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("insightmesh.purchase_advisor")

_VALID_VERDICT = {"BUY", "WAIT", "SKIP"}


def _aspect_lines(overview: Dict[str, Any]) -> List[str]:
    aspects = (overview.get("aspect_sentiment") or {}).get("aspects") or []
    out = []
    for a in aspects[:12]:
        if not isinstance(a, dict):
            continue
        nm = a.get("aspect") or a.get("label") or a.get("name") or "?"
        rating = a.get("score")
        if rating is None:
            rating = a.get("sentiment")
        out.append(f"  - {nm}: {rating}")
    return out


def _cluster_lines(overview: Dict[str, Any]) -> List[str]:
    clusters = overview.get("canonical_clusters") or []
    out = []
    for c in clusters[:10]:
        if not isinstance(c, dict):
            continue
        reason = c.get("reason") or c.get("canonical_reason") or "?"
        cnt = c.get("count") or c.get("size") or ""
        share = c.get("share_%")
        out.append(f"  - {reason}" + (f" ({share}%)" if share is not None else "")
                   + (f" [{cnt} mentions]" if cnt else ""))
    return out


def _praise_lines(overview: Dict[str, Any]) -> List[str]:
    love = overview.get("what_users_love") or []
    out = []
    for item in love[:6]:
        if isinstance(item, dict):
            out.append(f"  - {item.get('theme') or item.get('reason') or item.get('label') or item}")
        elif isinstance(item, str):
            out.append(f"  - {item}")
    return out


def _build_prompt(overview: Dict[str, Any], product_name: str,
                  product_intel: Dict[str, Any]) -> str:
    conf = overview.get("_analysis_confidence") or {}
    comp = overview.get("competitive_intelligence") or {}
    deal = overview.get("dealbreakers") or {}
    pi = product_intel or {}

    competitors = pi.get("key_competitors") or comp.get("competitors_found") or []
    price_tier = pi.get("price_tier") or "?"
    category = pi.get("category") or "?"

    lost_to = deal.get("lost_to") or {}
    deal_reasons = [d.get("reason") for d in (deal.get("top_reasons") or []) if d.get("reason")]

    return f"""You are a no-nonsense buying advisor. Using the analysis of real reviews for
"{product_name or 'the product'}" ({category}, price tier {price_tier}), produce a personalized
buy/wait/skip recommendation for different kinds of buyers.

ASPECT RATINGS (name: rating):
{chr(10).join(_aspect_lines(overview)) or '  (none)'}

TOP THEMES (complaints + praise, by share):
{chr(10).join(_cluster_lines(overview)) or '  (none)'}

WHAT USERS LOVE:
{chr(10).join(_praise_lines(overview)) or '  (none)'}

COMPETITIVE POSITION: {comp.get('competitive_position', 'unknown')}
KEY COMPETITIVE FINDING: {comp.get('key_finding', '(none)')}
KNOWN COMPETITORS: {', '.join(competitors) or '(none)'}

DEAL-BREAKERS: {deal.get('total_dealbreakers', 0)} reviewers expressed return/switch/regret
({(deal.get('dealbreaker_rate') or 0) * 100:.0f}% of reviews). Reasons: {', '.join(deal_reasons) or '(none)'}.
Switching to: {', '.join(lost_to.keys()) or '(none)'}.

ANALYSIS CONFIDENCE: {conf.get('label', '?')} ({conf.get('overall', '?')})
AVERAGE RATING: {overview.get('average_sentiment', '?')}

DETECT buyer personas FROM the themes (e.g. if reviewers mention gaming, there is a "Gamer"
persona; if productivity/work, a "Professional" persona; if casual/media, a "Casual" persona).
For each persona, base "detected_from" on how many themes/reviews point to that use case.

Return ONLY this JSON:
{{
  "verdict": "BUY" | "WAIT" | "SKIP",
  "verdict_confidence": 0.0-1.0,
  "one_line": "one sentence overall recommendation",
  "personas": [
    {{
      "name": "persona name",
      "detected_from": <int reviews/themes pointing here>,
      "recommendation": "BUY" | "WAIT" | "SKIP",
      "reason": "why, referencing concrete aspects/ratings",
      "best_aspects": ["..."],
      "worst_aspects": ["..."]
    }}
  ],
  "alternatives": [
    {{"product": "competitor name", "why": "...", "when_better": "...", "when_worse": "..."}}
  ],
  "wait_for": ["concrete things that would make this a clear buy"]
}}
Base everything on the data above. Do not invent aspects not present. 2-4 personas."""


def _coerce_verdict(v: Any, default: str = "WAIT") -> str:
    s = str(v or "").strip().upper()
    return s if s in _VALID_VERDICT else default


def _sanitize(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    verdict = _coerce_verdict(result.get("verdict"))
    try:
        vc = float(result.get("verdict_confidence"))
        vc = max(0.0, min(1.0, vc))
    except (TypeError, ValueError):
        vc = 0.5

    personas = []
    for p in (result.get("personas") or []):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        try:
            detected = int(p.get("detected_from") or 0)
        except (TypeError, ValueError):
            detected = 0
        personas.append({
            "name": str(p.get("name")).strip(),
            "detected_from": detected,
            "recommendation": _coerce_verdict(p.get("recommendation")),
            "reason": str(p.get("reason") or "").strip(),
            "best_aspects": [str(x) for x in (p.get("best_aspects") or []) if x][:5],
            "worst_aspects": [str(x) for x in (p.get("worst_aspects") or []) if x][:5],
        })

    alternatives = []
    for a in (result.get("alternatives") or []):
        if not isinstance(a, dict) or not a.get("product"):
            continue
        alternatives.append({
            "product": str(a.get("product")).strip(),
            "why": str(a.get("why") or "").strip(),
            "when_better": str(a.get("when_better") or "").strip(),
            "when_worse": str(a.get("when_worse") or "").strip(),
        })

    wait_for = [str(x).strip() for x in (result.get("wait_for") or []) if str(x).strip()][:6]

    if not personas and not result.get("one_line"):
        return None

    return {
        "verdict": verdict,
        "verdict_confidence": round(vc, 2),
        "one_line": str(result.get("one_line") or "").strip(),
        "personas": personas,
        "alternatives": alternatives,
        "wait_for": wait_for,
    }


def generate_purchase_advice(overview: Dict[str, Any],
                             per_review: List[Dict[str, Any]],
                             product_name: str,
                             product_intel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Entry point. Returns the purchase-advice dict, or None (fail-open)."""
    try:
        if not isinstance(overview, dict):
            return None
        from backend.utils.llm import chat_json, available_backend
        if available_backend() == "none":
            return None

        prompt = _build_prompt(overview, product_name, product_intel or {})
        result = chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1200,
        )
        advice = _sanitize(result if isinstance(result, dict) else {})
        if advice:
            log.info("[purchase_advisor] verdict=%s conf=%.2f personas=%d",
                     advice["verdict"], advice["verdict_confidence"], len(advice["personas"]))
        return advice
    except Exception as e:
        log.warning("[purchase_advisor] skipped (%s)", e)
        return None
