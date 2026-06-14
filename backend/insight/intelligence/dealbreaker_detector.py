"""
Deal-Breaker & Switching Detector.

Finds reviews where people express the highest-value loss signals for a product
team — because each one is a lost customer, not just a complaint:
  • Returning the product
  • Switching to a competitor
  • Warning others not to buy
  • Regretting their purchase

Pure regex — NO LLM call (see CLAUDE.md "FEATURE 2"). The patterns cover how
real people talk about returning products on YouTube, Reddit, and the App Store.
Fail-open: any error returns an empty-but-valid result.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List

log = logging.getLogger("insightmesh.dealbreaker")

RETURN_PATTERNS = [
    r"(return(ed|ing)?|sent (it )?back|refund|got my money back)",
    r"(taking it back|brought it back|shipping it back)",
]

SWITCH_PATTERNS = [
    r"(switch(ed|ing)? to|went (back )?to|moved to|chose .+ instead)",
    r"(going back to|sticking with|staying with|prefer .+ over)",
    r"(bought .+ instead|ended up (with|getting)|went with)",
]

WARN_PATTERNS = [
    r"(don'?t buy|do not buy|avoid|stay away|waste of money)",
    r"(wouldn'?t recommend|can'?t recommend|not worth|save your money)",
    r"(regret|wish i (hadn'?t|didn'?t)|buyer'?s? remorse)",
    r"(worst purchase|biggest mistake|total waste|rip.?off)",
]

REGRET_PATTERNS = [
    r"(should have (bought|gotten)|wish i (got|bought|went with))",
    r"(if i could go back|if i knew|had i known)",
]

_RETURN_RE = [re.compile(p, re.I) for p in RETURN_PATTERNS]
_SWITCH_RE = [re.compile(p, re.I) for p in SWITCH_PATTERNS]
_WARN_RE = [re.compile(p, re.I) for p in WARN_PATTERNS]
_REGRET_RE = [re.compile(p, re.I) for p in REGRET_PATTERNS]

_MAX_QUOTE = 240
_MAX_SAMPLES = 4


def _text_of(review: Dict[str, Any]) -> str:
    return (review.get("translated_text") or review.get("original") or "").strip()


def _any_match(text: str, regexes) -> bool:
    return any(rx.search(text) for rx in regexes)


def _competitor_in(text: str, known_competitors: List[str]) -> str:
    """Return the first known competitor mentioned in the text, else ''."""
    low = text.lower()
    for c in known_competitors or []:
        if c and c.strip() and c.strip().lower() in low:
            return c.strip()
    return ""


def _reason_of(review: Dict[str, Any]) -> str:
    """The theme that drove the leaving — from the review's cluster assignment."""
    r = (review.get("canonical_reason") or "").strip()
    if r:
        return r
    topic = review.get("cluster_topic")
    if isinstance(topic, list) and topic:
        return str(topic[0]).strip()
    return ""


def _entry(review: Dict[str, Any]) -> Dict[str, Any]:
    text = _text_of(review)
    return {
        "quote": text[:_MAX_QUOTE],
        "reason": _reason_of(review),
        "platform": (review.get("platform")
                     or (review.get("meta") or {}).get("platform") or ""),
    }


def detect_dealbreakers(per_review: List[Dict[str, Any]],
                        product_name: str,
                        known_competitors: List[str]) -> Dict[str, Any]:
    """Scan all reviews for deal-breaker signals. Returns structured findings."""
    findings: Dict[str, Any] = {
        "returns": [], "switches": [], "warnings": [], "regrets": [],
        "total_dealbreakers": 0, "dealbreaker_rate": 0.0,
        "top_reasons": [], "lost_to": {},
    }
    try:
        per_review = per_review or []
        n_total = len(per_review)
        if n_total == 0:
            return findings

        flagged_reasons: Counter = Counter()
        lost_to: Counter = Counter()
        flagged_count = 0

        for review in per_review:
            if not isinstance(review, dict):
                continue
            text = _text_of(review)
            if not text:
                continue

            is_return = _any_match(text, _RETURN_RE)
            is_switch = _any_match(text, _SWITCH_RE)
            is_warn = _any_match(text, _WARN_RE)
            is_regret = _any_match(text, _REGRET_RE)
            if not (is_return or is_switch or is_warn or is_regret):
                continue

            flagged_count += 1
            entry = _entry(review)
            if entry["reason"]:
                flagged_reasons[entry["reason"]] += 1

            if is_return and len(findings["returns"]) < _MAX_SAMPLES * 2:
                findings["returns"].append(entry)
            if is_switch:
                comp = _competitor_in(text, known_competitors or [])
                sw = dict(entry)
                if comp:
                    sw["switched_to"] = comp
                    lost_to[comp] += 1
                if len(findings["switches"]) < _MAX_SAMPLES * 2:
                    findings["switches"].append(sw)
            if is_warn and len(findings["warnings"]) < _MAX_SAMPLES * 2:
                findings["warnings"].append(entry)
            if is_regret and len(findings["regrets"]) < _MAX_SAMPLES * 2:
                findings["regrets"].append(entry)

        findings["total_dealbreakers"] = flagged_count
        findings["dealbreaker_rate"] = round(flagged_count / max(1, n_total), 2)
        findings["top_reasons"] = [
            {"reason": r, "count": c} for r, c in flagged_reasons.most_common(5)
        ]
        findings["lost_to"] = dict(lost_to.most_common(5))

        log.info("[dealbreaker] %d/%d flagged (rate=%.2f) lost_to=%s",
                 flagged_count, n_total, findings["dealbreaker_rate"], findings["lost_to"])
    except Exception as e:
        log.warning("[dealbreaker] skipped (%s)", e)
    return findings
