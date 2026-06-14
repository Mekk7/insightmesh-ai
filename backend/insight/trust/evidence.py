# backend/insight/trust/evidence.py
"""
Evidence quality assessment — the honesty gate for the whole dashboard.

WHY THIS EXISTS
---------------
The old dashboard would happily emit a confident "TrustScore 54/100 — Risky,
multiple red flags" from 12 YouTube joke comments where the score was only low
because the SAMPLE was thin and full of banter — not because the product is bad.
That is exactly the kind of fake precision we are eliminating.

This module looks at what we actually collected and answers one blunt question:
    "Do we have enough REAL review signal to say anything with confidence?"

It returns a structured verdict the rest of the app uses to decide whether to
show confident numbers, or to honestly say "not enough signal yet."

KEY DISTINCTION
---------------
- n_total      : everything we scraped (includes jokes, spam, one-word hype)
- n_relevant   : comments that are actually about the product as a review/opinion
                 (uses the LLM `is_relevant` signal + light heuristics)

A low score with HIGH evidence quality  → the product genuinely has problems.
A low score with LOW evidence quality   → we just don't know yet. Say so.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

# Comments that are clearly not a usable product review even if is_relevant
# wasn't set (pure emoji, one-word hype, aspirational "when I grow up" chatter).
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_LOW_VALUE_PATTERNS = [
    re.compile(r"^\s*(first|nice|cool|wow|lol|lmao|👍+|🔥+|love it|good|ok+)\s*[!.\s]*$", re.I),
    re.compile(r"\bwhen i('?m| am)\s+(older|big|grow)\b", re.I),     # aspirational, not a review
    re.compile(r"\bmy (uncle|cousin|friend|dad|mom|brother) (has|have|got)\b", re.I),  # anecdote, not a review
]


def _looks_substantive(text: str) -> bool:
    """Heuristic: does this read like an actual opinion about the product?"""
    t = (text or "").strip()
    if len(t) < 12:
        return False
    # Strip emoji to count real words
    words = re.sub(_EMOJI_RE, " ", t).split()
    if len([w for w in words if len(w) > 1]) < 4:
        return False
    for pat in _LOW_VALUE_PATTERNS:
        if pat.search(t):
            return False
    return True


def _is_relevant_review(r: Dict[str, Any]) -> bool:
    """
    A per-review entry counts as real review signal when:
      - the LLM did not explicitly mark it irrelevant, AND
      - it has some substance (not pure emoji / one-word hype / pure anecdote).
    `is_relevant` defaults True when no LLM understanding ran, so we still apply
    the heuristic as a backstop.
    """
    if r.get("is_relevant") is False:
        return False
    text = r.get("translated_text") or r.get("original") or ""
    return _looks_substantive(text)


def assess_evidence(
    per_review: List[Dict[str, Any]],
    *,
    decision_health: Optional[Dict[str, Any]] = None,
    language_count: int = 1,
) -> Dict[str, Any]:
    """
    Returns an honest read of how much we can trust any aggregate conclusion.

    {
      "n_total": 12,
      "n_relevant": 3,
      "relevance_ratio": 0.25,
      "quality": 0.18,              # 0..1 overall evidence quality
      "level": "none|thin|moderate|solid",
      "insufficient": True,         # if True, the UI must NOT show confident scores
      "headline": "Only 3 of 12 comments are real reviews",
      "reasons": ["Very small sample", "Most comments are off-topic chatter"],
      "irrelevant_examples": ["Bro where is the gasoline?", ...],
    }
    """
    per_review = per_review or []
    n_total = len(per_review)

    relevant: List[Dict[str, Any]] = []
    irrelevant: List[Dict[str, Any]] = []
    for r in per_review:
        (relevant if _is_relevant_review(r) else irrelevant).append(r)

    n_relevant = len(relevant)
    relevance_ratio = (n_relevant / n_total) if n_total else 0.0

    # ---- Volume component (0..1): how many REAL reviews do we have? ----
    # 0 → 0.0, 8 → ~0.45, 30 → ~0.8, 100+ → ~1.0  (logarithmic)
    if n_relevant <= 0:
        volume = 0.0
    else:
        volume = min(1.0, math.log10(n_relevant + 1) / math.log10(101))

    # ---- Signal richness: did buyers state actionable intent? ----
    dh = decision_health or {}
    intent_signal = 0.0
    for k in ("recommend_pct", "return_pct", "avoid_pct", "buy_pct"):
        if float(dh.get(k, 0) or 0) > 0:
            intent_signal = 1.0
            break

    # ---- Coverage: more languages = a little more confidence ----
    coverage = min(1.0, 0.4 + 0.2 * max(0, int(language_count or 1) - 1))

    # ---- Blend into one quality number (0..1) ----
    # Volume dominates; relevance ratio is a strong multiplier (junk-heavy data
    # is penalized hard); intent + coverage are small bonuses.
    quality = (
        0.60 * volume
        + 0.10 * intent_signal
        + 0.10 * coverage
    )
    # Relevance ratio acts as a gate: if 80% is junk, slash the quality.
    quality *= (0.35 + 0.65 * relevance_ratio)
    quality = round(max(0.0, min(1.0, quality)), 3)

    # ---- Level + insufficiency ----
    if n_relevant == 0:
        level = "none"
    elif n_relevant < 8:
        level = "thin"
    elif n_relevant < 40:
        level = "moderate"
    else:
        level = "solid"

    # We refuse to show confident verdicts when the real-review base is too small
    # or the data is mostly noise.
    insufficient = (n_relevant < 8) or (relevance_ratio < 0.40 and n_relevant < 20)

    # ---- Human-readable reasons ----
    reasons: List[str] = []
    if n_relevant == 0:
        reasons.append("No usable reviews found — only off-topic chatter")
    elif n_relevant < 8:
        reasons.append(f"Very small sample — only {n_relevant} real review{'s' if n_relevant != 1 else ''}")
    elif n_relevant < 40:
        reasons.append(f"Moderate sample ({n_relevant} real reviews) — directional")
    if n_total and relevance_ratio < 0.5:
        reasons.append(f"{n_total - n_relevant} of {n_total} comments are jokes, spam, or off-topic")
    if intent_signal == 0.0:
        reasons.append("No reviewer stated whether they'd recommend, buy, or return")

    if n_relevant == 0:
        headline = "Not enough real reviews to analyze"
    elif insufficient:
        headline = f"Only {n_relevant} of {n_total} comments are real reviews — treat as a first impression, not a verdict"
    elif level == "moderate":
        headline = f"{n_relevant} real reviews — a directional read, not the final word"
    else:
        headline = f"{n_relevant} real reviews — a solid evidence base"

    irrelevant_examples = [
        (r.get("original") or "")[:120]
        for r in irrelevant[:4]
        if (r.get("original") or "").strip()
    ]

    return {
        "n_total": n_total,
        "n_relevant": n_relevant,
        "relevance_ratio": round(relevance_ratio, 3),
        "quality": quality,
        "level": level,
        "insufficient": bool(insufficient),
        "headline": headline,
        "reasons": reasons,
        "irrelevant_examples": irrelevant_examples,
    }
