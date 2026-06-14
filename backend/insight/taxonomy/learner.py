# backend/insight/taxonomy/learner.py
"""
Dynamic Aspect Taxonomy Learner.

The system used to hand-code aspect dictionaries per domain (auto, audio, XR,
phone). That meant:
  - Robot vacuums → no idea what to do
  - Video games → no idea what to do
  - Restaurants, fitness equipment, software → no idea what to do

This module solves it: for ANY product, look at 15-25 sample reviews and let
the LLM propose the actual aspect taxonomy that fits this specific product.
Returns a structured list of aspects with keyword variants.

Resolution:
  1. LLM (Ollama → OpenAI): asks the model to read sample reviews and identify
     8-12 specific aspects with 4-6 keyword variants each
  2. Fallback: the existing hand-coded DOMAIN_ASPECTS dictionary if LLM unavailable
  3. Last resort: universal aspects (price, quality, support, etc.)

The taxonomy is CACHED by product query — if you analyze "PS5" twice within
24 hours, the same aspect dictionary is used (consistent dashboards across runs).

Public API:
  learn_aspect_taxonomy(query, sample_reviews) -> {
    "aspects": {
      "<aspect_key>": {
        "label": "Human-friendly name",
        "aliases": ["keyword1", "keyword2", ...],
        "phrases": ["multi-word phrase 1", ...],
      },
      ...
    },
    "source": "llm" | "domain_dict" | "universal_fallback",
    "domain_detected": str | None,
  }
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

try:
    from backend.utils import llm as llm_client
except Exception:
    llm_client = None

# Import the existing hand-coded dictionaries as a fallback
try:
    from backend.insight.absa.aspect_sentiment import (
        UNIVERSAL_ASPECTS,
        DOMAIN_ASPECTS,
        DOMAIN_HINTS,
        _detect_domain,
    )
except Exception:
    UNIVERSAL_ASPECTS = {}
    DOMAIN_ASPECTS = {}
    DOMAIN_HINTS = []

    def _detect_domain(query, corpus_text):  # type: ignore
        return None


log = logging.getLogger("insightmesh.taxonomy")

# In-memory taxonomy cache keyed by normalized query (24h TTL)
_TAX_CACHE: Dict[str, Dict[str, Any]] = {}
_TAX_TTL_SEC = 24 * 3600


def _norm_query(query: str) -> str:
    return re.sub(r"\W+", " ", (query or "").lower()).strip()


def _llm_propose_taxonomy(query: str, sample_reviews: List[str]) -> Optional[Dict[str, Any]]:
    """Ask the LLM to read samples and propose an aspect taxonomy. Returns None on failure."""
    if llm_client is None or llm_client.available_backend() == "none":
        return None
    if not sample_reviews:
        return None

    # Trim each review and the count to keep token budget tight
    cleaned = [s[:300].strip() for s in sample_reviews if s and s.strip()]
    sample_blob = "\n---\n".join(cleaned[:20])[:5000]

    prompt = f"""You are a product-review analyst. Look at these real reviews for "{query}" and identify the 8-12 SPECIFIC aspects reviewers care about.

REVIEWS:
{sample_blob}

Rules:
- Aspects must be SPECIFIC to this product, not generic.
- A good aspect has 4-8 keyword variants reviewers actually use.
- Use lowercase snake_case for the aspect key.
- Use plain words people use in reviews, not marketing terms.
- Include universal aspects (price, quality, support, durability) only if reviewers actually discuss them.

Return EXACTLY this JSON shape (no markdown fences, no extra prose):
{{
  "aspects": {{
    "<aspect_key>": {{
      "label": "Human-friendly aspect name (Title Case)",
      "aliases": ["keyword", "another keyword", "synonym"],
      "phrases": ["multi-word phrase 1", "another phrase"]
    }}
  }}
}}

