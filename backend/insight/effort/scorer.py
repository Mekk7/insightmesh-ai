# backend/insight/effort/scorer.py
"""
Customer Effort Score (CES).

Beyond sentiment: how *hard* is it to be a customer? Sentiment captures whether
they like the product. Effort captures whether the journey *around* the product
hurts — setup, learning curve, support, returns, edge cases.

A product can have great sentiment but terrible effort (people love it once
they figure it out — but most never get there). That's a critical failure mode
the dashboard otherwise misses.

Signal categories:
  - support_pain       : "called support twice", "no response from support"
  - setup_friction     : "couldn't get it working", "took 3 tries"
  - learning_curve     : "took weeks to figure out", "confusing", "had to watch tutorials"
  - documentation_gap  : "manual is useless", "no documentation"
  - return_friction    : "returning was a nightmare", "RMA process", "still waiting for refund"
  - account_friction   : "couldn't sign in", "lost my data", "had to reinstall"

Output:
  {
    "score": 0..100,        # 0 = effortless, 100 = miserable journey
    "label": "Effortless" | "Light" | "Moderate" | "Heavy" | "Punishing",
    "signal_breakdown": [
      {"category": "support_pain", "count": 7, "share_pct": 4.2, "sample": "..."},
      ...
    ],
    "narrative": str,
  }

"no N/A" rule: if no effort signals detected (< 3 hits total across categories
on < 1% of reviews), returns None and the dashboard skips the card.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


_SIGNAL_PATTERNS: Dict[str, re.Pattern] = {
    "support_pain": re.compile(
        r"\b(?:"
        r"(?:called|emailed|contacted|messaged)\s+(?:support|service|customer\s+service)\s+(?:\d+|twice|three\s+times|multiple\s+times|several\s+times)|"
        r"(?:still\s+)?(?:waiting|no\s+response)\s+from\s+support|"
        r"support (?:doesn'?t|won'?t|never)\s+(?:reply|respond|answer)|"
        r"(?:terrible|awful|useless|nonexistent)\s+(?:customer\s+)?support|"
        r"(?:rude|unhelpful)\s+(?:support|agent|representative)|"
        r"on hold for (?:\d+|hours|forever)"
        r")\b",
        re.IGNORECASE,
    ),
    "setup_friction": re.compile(
        r"\b(?:"
        r"couldn'?t\s+(?:get\s+it|figure\s+out\s+how\s+to)\s+(?:to\s+)?(?:work|start|set up|connect|pair)|"
        r"took\s+(?:me\s+)?(?:hours|days|forever|\d+\s+(?:hours|days|tries))\s+to\s+(?:set\s*up|install|configure|get\s+working)|"
        r"(?:setup|installation|onboarding)\s+(?:was|is)\s+(?:a\s+)?(?:nightmare|painful|terrible|impossible|confusing)|"
        r"couldn'?t (?:pair|connect|sync|register)|"
        r"after \d+ (?:attempts|tries)"
        r")\b",
        re.IGNORECASE,
    ),
    "learning_curve": re.compile(
        r"\b(?:"
        r"steep\s+learning\s+curve|"
        r"took (?:me\s+)?(?:weeks|months) to (?:figure\s+out|learn|master|get used to)|"
        r"had to watch (?:multiple\s+)?(?:tutorials|videos|youtube)|"
        r"(?:very\s+)?(?:confusing|complicated|unintuitive)\s+(?:to\s+use|interface|controls|menus)|"
        r"not\s+(?:user|beginner)[\s-]friendly|"
        r"so many (?:menus|settings|options) it'?s (?:confusing|overwhelming)"
        r")\b",
        re.IGNORECASE,
    ),
    "documentation_gap": re.compile(
        r"\b(?:"
        r"(?:manual|documentation|instructions|guide)\s+(?:is|are)\s+(?:useless|terrible|missing|unclear|wrong|outdated|nonexistent)|"
        r"no (?:proper\s+)?(?:manual|documentation|instructions|guide|tutorial)|"
        r"(?:can'?t\s+find|where\s+is)\s+(?:the\s+)?(?:manual|documentation|instructions)|"
        r"figured it out (?:from|by) (?:reddit|youtube|google)"
        r")\b",
        re.IGNORECASE,
    ),
    "return_friction": re.compile(
        r"\b(?:"
        r"(?:return|refund|rma|exchange)\s+(?:was|is|process)\s+(?:a\s+)?(?:nightmare|painful|terrible|hassle|joke)|"
        r"still\s+waiting\s+for\s+(?:a\s+)?(?:refund|replacement|response)|"
        r"(?:had\s+to\s+)?(?:fight|argue|negotiate)\s+(?:for|to\s+get)\s+(?:a\s+)?(?:refund|replacement|return)|"
        r"refused\s+(?:to\s+)?(?:refund|exchange|return)|"
        r"restocking fee|return shipping (?:costs|fee)"
        r")\b",
        re.IGNORECASE,
    ),
    "account_friction": re.compile(
        r"\b(?:"
        r"(?:couldn'?t|can'?t)\s+(?:sign|log)\s+(?:in|on)|"
        r"lost\s+(?:my\s+|all\s+)?(?:data|account|files|photos|settings|progress)|"
        r"had\s+to\s+(?:reinstall|factory\s+reset|re.?setup|start over)|"
        r"(?:locked\s+out|kicked\s+out)\s+of\s+(?:my\s+)?account|"
        r"two.?factor (?:nightmare|broke|won't work)"
        r")\b",
        re.IGNORECASE,
    ),
}


_CATEGORY_LABELS = {
    "support_pain": "Support friction",
    "setup_friction": "Setup pain",
    "learning_curve": "Learning curve",
    "documentation_gap": "Docs gap",
    "return_friction": "Returns process",
    "account_friction": "Account issues",
}


def _score_label(score: float) -> str:
    if score < 10:
        return "Effortless"
    if score < 25:
        return "Light"
    if score < 50:
        return "Moderate"
    if score < 75:
        return "Heavy"
    return "Punishing"


def compute_effort_score(
    per_review: List[Dict[str, Any]],
    *,
    min_signals: int = 3,
    min_signal_share: float = 0.01,
) -> Optional[Dict[str, Any]]:
    """
    Scan every review for effort signals. Aggregate into a CES + breakdown.

    Returns None when:
      - no per-review data
      - fewer than `min_signals` total hits across all categories
      - hit rate below `min_signal_share` of the corpus (too sparse to be meaningful)
    """
    if not isinstance(per_review, list) or len(per_review) < 10:
        return None

    n_reviews = len(per_review)

    # Per-category hits with samples
    hits_by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    total_hits = 0
    affected_reviews = set()

    for idx, r in enumerate(per_review):
        text = (r.get("translated_text") or r.get("original") or "").strip()
        if not text:
            continue
        for cat, pat in _SIGNAL_PATTERNS.items():
            m = pat.search(text)
            if m:
                hits_by_cat[cat].append({
                    "review_idx": idx,
                    "sample": text,
                    "match": m.group(0),
                })
                total_hits += 1
                affected_reviews.add(idx)

    if total_hits < min_signals:
        return None

    affected_share = len(affected_reviews) / max(1, n_reviews)
    if affected_share < min_signal_share:
        return None

    # Score: how many DISTINCT reviews report friction, weighted by category diversity
    # 0% affected → 0, 25% affected → ~50, 50% affected → ~80, 100% → 100
    # diversity bonus: hitting multiple categories adds urgency
    diversity = len(hits_by_cat)
    base = min(80.0, affected_share * 160.0)
    diversity_bonus = min(20.0, diversity * 3.5)
    score = round(min(100.0, base + diversity_bonus), 1)

    # Breakdown
    breakdown: List[Dict[str, Any]] = []
    for cat, hits in hits_by_cat.items():
        # Dedup reviews per category for accurate count
        unique_review_idxs = {h["review_idx"] for h in hits}
        sample = next(
            (h["sample"][:240] for h in hits if 30 <= len(h["sample"]) <= 240),
            (hits[0]["sample"][:240] if hits else None)
        )
        breakdown.append({
            "category": cat,
            "label": _CATEGORY_LABELS.get(cat, cat),
            "count": len(unique_review_idxs),
            "share_pct": round(100 * len(unique_review_idxs) / n_reviews, 1),
            "sample": sample,
        })
    breakdown.sort(key=lambda x: -x["count"])

    label = _score_label(score)

    # Narrative
    if score >= 75:
        narrative = f"Customer journey is punishing — {affected_share*100:.0f}% of reviewers report significant friction. Even happy buyers face hurdles."
    elif score >= 50:
        narrative = f"Effort is heavy — {affected_share*100:.0f}% of reviewers mention friction. Top pain: {breakdown[0]['label'].lower()}."
    elif score >= 25:
        narrative = f"Moderate effort. Setup or support hurts a portion of buyers ({affected_share*100:.0f}%)."
    elif score >= 10:
        narrative = "Light effort signal — minor friction here and there but most owners get through it cleanly."
    else:
        narrative = "Effortless journey for most buyers."

    return {
        "score": score,
        "label": label,
        "affected_share_pct": round(affected_share * 100, 1),
        "total_signals": total_hits,
        "categories_hit": diversity,
        "breakdown": breakdown,
        "narrative": narrative,
    }
