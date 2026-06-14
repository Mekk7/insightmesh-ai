# backend/utils/quality.py
"""
Comment quality scoring — complements `filtering.py`.

filtering.py answers a binary question: "is this comment worth keeping at all?"
This module answers a graded one: "given that it survived, how *good* is it?"

The score (0.0 to 1.0) is used downstream to:
  - pick the best samples for "Voice of the customer" cards
  - weight mood/sentiment in aggregate (optional)
  - flag suspiciously low-info comments in debug output

Signals contributing to the score:
  + length sweet spot (penalize both very short and very long)
  + lexical diversity (unique_words / total_words)
  + presence of concrete nouns/numbers (signals specificity)
  + sentiment confidence (delegated to caller)
  - shallow reaction phrases ("this is amazing", "totally agree")
  - reply-style mentions (@user, "as someone said above")
  - all-caps shouting
"""
from __future__ import annotations

import re
from typing import Optional


_SHALLOW_REACTIONS = {
    "this is amazing", "this is great", "this is good", "this is bad",
    "totally agree", "completely agree", "i agree", "well said",
    "exactly this", "this so much", "this", "facts", "true that",
    "same here", "underrated comment", "this comment", "best comment",
    "wholesome", "based", "cringe", "lmao this",
}

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|mph|km|miles?|hrs?|hours?|days?|times?|years?|months?|gb|tb|hz|°|degrees?)?\b", re.I)
_AT_MENTION_RE = re.compile(r"@[A-Za-z0-9_]+")
_REPLY_PHRASES = re.compile(r"\b(as\s+(?:someone|others)?\s+(?:said|mentioned)|like\s+the\s+person\s+(?:above|below))\b", re.I)
_PUNCT_OR_SPACE = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def _tokens(text: str) -> list[str]:
    t = _PUNCT_OR_SPACE.sub(" ", (text or "").lower())
    t = _WHITESPACE.sub(" ", t).strip()
    return [w for w in t.split() if w]


def _length_score(n_chars: int) -> float:
    """Sweet spot is roughly 40..220 chars. Short = thin signal, very long = often a wall of text."""
    if n_chars <= 0:
        return 0.0
    if n_chars < 20:
        return n_chars / 20.0 * 0.5
    if n_chars <= 220:
        return 1.0
    if n_chars <= 600:
        return 0.85
    if n_chars <= 1200:
        return 0.6
    return 0.4


def _diversity_score(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    if len(tokens) < 3:
        return 0.3
    unique = len(set(tokens))
    ratio = unique / len(tokens)
    return min(1.0, ratio * 1.25)


def _specificity_score(text: str) -> float:
    """+ if comment cites numbers/units, brand-like proper nouns, or product features."""
    score = 0.0
    if _NUMBER_RE.search(text):
        score += 0.4
    proper_nouns = len(re.findall(r"\b[A-Z][a-z]{2,}\b", text))
    if proper_nouns >= 1:
        score += min(0.3, proper_nouns * 0.1)
    if any(kw in text.lower() for kw in ("because", "after", "before", "compared to", "instead of", "wish", "should", "could", "would")):
        score += 0.3
    return min(1.0, score)


def _shouting_penalty(text: str) -> float:
    if len(text) < 12:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_ratio > 0.7:
        return 0.3
    if upper_ratio > 0.5:
        return 0.15
    return 0.0


def _shallow_penalty(text: str) -> float:
    t_norm = re.sub(r"\s+", " ", (text or "").lower().strip(" .!?,"))
    if t_norm in _SHALLOW_REACTIONS:
        return 0.5
    for phrase in _SHALLOW_REACTIONS:
        if t_norm.startswith(phrase + " ") or t_norm.endswith(" " + phrase):
            return 0.25
    return 0.0


def _reply_noise_penalty(text: str) -> float:
    mentions = len(_AT_MENTION_RE.findall(text or ""))
    if mentions >= 2:
        return 0.3
    if mentions == 1 and len((text or "").split()) <= 6:
        return 0.2
    if _REPLY_PHRASES.search(text or ""):
        return 0.15
    return 0.0


def quality_score(text: str, *, sentiment_confidence: Optional[float] = None) -> float:
    """
    Return a quality score in [0, 1].

    sentiment_confidence (optional, 0..1): if provided, blended in as a small bonus
    so 'I love it' (high-confidence positive but shallow) still scores below
    'The battery dies after 4 hours of mixed driving' (high confidence + specific).
    """
    if not text or not text.strip():
        return 0.0

    n_chars = len(text)
    toks = _tokens(text)

    base = (
        _length_score(n_chars) * 0.25
        + _diversity_score(toks) * 0.25
        + _specificity_score(text) * 0.30
    )
    base -= _shouting_penalty(text)
    base -= _shallow_penalty(text)
    base -= _reply_noise_penalty(text)

    if sentiment_confidence is not None:
        try:
            base += float(sentiment_confidence) * 0.20
        except Exception:
            pass

    return max(0.0, min(1.0, round(base, 3)))


def is_shallow(text: str) -> bool:
    """Cheap pre-check for clearly shallow comments (used as a filter signal)."""
    if not text:
        return True
    t = re.sub(r"\s+", " ", text.lower().strip(" .!?,"))
    if t in _SHALLOW_REACTIONS:
        return True
    toks = _tokens(t)
    if 0 < len(toks) <= 4 and not _NUMBER_RE.search(text) and not re.search(r"[A-Z][a-z]{2,}", text):
        if len(set(toks)) <= 3:
            return True
    return False