Aim for 8-12 aspects total. Keywords should be all lowercase.
"""

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=900,
        )
        if not isinstance(parsed, dict):
            return None
        aspects = parsed.get("aspects")
        if not isinstance(aspects, dict) or not aspects:
            return None

        # Sanity-check the structure: each entry must have at least aliases or phrases
        cleaned_aspects: Dict[str, Dict[str, List[str]]] = {}
        for key, spec in aspects.items():
            if not isinstance(spec, dict):
                continue
            label = spec.get("label") or key.replace("_", " ").title()
            aliases = [a.lower().strip() for a in (spec.get("aliases") or []) if isinstance(a, str) and a.strip()]
            phrases = [p.lower().strip() for p in (spec.get("phrases") or []) if isinstance(p, str) and p.strip()]
            if not aliases and not phrases:
                continue
            cleaned_aspects[re.sub(r"\W+", "_", key.lower()).strip("_") or "aspect"] = {
                "label": label,
                "aliases": aliases[:10],
                "phrases": phrases[:6],
            }

        if len(cleaned_aspects) < 4:
            return None  # Too thin to be useful

        return cleaned_aspects
    except Exception as e:
        log.debug("[taxonomy] LLM call failed: %s", e)
        return None


def _domain_dict_taxonomy(query: str, corpus_text: str) -> Optional[Dict[str, Any]]:
    """Use the existing hand-coded dictionary if the product matches a known domain."""
    domain = _detect_domain(query, corpus_text)
    if not domain:
        return None
    domain_aspects = DOMAIN_ASPECTS.get(domain) or {}
    if not domain_aspects:
        return None
    out: Dict[str, Dict[str, Any]] = {}
    # Universal aspects come first
    for key, spec in UNIVERSAL_ASPECTS.items():
        out[key] = {
            "label": key.replace("_", " ").title(),
            "aliases": list(spec.get("aliases") or []),
            "phrases": list(spec.get("phrases") or []),
        }
    # Then domain-specific aspects (override universals if same key)
    for key, spec in domain_aspects.items():
        out[key] = {
            "label": key.replace("_", " ").title(),
            "aliases": list(spec.get("aliases") or []),
            "phrases": list(spec.get("phrases") or []),
        }
    return {"aspects": out, "source": "domain_dict", "domain_detected": domain}


def _universal_fallback() -> Dict[str, Any]:
    """Last resort — works on literally any product but is generic."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, spec in UNIVERSAL_ASPECTS.items():
        out[key] = {
            "label": key.replace("_", " ").title(),
            "aliases": list(spec.get("aliases") or []),
            "phrases": list(spec.get("phrases") or []),
        }
    return {"aspects": out, "source": "universal_fallback", "domain_detected": None}


def learn_aspect_taxonomy(
    query: str,
    sample_reviews: List[str],
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Returns an aspect taxonomy for THIS product, derived from real reviews.

    Caches per normalized query for 24h. Result shape:
      {
        "aspects": { aspect_key: {label, aliases, phrases} },
        "source":  "llm" | "domain_dict" | "universal_fallback",
        "domain_detected": str | None,
      }
    """
    qn = _norm_query(query)
    now = time.time()

    if not force_refresh and qn in _TAX_CACHE:
        entry = _TAX_CACHE[qn]
        if now - entry.get("_ts", 0) < _TAX_TTL_SEC:
            return entry["data"]

    # Try LLM first (the smart path)
    llm_aspects = _llm_propose_taxonomy(query, sample_reviews)
    if llm_aspects:
        result = {"aspects": llm_aspects, "source": "llm", "domain_detected": None}
        _TAX_CACHE[qn] = {"_ts": now, "data": result}
        return result

    # Fallback: hand-coded domain dictionary
    corpus = " ".join((sample_reviews or [])[:50])[:6000]
    domain_result = _domain_dict_taxonomy(query, corpus)
    if domain_result:
        _TAX_CACHE[qn] = {"_ts": now, "data": domain_result}
        return domain_result

    # Last resort: universal aspects only
    result = _universal_fallback()
    _TAX_CACHE[qn] = {"_ts": now, "data": result}
    return result
