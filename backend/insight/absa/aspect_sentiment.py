# backend/insight/absa/aspect_sentiment.py
"""
Aspect-Based Sentiment Analysis (ABSA).

Goes beyond "this product is 4.1 stars overall" and answers:
  - Battery:    3.2★ (mostly negative, 67 mentions)
  - Software:   3.0★ (mixed, 41 mentions)
  - Build:      4.4★ (mostly positive, 28 mentions)
  - Price:      2.8★ (mostly negative, 19 mentions)

How it works (free, no paid APIs):

  1. Aspect taxonomy — a universal product-aspect dictionary keyed by category
     (universal aspects like "price", "quality", "support", plus category-specific
     aspects auto-selected from a per-domain map).
  2. Aspect mention detection — for each review, find which aspects are mentioned
     using lexical patterns + variant forms (e.g., "battery", "battery life",
     "charging time" → all map to the "battery" aspect).
  3. Sentence-level sentiment — split the review into clauses around connectors
     ("but", "however", ".") and score each aspect from the clause it appears in,
     not the whole review (handles "the battery is awful but the screen is great").
  4. Aggregation — for each aspect, compute mention count, average sentiment,
     positive/negative breakdown, a representative quote.
  5. Optional LLM refinement — if Ollama is available, ask it to suggest 2-3
     additional aspect labels it sees in the comments that our dictionary missed.

This module returns a list of aspect summaries, ranked by mention count.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


# --- Aspect taxonomy --------------------------------------------------------
# Each aspect maps to a list of regex-friendly keyword variants. Keep lowercase.
# `aliases` are matched as word boundaries; `phrases` are matched as substrings
# (useful for multi-word aspects like "battery life").

UNIVERSAL_ASPECTS: Dict[str, Dict[str, List[str]]] = {
    "price":         {"aliases": ["price", "cost", "expensive", "cheap", "pricing", "value", "worth", "overpriced", "affordable"], "phrases": ["price tag", "money's worth", "for the price"]},
    "quality":       {"aliases": ["quality", "build", "construction", "materials", "feel"], "phrases": ["build quality", "fit and finish"]},
    "performance":   {"aliases": ["performance", "speed", "fast", "slow", "lag", "sluggish", "responsive", "snappy"], "phrases": ["response time"]},
    "design":        {"aliases": ["design", "looks", "aesthetic", "appearance", "style", "beautiful", "ugly"], "phrases": ["looks great", "looks bad"]},
    "reliability":   {"aliases": ["reliable", "reliability", "consistent", "stable", "buggy", "broken", "crashes"], "phrases": ["keeps crashing"]},
    "support":       {"aliases": ["support", "service", "warranty", "help", "customer-service"], "phrases": ["customer service", "tech support", "service center"]},
    "usability":     {"aliases": ["intuitive", "confusing", "usable", "ux", "ui", "interface", "ergonomic"], "phrases": ["ease of use", "user-friendly", "user friendly", "hard to use"]},
    "size_weight":   {"aliases": ["heavy", "light", "lightweight", "bulky", "compact", "weight", "portable", "size"], "phrases": ["too heavy", "too big", "too small"]},
    "documentation": {"aliases": ["documentation", "manual", "instructions", "tutorial", "guide", "faq"], "phrases": ["the manual", "user guide"]},
    "delivery":      {"aliases": ["shipping", "delivery", "packaging", "arrived"], "phrases": ["arrived damaged", "took forever"]},
}

# Category-specific aspects, layered on top of universals.
# Detected by checking which keywords appear in the product query or in the corpus.
DOMAIN_ASPECTS: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "auto_ev": {
        "battery":     {"aliases": ["battery", "range", "charging", "charger", "kwh", "miles", "mileage"], "phrases": ["battery life", "battery range", "charging speed", "supercharger"]},
        "autopilot":   {"aliases": ["autopilot", "fsd", "self-driving", "self_driving", "autonomous"], "phrases": ["self driving", "phantom braking", "lane keep"]},
        "acceleration":{"aliases": ["acceleration", "torque", "fast", "quick", "0-60"], "phrases": ["off the line"]},
        "interior":    {"aliases": ["interior", "seats", "dashboard", "cabin", "trim"], "phrases": ["leg room", "seat comfort"]},
        "software":    {"aliases": ["software", "ota", "update", "firmware", "infotainment", "screen"], "phrases": ["over the air"]},
        "service":     {"aliases": ["service-center", "tesla-service"], "phrases": ["service center", "service appointment", "wait time"]},
    },
    "audio": {
        "sound":       {"aliases": ["sound", "audio", "bass", "treble", "mids", "highs"], "phrases": ["sound quality", "sound stage", "bass response"]},
        "noise_cancel":{"aliases": ["anc", "noise-cancelling", "noise-canceling"], "phrases": ["noise cancellation", "noise canceling", "noise cancelling", "block outside"]},
        "comfort":     {"aliases": ["comfortable", "comfort", "fit", "tight", "loose"], "phrases": ["ear cups", "ear pads", "head band", "long sessions"]},
        "battery":     {"aliases": ["battery", "hours", "charge"], "phrases": ["battery life", "battery lasts"]},
        "microphone":  {"aliases": ["mic", "microphone", "calls"], "phrases": ["voice quality", "call quality"]},
        "app":         {"aliases": ["app", "application", "bluetooth", "pairing"], "phrases": ["companion app", "the app"]},
    },
    "xr_vr": {
        "weight":      {"aliases": ["weight", "heavy", "heavier"], "phrases": ["neck fatigue", "front heavy"]},
        "display":     {"aliases": ["display", "screen", "resolution", "passthrough", "pixels"], "phrases": ["screen door", "micro oled"]},
        "comfort":     {"aliases": ["comfort", "comfortable", "pressure"], "phrases": ["face pressure", "long sessions"]},
        "battery":     {"aliases": ["battery", "tethered", "cord"], "phrases": ["battery life", "battery pack"]},
        "content":     {"aliases": ["apps", "content", "games", "experiences", "library"], "phrases": ["app library", "killer app"]},
        "tracking":    {"aliases": ["tracking", "eye-tracking", "hand-tracking"], "phrases": ["eye tracking", "hand tracking", "pinch gesture"]},
    },
    "phone_laptop": {
        "battery":     {"aliases": ["battery", "charge", "charging"], "phrases": ["battery life", "battery drain"]},
        "display":     {"aliases": ["screen", "display", "brightness", "oled", "lcd"], "phrases": ["refresh rate"]},
        "camera":      {"aliases": ["camera", "photos", "video", "lens", "zoom"], "phrases": ["low light", "night mode"]},
        "speakers":    {"aliases": ["speakers", "audio", "sound", "loud"], "phrases": ["speaker quality"]},
        "keyboard":    {"aliases": ["keyboard", "keys", "typing", "trackpad", "touchpad"], "phrases": ["key travel"]},
        "software":    {"aliases": ["software", "os", "update", "bloatware"], "phrases": ["operating system"]},
    },
}

# Domain detection: maps keywords in product query → domain key.
# NOTE: every entry is matched as a WHOLE WORD (see `_detect_domain`), never a raw
# substring. The old code did `"ev" in haystack`, and since the corpus text it
# searched contains words like "review", "every", "never", "even", that matched
# almost any product → everything was tagged "auto_ev". Word-boundary matching
# plus the Product-Intelligence category path below fix that.
DOMAIN_HINTS: List[Tuple[List[str], str]] = [
    (["tesla", "model y", "model 3", "model s", "model x", "ev", "evs", "electric car", "electric suv", "rivian", "lucid"], "auto_ev"),
    (["headphone", "headphones", "earbud", "earbuds", "airpods", "wh-1000", "xm5", "xm6", "sony", "bose", "sennheiser"], "audio"),
    (["vision pro", "quest", "vr", "xr", "ar", "headset", "meta quest"], "xr_vr"),
    (["iphone", "pixel", "galaxy", "samsung", "macbook", "thinkpad", "laptop", "phone"], "phone_laptop"),
]

# Product-Intelligence category → domain. The category comes from the LLM-generated
# ProductIntelligence (e.g. "over-ear headphones", "gaming console", "electric SUV").
# This is the authoritative signal: it's grounded in what the product actually IS,
# not a fragile keyword scan of review text. Each entry is matched as a substring of
# the (lowercased) category phrase — categories are short, curated, and safe to scan.
CATEGORY_DOMAIN_HINTS: List[Tuple[List[str], str]] = [
    (["headphone", "earbud", "earphone", "headset audio", "speaker", "soundbar", "audio"], "audio"),
    (["vr", "xr", "ar headset", "mixed reality", "virtual reality", "augmented reality", "vision pro"], "xr_vr"),
    (["electric car", "electric suv", "electric vehicle", "ev", " car", "sedan", "truck", "automobile", "vehicle"], "auto_ev"),
    (["phone", "smartphone", "laptop", "notebook", "tablet", "computer"], "phone_laptop"),
]


def _domain_from_category(category: Optional[str]) -> Optional[str]:
    """Map a ProductIntelligence category string to an ABSA domain key.

    Returns None when the category doesn't match a known domain (caller then
    falls back to keyword detection, or to universal aspects only)."""
    if not category:
        return None
    cat = category.lower()
    # Guard against "headset" matching xr_vr when it's clearly audio
    if "headset" in cat and any(k in cat for k in ("audio", "gaming headset", "headphone")):
        return "audio"
    for keywords, domain in CATEGORY_DOMAIN_HINTS:
        if any(k in cat for k in keywords):
            return domain
    return None

NEG_TOKENS = re.compile(r"\b(not|no|never|don'?t|doesn'?t|isn'?t|aren'?t|wasn'?t|won'?t|can'?t|cannot|hardly|barely)\b", re.I)
POS_LEXICON = {"great", "amazing", "love", "loved", "perfect", "excellent", "best", "fantastic", "incredible", "good", "solid", "awesome", "beautiful", "smooth", "fast", "reliable", "comfortable", "easy", "nice", "premium", "stellar"}
NEG_LEXICON = {"bad", "terrible", "awful", "horrible", "worst", "broken", "buggy", "slow", "lag", "junk", "trash", "useless", "disappointed", "disappointing", "hate", "annoying", "frustrating", "uncomfortable", "expensive", "overpriced", "cheap", "flimsy", "garbage", "scam", "wait", "delay"}

# Star → numeric mapping used as fallback when no lexicon hits
_STAR_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}


def _detect_domain(query: Optional[str], corpus_text: str) -> Optional[str]:
    haystack = ((query or "") + " " + corpus_text[:4000]).lower()
    for keywords, domain in DOMAIN_HINTS:
        for k in keywords:
            # Whole-word / whole-phrase match only. The old `k in haystack`
            # substring test made short keys like "ev" match "review"/"every",
            # tagging every product as auto_ev.
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", haystack):
                return domain
    return None


def _build_aspect_dict(domain: Optional[str]) -> Dict[str, Dict[str, List[str]]]:
    out = dict(UNIVERSAL_ASPECTS)
    if domain and domain in DOMAIN_ASPECTS:
        for k, v in DOMAIN_ASPECTS[domain].items():
            out[k] = v
    return out


def _split_clauses(text: str) -> List[str]:
    """Break a review into clauses for clause-level aspect-sentiment scoring."""
    if not text:
        return []
    # Cut on sentence terminators and strong connectors
    parts = re.split(r"(?:[.!?]+|\bbut\b|\bhowever\b|\balthough\b|\byet\b|\bthough\b)\s+", text, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _aspect_hits_in_clause(clause: str, aspect_dict: Dict[str, Dict[str, List[str]]]) -> List[str]:
    """Return aspect keys mentioned in this clause."""
    cl = clause.lower()
    hits: List[str] = []
    for aspect, spec in aspect_dict.items():
        # phrases first (multi-word, higher specificity)
        if any(p in cl for p in spec.get("phrases", [])):
            hits.append(aspect)
            continue
        # then aliases as word boundaries
        for alias in spec.get("aliases", []):
            if re.search(rf"\b{re.escape(alias)}\b", cl):
                hits.append(aspect)
                break
    return hits


def _clause_sentiment_polarity(clause: str, fallback_star: int = 3) -> float:
    """
    Quick lexical polarity score for a single clause: returns -1..+1.
    Pos lexicon hits push positive; negation flips. Fallback to overall review stars.
    """
    cl = clause.lower()
    pos_hits = sum(1 for w in POS_LEXICON if re.search(rf"\b{w}\b", cl))
    neg_hits = sum(1 for w in NEG_LEXICON if re.search(rf"\b{w}\b", cl))
    # Negation handling: rough — if a negation appears near a positive word, flip one
    if NEG_TOKENS.search(cl):
        if pos_hits and not neg_hits:
            pos_hits -= 1
            neg_hits += 1
    if pos_hits == 0 and neg_hits == 0:
        # Fall back to the review's overall star rating
        return ({1: -1.0, 2: -0.5, 3: 0.0, 4: 0.5, 5: 1.0}).get(int(fallback_star), 0.0)
    total = pos_hits + neg_hits
    return (pos_hits - neg_hits) / max(1, total)


def analyze_aspects(
    per_review: List[Dict[str, Any]],
    *,
    query: Optional[str] = None,
    product_category: Optional[str] = None,
    max_aspects: int = 10,
    min_mentions: int = 2,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "domain":   "auto_ev" | "audio" | ... | None,
        "aspects": [
          {
            "aspect": "battery",
            "mentions": 67,
            "avg_polarity": -0.42,            # -1..+1
            "avg_sentiment_stars": 2.8,        # converted to 1..5 scale
            "pct_positive": 23,
            "pct_negative": 61,
            "pct_neutral": 16,
            "sample_positive": "Battery lasts forever on a long drive...",
            "sample_negative": "Range drops 30% on highway speeds...",
          }, ...
        ]
      }
    """
    if not isinstance(per_review, list) or not per_review:
        return {"domain": None, "aspects": []}

    corpus_text = " ".join((r.get("original") or "")[:300] for r in per_review[:80])
    # Prefer the Product-Intelligence category (authoritative — it's what the
    # product IS) over scanning review text for keywords. Fall back to keyword
    # detection only when no category was supplied or it didn't map to a domain.
    domain = _domain_from_category(product_category) or _detect_domain(query, corpus_text)
    aspect_dict = _build_aspect_dict(domain)

    # aspect → list of (polarity, original_text, star_int, review_index).
    # We carry the review index so the same review's multiple clauses for one
    # aspect can be reconciled into a single net sentiment when picking samples.
    buckets: Dict[str, List[Tuple[float, str, int, int]]] = defaultdict(list)

    for r_idx, r in enumerate(per_review):
        text = (r.get("translated_text") or r.get("original") or "").strip()
        if not text:
            continue
        star = _STAR_NUM.get(r.get("sentiment"), 3)
        clauses = _split_clauses(text)
        if not clauses:
            clauses = [text]
        for clause in clauses:
            hits = _aspect_hits_in_clause(clause, aspect_dict)
            if not hits:
                continue
            pol = _clause_sentiment_polarity(clause, fallback_star=star)
            for aspect in hits:
                buckets[aspect].append((pol, text, star, r_idx))

    aspects_out: List[Dict[str, Any]] = []
    for aspect, hits in buckets.items():
        if len(hits) < min_mentions:
            continue
        polarities = [p for p, _, _, _ in hits]
        avg_pol = sum(polarities) / len(polarities)
        pos = sum(1 for p in polarities if p > 0.2)
        neg = sum(1 for p in polarities if p < -0.2)
        neu = len(polarities) - pos - neg

        # ---- Sample selection (one review can only land on ONE side) ----
        # A review that says "sound is great but bass is muddy" produces both a
        # positive and a negative clause hit for the same aspect. Picking the
        # highest- and lowest-polarity CLAUSE independently could surface the
        # SAME review in both the +praise and -complaint boxes. So we first
        # collapse each review's clause polarities for this aspect into its NET
        # polarity, then draw the positive sample from a net-positive review and
        # the negative sample from a (necessarily different) net-negative review.
        review_pols: Dict[int, List[float]] = defaultdict(list)
        review_text: Dict[int, str] = {}
        for p, t, _, ri in hits:
            review_pols[ri].append(p)
            review_text[ri] = t
        net_by_review = {ri: sum(ps) / len(ps) for ri, ps in review_pols.items()}
        pos_reviews = sorted((ri for ri, p in net_by_review.items() if p > 0.2),
                             key=lambda ri: -net_by_review[ri])
        neg_reviews = sorted((ri for ri, p in net_by_review.items() if p < -0.2),
                             key=lambda ri: net_by_review[ri])
        # pos_reviews and neg_reviews are disjoint by construction (a review's
        # net polarity can't be both > 0.2 and < -0.2), so the two samples are
        # guaranteed to be different reviews.
        sample_pos = review_text[pos_reviews[0]] if pos_reviews else None
        sample_neg = review_text[neg_reviews[0]] if neg_reviews else None
        # Convert polarity → 1..5 stars (linear)
        avg_stars = round(3 + 2 * avg_pol, 2)
        aspects_out.append({
            "aspect": aspect,
            "mentions": len(hits),
            "avg_polarity": round(avg_pol, 2),
            "avg_sentiment_stars": avg_stars,
            "pct_positive": round(100 * pos / len(polarities)),
            "pct_negative": round(100 * neg / len(polarities)),
            "pct_neutral": round(100 * neu / len(polarities)),
            "sample_positive": (sample_pos or "")[:240],
            "sample_negative": (sample_neg or "")[:240],
        })

    aspects_out.sort(key=lambda x: -x["mentions"])
    return {"domain": domain, "aspects": aspects_out[:max_aspects]}
