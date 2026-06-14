# backend/insight/severity/scorer.py
"""
Complaint Severity Scorer.

Not all complaints are equal:
  - "Phantom braking on highway" → SAFETY / CRITICAL → fix in next OTA
  - "Wish the cupholder was bigger" → COSMETIC / LOW → backlog

We classify each complaint cluster into a severity tier so the next-version
roadmap can rank by urgency, not just by mention count.

Severity tiers:
  CRITICAL  — safety, data loss, security, accessibility blockers, breaking faults
  HIGH      — strong dissatisfaction, return/refund language, frequent show-stoppers
  MEDIUM    — significant pain points without churn language
  LOW       — minor annoyances, cosmetic, nice-to-haves
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# --- Pattern banks for severity detection ----------------------------------

_CRITICAL_CUES = re.compile(
    r"\b("
    r"crash(?:es|ed)?|fire|smoke|burn(?:ed|ing|s)?|"
    r"safety|unsafe|dangerous|hazard|injury|injur(?:ed|y)|hurt|"
    r"data loss|lost (?:my|all) (?:data|files|photos)|"
    r"hack(?:ed|able)|security (?:flaw|breach|vulnerability)|exploit|"
    r"privacy (?:violation|breach)|leaked? (?:data|info)|"
    r"accident|crashed (?:the )?car|"
    r"emergency|stranded|locked out|trapped|"
    r"recall(?:ed)?|class action|lawsuit"
    r")\b",
    re.IGNORECASE,
)

_HIGH_CUES = re.compile(
    r"\b("
    r"return(?:ed|ing)?|refund(?:ed)?|rma|sent (?:it )?back|exchange|"
    r"unusable|useless|broken|doesn'?t work|won'?t work|never works?|"
    r"complete (?:waste|garbage|trash)|biggest mistake|biggest regret|"
    r"lemon|defective|fail(?:ed|s|ure)?|"
    r"do(?:n'?t| not) buy|stay away|avoid|warning|"
    r"worst (?:purchase|product|thing)|"
    r"every (?:day|week|time)|constantly (?:fails|breaks|crashes)"
    r")\b",
    re.IGNORECASE,
)

_LOW_CUES = re.compile(
    r"\b("
    r"wish|would (?:be )?(?:nice|cool|love)|hope(?:fully)?|"
    r"minor (?:issue|complaint|nitpick)|nit ?pick|small thing|"
    r"could be better|slightly|a bit (?:annoying|odd)|"
    r"cosmetic|aesthetic|color choice"
    r")\b",
    re.IGNORECASE,
)

# Accessibility flags get bumped to at least HIGH
_ACCESSIBILITY_CUES = re.compile(
    r"\b("
    r"wheelchair|accessib(?:le|ility)|disabled|disability|"
    r"blind|deaf|hard of hearing|hearing aid|"
    r"colorblind|color.blind|"
    r"motor (?:impairment|control)|"
    r"wcag|ada compliant"
    r")\b",
    re.IGNORECASE,
)


def score_text(text: str) -> Dict[str, Any]:
    """Classify one piece of text. Returns severity tier + flags + score 0..1."""
    if not text or not text.strip():
        return {"severity": "MEDIUM", "score": 0.5, "is_safety": False, "is_accessibility": False, "matched_cues": []}

    matched: List[str] = []

    critical = _CRITICAL_CUES.search(text)
    high = _HIGH_CUES.search(text)
    low = _LOW_CUES.search(text)
    a11y = _ACCESSIBILITY_CUES.search(text)

    if critical:
        matched.append(critical.group(0))
    if high:
        matched.append(high.group(0))
    if low:
        matched.append(low.group(0))
    if a11y:
        matched.append(a11y.group(0))

    is_safety = bool(critical) and any(k in (text or "").lower() for k in ("safety", "unsafe", "dangerous", "hazard", "injury", "fire", "smoke"))
    is_accessibility = bool(a11y)

    # Decide tier (highest wins)
    if critical:
        severity = "CRITICAL"
        score = 1.0
    elif high:
        severity = "HIGH"
        score = 0.75
    elif a11y:
        # Accessibility without other signals → at least HIGH
        severity = "HIGH"
        score = 0.7
    elif low and not high and not critical:
        severity = "LOW"
        score = 0.2
    else:
        severity = "MEDIUM"
        score = 0.5

    return {
        "severity": severity,
        "score": round(score, 2),
        "is_safety": is_safety,
        "is_accessibility": is_accessibility,
        "matched_cues": matched[:4],
    }


def score_cluster(reason: str, quotes: List[str]) -> Dict[str, Any]:
    """
    Score a complaint cluster's severity using its reason + representative quotes.
    Takes the max severity across the reason and the quotes (worst signal wins).
    """
    samples = [reason or ""] + [q if isinstance(q, str) else (q.get("quote", "") if isinstance(q, dict) else "") for q in (quotes or [])[:5]]
    samples = [s for s in samples if s]

    if not samples:
        return {"severity": "MEDIUM", "score": 0.5, "is_safety": False, "is_accessibility": False, "matched_cues": []}

    best: Optional[Dict[str, Any]] = None
    best_rank = -1
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    for s in samples:
        scored = score_text(s)
        r = rank[scored["severity"]]
        if r > best_rank:
            best = scored
            best_rank = r

    return best or {"severity": "MEDIUM", "score": 0.5, "is_safety": False, "is_accessibility": False, "matched_cues": []}
