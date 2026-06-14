# backend/insight/marketing/angles.py
"""
Marketing Angle Extractor.

The flip side of complaints: find the strongest *praise* themes from real
reviewers, and surface them as quotable, ready-to-use marketing copy.

For a company, this answers:
  "What should our next ad campaign lean on? What's the strongest claim we
  can make with customer-voice backing?"

Each angle includes:
  - the theme (e.g. "unbeatable noise cancellation")
  - mention count + share
  - a verbatim quote that captures the praise (the strongest, most quotable one)
  - a sentiment confidence score
  - tagline-style summary phrasing

"no N/A" rule: only returns angles with:
  - ≥ 5 mentions across reviews
  - ≥ 80% positive language (truly consistent praise, not mixed)
  - a usable verbatim quote (15-180 chars, no profanity, no PII-looking strings)

If fewer than 2 angles qualify, returns [] — no Marketing Angles card.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


# Quotability filters
_PROFANITY = re.compile(r"\b(?:f[u\*]ck|sh[i\*]t|damn|wtf|asshole|bitch)\b", re.IGNORECASE)
_PII_LIKE = re.compile(r"(?:@\w+|\b\d{3}[-\s]?\d{3}[-\s]?\d{4}\b|\b[\w\.]+@[\w\.]+\b|\bhttp[s]?://)")
_ALL_CAPS = re.compile(r"^[A-Z\s!?.,'\"\-]{8,}$")

# Words that signal a strong, positive, quotable sentence
_QUOTABLE_POSITIVE = re.compile(
    r"\b(?:"
    r"best|incredible|amazing|unbelievable|stunning|gorgeous|"
    r"perfect|flawless|exceptional|exceptional|stellar|"
    r"absolutely|hands.down|by far|no contest|unmatched|"
    r"life.?chang(?:ing|er)|game.?chang(?:ing|er)|"
    r"worth every (?:penny|cent|dollar)|"
    r"can'?t recommend (?:enough|highly enough)|"
    r"so (?:happy|glad|impressed)|"
    r"exceeded (?:my\s+)?expectations|"
    r"highly recommend"
    r")\b",
    re.IGNORECASE,
)

# Connectors / qualifiers we want to avoid in headlines (mixed signal)
_HEDGE = re.compile(
    r"\b(?:"
    r"but|however|although|though|except|kinda|sort of|"
    r"would'?ve been|could'?ve been|wish (?:it|they)|if only|"
    r"otherwise|despite|even though"
    r")\b",
    re.IGNORECASE,
)


_STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}


def _is_quotable(text: str) -> bool:
    """A sentence is quotable if it's a pure positive, no profanity, no PII, decent length."""
    if not text or len(text) < 15 or len(text) > 180:
        return False
    if _PROFANITY.search(text):
        return False
    if _PII_LIKE.search(text):
        return False
    if _ALL_CAPS.match(text):
        return False
    if _HEDGE.search(text):
        return False
    if not _QUOTABLE_POSITIVE.search(text):
        return False
    return True


def _extract_quotable_sentences(text: str) -> List[str]:
    """Split into sentences and return only those that pass the quotability test."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return [s.strip() for s in sentences if _is_quotable(s.strip())]


def _tagline_from_theme(theme: str) -> str:
    """Convert a theme key/phrase into a tagline-style summary."""
    t = (theme or "").strip()
    if not t:
        return ""
    # Title case but preserve common short words lowercase
    smalls = {"and", "or", "the", "for", "of", "in", "on", "to", "a", "an", "with"}
    parts = t.split()
    titled = []
    for i, w in enumerate(parts):
        wl = w.lower()
        if i == 0 or wl not in smalls:
            titled.append(wl.capitalize())
        else:
            titled.append(wl)
    return " ".join(titled)


def extract_marketing_angles(
    per_review: List[Dict[str, Any]],
    *,
    min_mentions: int = 5,
    min_positive_ratio: float = 0.80,
    max_angles: int = 5,
) -> List[Dict[str, Any]]:
    """
    Returns ready-to-use marketing angles or [] if no strong theme qualifies.

    Each angle:
      {
        "theme": "Unbeatable Noise Cancellation",
        "mentions": 88,
        "positive_ratio": 0.94,
        "avg_sentiment_stars": 4.7,
        "best_quote": "Best ANC on the market for plane noise — silence on a 12-hour flight.",
        "tagline": "Silence-grade noise cancellation, in customers' words",
        "supporting_quotes": ["...", "..."],
      }
    """
    if not isinstance(per_review, list) or len(per_review) < 10:
        return []

    # Group by theme = canonical_reason (preferred) or top keyphrase
    by_theme: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "stars": [], "quotable_sentences": [], "total": 0, "positive": 0,
    })

    for r in per_review:
        text = (r.get("translated_text") or r.get("original") or "").strip()
        if not text:
            continue

        theme = (r.get("canonical_reason") or "").strip()
        if not theme:
            kps = r.get("keyphrases") or []
            theme = kps[0] if kps else None
        if not theme:
            continue

        bucket = by_theme[theme]
        bucket["total"] += 1

        star = _STAR_TO_NUM.get(r.get("sentiment"), 0)
        if star >= 4:
            bucket["positive"] += 1
        if star > 0:
            bucket["stars"].append(star)

        # Only mine quotable sentences from genuinely positive reviews
        if star >= 4:
            for s in _extract_quotable_sentences(text):
                bucket["quotable_sentences"].append(s)

    angles: List[Dict[str, Any]] = []
    for theme, data in by_theme.items():
        if data["total"] < min_mentions:
            continue
        positive_ratio = data["positive"] / max(1, data["total"])
        if positive_ratio < min_positive_ratio:
            continue
        if not data["quotable_sentences"]:
            continue  # No quotable evidence → don't bother

        # Pick best quote: closest to the "sweet spot" length (40-120 chars), most positive cues
        scored_quotes: List[Tuple[float, str]] = []
        for q in set(data["quotable_sentences"]):
            length_score = 1.0 - abs(80 - len(q)) / 100.0
            positive_hits = len(_QUOTABLE_POSITIVE.findall(q))
            scored_quotes.append((length_score + positive_hits * 0.3, q))
        scored_quotes.sort(key=lambda x: -x[0])
        best_quote = scored_quotes[0][1] if scored_quotes else None
        supporting = [q for _, q in scored_quotes[1:3]]

        if not best_quote:
            continue

        avg_stars = round(sum(data["stars"]) / len(data["stars"]), 2) if data["stars"] else None

        angles.append({
            "theme": _tagline_from_theme(theme),
            "raw_theme": theme,
            "mentions": data["total"],
            "positive_ratio": round(positive_ratio, 2),
            "avg_sentiment_stars": avg_stars,
            "best_quote": best_quote,
            "supporting_quotes": supporting,
        })

    angles.sort(key=lambda a: (-a["mentions"], -a["positive_ratio"]))

    # "no N/A" rule: don't show a card with only 1 angle
    if len(angles) < 2:
        return []

    return angles[:max_angles]
