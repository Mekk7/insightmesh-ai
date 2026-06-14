# backend/insight/intent/buyer_intent.py
"""
Buyer Intent Classifier.

Sentiment tells you how someone *feels*. Buyer intent tells you what they're going to *do*.
For a consumer dashboard, that's the killer signal: "47% of negative reviewers said
they're returning it" is more decision-useful than "average sentiment is 2.8 stars."

Intent classes:
  BUY        — "thinking of getting one", "ordered last week", "in my cart"
  OWN        — "had mine for a year", "we've owned this", "after 6 months"
  RETURN     — "returning it", "got a refund", "RMA"
  RECOMMEND  — "would recommend", "telling my friends", "10/10"
  AVOID      — "don't buy", "stay away", "warning others"
  WAIT       — "waiting for v2", "watching for now", "saving up"
  COMPARE    — actively comparing to another product (also extracts target)
  UNKNOWN    — no detectable intent (silent on action)

Implementation:
  - Primary path: regex pattern banks with high-precision phrases. Fast, no model.
  - Optional path: when the unified LLM client (Ollama → OpenAI) is available,
    refine ambiguous cases with a constrained JSON classification call.
  - Aggregator: collapse per-review intent into a corpus-level distribution.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


# --- High-precision pattern banks ------------------------------------------

_PATTERNS: Dict[str, List[re.Pattern]] = {
    "BUY": [
        re.compile(r"\b(thinking|considering|planning|going|about) to (buy|get|order|purchase|grab|cop)\b", re.I),
        re.compile(r"\b(just|finally) (ordered|bought|got|picked up|received)\b", re.I),
        re.compile(r"\b(pre-?order(?:ed)?|on (?:my )?(?:wish ?list|order))\b", re.I),
        re.compile(r"\b(in (?:my|the) cart|added to cart|on (?:my )?radar)\b", re.I),
        re.compile(r"\b(buying|getting) (?:it|one|this) (?:tomorrow|next week|soon|asap)\b", re.I),
        re.compile(r"\b(can'?t wait to (?:buy|get|try|receive))\b", re.I),
    ],
    "OWN": [
        re.compile(r"\b(had (?:mine|it|this) for|owned (?:for|since)|been using (?:for|since))\b", re.I),
        re.compile(r"\b(after (?:\d+|a few|several) (?:weeks?|months?|years?))\b", re.I),
        re.compile(r"\b(my (?:daily|main) driver|long.?term (?:review|owner))\b", re.I),
    ],
    "RETURN": [
        re.compile(r"\b(returned? (?:it|this|mine)|sent (?:it )?back|got (?:a |my )?refund|refunded|rma'?d?|exchanged)\b", re.I),
        re.compile(r"\b(taking it back|returning (?:it|mine)|going back to (?:the )?store|repackaging)\b", re.I),
        re.compile(r"\b(lemon|defective unit|dud)\b", re.I),
    ],
    "RECOMMEND": [
        re.compile(r"\b(would (?:definitely |totally |highly )?recommend|10/10|five stars?\s*$|hands? down)\b", re.I),
        re.compile(r"\b(telling (?:everyone|my friends|family)|made (?:a |my )?friend buy)\b", re.I),
        re.compile(r"\b(buy without (?:hesitation|second thought)|no.?brainer)\b", re.I),
        re.compile(r"\b(absolutely (?:worth|love) (?:it|this))\b", re.I),
    ],
    "AVOID": [
        re.compile(r"\b(do(?:n'?t| not) buy|stay away|avoid (?:it|this|at all costs)|warning (?:others|y'?all))\b", re.I),
        re.compile(r"\b(save your money|not worth (?:it|a (?:penny|dime))|skip (?:it|this))\b", re.I),
        re.compile(r"\b(biggest (?:mistake|regret)|wish i (?:never|hadn'?t) (?:bought|got))\b", re.I),
        re.compile(r"\b(0/10|zero stars?|don'?t (?:bother|waste))\b", re.I),
    ],
    "WAIT": [
        re.compile(r"\b(wait(?:ing)? for (?:v\d|the next|version 2|gen \d|the (?:update|fix|release)))\b", re.I),
        re.compile(r"\b(watching for now|on the fence|holding off|saving up)\b", re.I),
        re.compile(r"\b(maybe (?:later|next year)|come back when)\b", re.I),
    ],
    "COMPARE": [
        re.compile(r"\b(compared? to|vs\.?|versus|switch(?:ed|ing) from|after (?:my|the))\s+([A-Z][A-Za-z0-9\-\s]{2,30})", re.I),
        re.compile(r"\b(better than|worse than)\s+([A-Z][A-Za-z0-9\-\s]{2,30})", re.I),
        re.compile(r"\b(coming from (?:a |an |my )?)([A-Z][A-Za-z0-9\-\s]{2,30})", re.I),
    ],
}


def _detect_first(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Walk patterns in priority order; return (intent_label, comparison_target)."""
    # RETURN / AVOID are decision-critical, check first
    priority = ["RETURN", "AVOID", "RECOMMEND", "BUY", "WAIT", "OWN", "COMPARE"]
    comparison_target = None
    for label in priority:
        for pat in _PATTERNS[label]:
            m = pat.search(text)
            if m:
                if label == "COMPARE" and len(m.groups()) >= 2:
                    comparison_target = m.group(2).strip().rstrip(".,!?")
                return label, comparison_target
    return None, comparison_target


