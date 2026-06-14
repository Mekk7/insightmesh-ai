# backend/insight/personas/segmenter.py
"""
Reviewer persona segmentation.

What this answers for consumers:
  "Tech enthusiasts love this product. Mainstream users struggle with the
  learning curve. Long-term owners stand by it."

What this answers for companies:
  "Your dissatisfied segment skews mainstream and price-sensitive. Tech-savvy
  buyers love what you built — the gap is in onboarding."

Method (no ML required, transparent rules):

  For each review, score against 5 archetypes based on linguistic + behavioral
  features:
    - Tech enthusiast: technical jargon, spec mentions, version numbers,
      mentions of firmware/OTA/SDK/calibration
    - Long-term owner: explicit ownership duration ("had mine for 8 months"),
      reflects on durability/reliability
    - Professional / power user: work use cases, productivity, integration,
      workflow mentions
    - Critic / comparison shopper: explicit comparisons to other products,
      ranks pros/cons, mentions alternatives
    - Mainstream / casual: short reviews, simple language, focus on price /
      basic use, no technical depth

  Each review gets assigned to its dominant persona (or "mainstream" by default
  for the bulk of reviews that don't trigger any specific signals).

  Aggregator returns: only personas with >= MIN_SHARE of reviews. If only one
  persona dominates the whole corpus (no real differentiation), returns []
  so the dashboard doesn't bother rendering a meaningless card.

The "no N/A" rule: the aggregator returns [] when:
  - fewer than 8 reviews total (not enough signal)
  - only one persona makes the threshold (no useful comparison)
  - everyone is "mainstream" (no real differentiation)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


# --- Feature detection patterns ---------------------------------------------

_TECH_CUES = re.compile(
    r"\b(?:"
    r"firmware|ota|sdk|api|kernel|driver|spec(?:s)?|specification|"
    r"benchmark|fps|hz|ghz|mhz|kbps|mbps|gbps|nm process|"
    r"calibrat(?:ed|ion)|configur(?:ed|ation)|throughput|latency|"
    r"resolution|refresh rate|bitrate|codec|"
    r"v\d+\.\d+|version \d|gen \d+|generation \d|"
    r"voltage|amperage|watt(?:s|age)|kwh|mah|"
    r"raw format|debug|root(?:ed)?|jailbreak"
    r")\b",
    re.IGNORECASE,
)

_OWNERSHIP_CUES = re.compile(
    r"\b(?:"
    r"(?:had|owned|using|been with)\s+(?:mine|it|this|one)?\s*for\s+(?:\d+|a\s+(?:few|couple)|several)\s+(?:days?|weeks?|months?|years?)|"
    r"(?:after|in)\s+(?:\d+|a\s+few|several)\s+(?:weeks?|months?|years?)\s+of\s+(?:use|ownership)|"
    r"long.?term (?:owner|review|use)|"
    r"daily driver|my main|every day for"
    r")\b",
    re.IGNORECASE,
)

_PROFESSIONAL_CUES = re.compile(
    r"\b(?:"
    r"professional(?:ly)?|for work|work(?:flow|ing)|productivity|"
    r"as a (?:photographer|developer|engineer|designer|musician|producer|"
    r"creator|writer|architect|consultant|surgeon|teacher|nurse|pilot)|"
    r"my (?:business|practice|office|studio|clients)|"
    r"client (?:meetings?|calls?|sessions?)|"
    r"production (?:environment|use|grade)|"
    r"render(?:ing)?|export(?:ing)?|deliverables?"
    r")\b",
    re.IGNORECASE,
)

_CRITIC_CUES = re.compile(
    r"\b(?:"
    r"compared (?:to|with)|versus|vs\.?|"
    r"better than|worse than|preferred|alternative|"
    r"pros and cons|pros:|cons:|"
    r"switched from|coming from|"
    r"in (?:my|the) opinion|honestly|to be (?:fair|honest)|"
    r"giving (?:it )?\d+(?:\s*\/\s*\d+|\s*out of)|"
    r"after testing|side.by.side"
    r")\b",
    re.IGNORECASE,
)

# Words/patterns that suggest casual / mainstream
_CASUAL_LENGTH_THRESHOLD = 220  # very short reviews
_TECHNICAL_TERM_MIN = 1


PERSONA_LABELS = {
    "tech_enthusiast": {
        "label": "Tech enthusiast",
        "desc": "Reviewers who dig into specs, versions, and technical details",
        "icon": "▲",
        "tone": "indigo",
    },
    "long_term_owner": {
        "label": "Long-term owner",
        "desc": "Reviewers who've used the product for months or years",
        "icon": "◐",
        "tone": "amber",
    },
    "professional": {
        "label": "Professional / power user",
        "desc": "Using the product for work or production",
        "icon": "◆",
        "tone": "blue",
    },
    "critic": {
        "label": "Comparison shopper",
        "desc": "Weighs pros and cons against alternatives",
        "icon": "≡",
        "tone": "violet",
    },
    "mainstream": {
        "label": "Mainstream user",
        "desc": "Everyday buyers focused on value and basic use",
        "icon": "●",
        "tone": "zinc",
    },
}

_STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}


def _classify_review(text: str) -> str:
    """Assign one persona to a single review based on dominant feature signals."""
    if not text:
        return "mainstream"
    t = text.lower()

    tech_hits = len(_TECH_CUES.findall(t))
    own_hits = 1 if _OWNERSHIP_CUES.search(t) else 0
    prof_hits = 1 if _PROFESSIONAL_CUES.search(t) else 0
    critic_hits = len(_CRITIC_CUES.findall(t))

    # Priority: explicit ownership beats technical jargon, professional beats critic
    if own_hits and len(text) > 80:
        return "long_term_owner"
    if prof_hits:
        return "professional"
    if tech_hits >= 2 or (tech_hits == 1 and len(text) > 120):
        return "tech_enthusiast"
    if critic_hits >= 2:
        return "critic"
    return "mainstream"


def segment_reviewers(
    per_review: List[Dict[str, Any]],
    *,
    min_reviews: int = 8,
    min_persona_share: float = 0.10,
    min_distinct_personas: int = 2,
) -> List[Dict[str, Any]]:
    """
    Returns a list of persona summaries — or empty list if no useful signal.

    Each persona block:
      {
        "key": "tech_enthusiast",
        "label": "Tech enthusiast",
        "desc": "...",
        "icon": "▲",
        "tone": "indigo",
        "count": 34,
        "pct": 18.5,
        "avg_sentiment_stars": 4.3,
        "verdict": "loved" | "mixed" | "struggling",
        "top_concern": str | None,
        "sample_quote": str,
        "buyer_intent_mix": {"BUY": 5, "RECOMMEND": 12, "RETURN": 1, ...},
      }

    Returns [] if:
      - fewer than `min_reviews` total reviews
      - fewer than `min_distinct_personas` personas reach `min_persona_share`
      - the only personas detected are everyone in "mainstream" (no real signal)
    """
    if not isinstance(per_review, list) or len(per_review) < min_reviews:
        return []

    # Bucket reviews by persona
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in per_review:
        text = (r.get("translated_text") or r.get("original") or "").strip()
        persona = _classify_review(text)
        buckets[persona].append(r)

    total = sum(len(v) for v in buckets.values())
    if total == 0:
        return []

    # Threshold: only keep personas above the share threshold
    qualifying = {k: v for k, v in buckets.items() if (len(v) / total) >= min_persona_share}
    if len(qualifying) < min_distinct_personas:
        return []

    # If only mainstream qualifies, that's no information — bail out
    if list(qualifying.keys()) == ["mainstream"]:
        return []

    out: List[Dict[str, Any]] = []
    for persona_key, reviews in qualifying.items():
        meta = PERSONA_LABELS.get(persona_key, PERSONA_LABELS["mainstream"])

        # Average sentiment
        stars = [_STAR_TO_NUM.get(r.get("sentiment"), 0) for r in reviews]
        stars = [s for s in stars if s > 0]
        avg_stars = round(sum(stars) / len(stars), 2) if stars else None

        # Verdict tag
        if avg_stars is None:
            verdict = "mixed"
        elif avg_stars >= 4.2:
            verdict = "loved"
        elif avg_stars <= 2.8:
            verdict = "struggling"
        else:
            verdict = "mixed"

        # Top concern within this persona (most common canonical_reason among complaints)
        concerns: Counter = Counter()
        for r in reviews:
            if r.get("review_category") == "Complaint" and r.get("canonical_reason"):
                concerns[r["canonical_reason"]] += 1
        top_concern = concerns.most_common(1)[0][0] if concerns else None

        # Sample quote — pick highest quality
        ranked = sorted(reviews, key=lambda r: -(r.get("quality") or 0))
        sample_quote = None
        for r in ranked:
            text = (r.get("original") or "").strip()
            if 30 <= len(text) <= 240:
                sample_quote = text
                break
        if not sample_quote and ranked:
            sample_quote = (ranked[0].get("original") or "")[:220]

        # Buyer intent mix within this persona
        intent_mix: Counter = Counter()
        for r in reviews:
            intent = r.get("buyer_intent")
            if intent and intent != "UNKNOWN":
                intent_mix[intent] += 1

        out.append({
            "key": persona_key,
            "label": meta["label"],
            "desc": meta["desc"],
            "icon": meta["icon"],
            "tone": meta["tone"],
            "count": len(reviews),
            "pct": round(100 * len(reviews) / total, 1),
            "avg_sentiment_stars": avg_stars,
            "verdict": verdict,
            "top_concern": top_concern,
            "sample_quote": sample_quote,
            "buyer_intent_mix": dict(intent_mix),
        })

    # Sort by count desc
    out.sort(key=lambda x: -x["count"])
    return out