def classify(text: str) -> Dict[str, Any]:
    """
    Classify one review's buyer intent. Returns:
      {
        "intent": "BUY"|"RETURN"|"RECOMMEND"|"AVOID"|"WAIT"|"OWN"|"COMPARE"|"UNKNOWN",
        "compared_to": str | None,
        "confidence": float,
      }
    """
    if not text or not text.strip():
        return {"intent": "UNKNOWN", "compared_to": None, "confidence": 0.0}
    label, target = _detect_first(text)
    if label is None:
        return {"intent": "UNKNOWN", "compared_to": None, "confidence": 0.0}
    # Higher confidence for short, direct signals
    conf = 0.85 if len(text) < 280 else 0.7
    return {"intent": label, "compared_to": target, "confidence": conf}


def classify_batch(texts: List[str]) -> List[Dict[str, Any]]:
    return [classify(t or "") for t in texts]


# --- Aggregation -----------------------------------------------------------

_LABEL_ORDER = ["BUY", "OWN", "RETURN", "RECOMMEND", "AVOID", "WAIT", "COMPARE", "UNKNOWN"]
_LABEL_PRETTY = {
    "BUY": "Buying / ordered",
    "OWN": "Owns it",
    "RETURN": "Returning / refunding",
    "RECOMMEND": "Recommending",
    "AVOID": "Warning others off",
    "WAIT": "Waiting for next version",
    "COMPARE": "Comparing alternatives",
    "UNKNOWN": "No stated action",
}
_LABEL_TONE = {
    "BUY": "blue",
    "OWN": "zinc",
    "RETURN": "red",
    "RECOMMEND": "green",
    "AVOID": "red",
    "WAIT": "amber",
    "COMPARE": "indigo",
    "UNKNOWN": "zinc",
}


def aggregate_intents(per_review_intents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns a corpus-level summary:
      {
        "distribution": [{label, pretty, count, pct, tone}],
        "compared_products": [{name, count}],
        "decision_health": {
          "buy_pct": float, "return_pct": float, "recommend_pct": float, "avoid_pct": float,
          "net_intent": float          # (recommend + buy) - (avoid + return), normalized
        }
      }
    """
    if not per_review_intents:
        return {"distribution": [], "compared_products": [], "decision_health": {}}

    counts = Counter()
    targets: Counter = Counter()
    for item in per_review_intents:
        label = (item or {}).get("intent") or "UNKNOWN"
        counts[label] += 1
        if item.get("compared_to"):
            targets[item["compared_to"]] += 1

    total = sum(counts.values()) or 1
    distribution = []
    for label in _LABEL_ORDER:
        c = counts.get(label, 0)
        if c == 0 and label == "UNKNOWN":
            # Always include UNKNOWN for completeness
            pass
        distribution.append({
            "label": label,
            "pretty": _LABEL_PRETTY[label],
            "count": c,
            "pct": round(100 * c / total, 1),
            "tone": _LABEL_TONE[label],
        })

    buy = counts.get("BUY", 0) / total
    own = counts.get("OWN", 0) / total
    ret = counts.get("RETURN", 0) / total
    rec = counts.get("RECOMMEND", 0) / total
    avo = counts.get("AVOID", 0) / total
    net = (rec + buy) - (avo + ret)
    decision_health = {
        "buy_pct": round(buy * 100, 1),
        "own_pct": round(own * 100, 1),
        "return_pct": round(ret * 100, 1),
        "recommend_pct": round(rec * 100, 1),
        "avoid_pct": round(avo * 100, 1),
        "net_intent": round(net, 3),  # -1..+1
    }

    compared_products = [{"name": name, "count": cnt} for name, cnt in targets.most_common(6)]

    return {
        "distribution": distribution,
        "compared_products": compared_products,
        "decision_health": decision_health,
    }
