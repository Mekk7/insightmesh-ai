# backend/insight/understanding/review_understanding.py
"""
Smart per-review understanding — the heart of the analyzer.

WHY THIS EXISTS
---------------
The transformer pipeline (langdetect → opus-mt translate → multilingual BERT
sentiment → English emotion model) breaks badly on romanized / code-mixed text
such as Hinglish ("Aap bahut acche gulab jamun bhejiye", "Wah kya taste hoga").
Concretely it fails three ways:

  1. langdetect sees ASCII letters and says "en", so the text is never translated.
  2. The English-only emotion model reads the un-translated Hindi as gibberish and
     emits confident-but-wrong labels (e.g. "sadness" for a compliment).
  3. opus-mt, fed romanized Hindi it can't parse, HALLUCINATES (the infamous
     "I'm sorry I'm sorry I'm sorry" translation), which then poisons sentiment,
     emotion, and clustering downstream.

THE FIX
-------
When an LLM backend is available (Ollama or OpenAI), we make ONE structured
JSON call per review that natively understands code-mixed text and returns:

  - detected language (BCP-ish code, e.g. "hi-Latn" for romanized Hindi)
  - a faithful English gloss (NOT a hallucinated paraphrase)
  - category: Praise | Complaint | Suggestion | Prediction | Neutral
  - sentiment as 1-5 stars + a -1..1 polarity
  - emotion (joy/anger/sadness/fear/surprise/disgust/neutral)
  - is_sarcastic + is_relevant flags
  - a short, human-readable reason phrase

This judgment OVERRIDES the transformer outputs when the model is confident.
The transformers remain as the fallback path when no LLM is configured.

COST / SPEED
------------
Every call is cached permanently by the unified llm client (SQLite, keyed on the
exact prompt). So each unique comment costs exactly one cheap call ONCE, then is
free forever. A 40-comment analysis is ~40 tiny calls the first time, 0 after.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.utils import llm as llm_client

log = logging.getLogger("insightmesh.understanding")

# Canonical categories the rest of the pipeline expects
_CANON_CATS = {"Praise", "Complaint", "Suggestion", "Prediction", "Neutral"}

# Emotion vocabulary aligned with the existing j-hartmann label space so the
# downstream EMOTION_LABEL_PRESENTATION map keeps working unchanged.
_CANON_EMOTIONS = {"joy", "anger", "sadness", "fear", "surprise", "disgust", "neutral"}

_STAR_LABELS = {1: "1 star", 2: "2 stars", 3: "3 stars", 4: "4 stars", 5: "5 stars"}


def llm_available() -> bool:
    """True when we have a backend that can do per-review understanding."""
    try:
        return llm_client.available_backend() != "none"
    except Exception:
        return False


def _coerce_stars(value: Any) -> Optional[int]:
    """Accept 1-5 as int/float/str; clamp; return None if unusable."""
    try:
        n = int(round(float(value)))
        return max(1, min(5, n))
    except Exception:
        return None


def _coerce_category(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    for c in _CANON_CATS:
        if v.startswith(c.lower()):
            return c
    # common synonyms
    if v in ("positive", "compliment", "appreciation"):
        return "Praise"
    if v in ("negative", "issue", "problem", "bug"):
        return "Complaint"
    if v in ("request", "feature request", "idea", "wish"):
        return "Suggestion"
    if v in ("forecast", "expectation"):
        return "Prediction"
    return None


def _coerce_emotion(value: Any) -> str:
    if not isinstance(value, str):
        return "neutral"
    v = value.strip().lower()
    if v in _CANON_EMOTIONS:
        return v
    # map a few common alternates into the canonical 7
    alt = {
        "happy": "joy", "happiness": "joy", "delight": "joy", "love": "joy",
        "angry": "anger", "frustration": "anger", "frustrated": "anger", "annoyed": "anger",
        "sad": "sadness", "disappointed": "sadness", "disappointment": "sadness",
        "scared": "fear", "worried": "fear", "anxiety": "fear",
        "surprised": "surprise", "excited": "surprise", "excitement": "surprise",
        "disgusted": "disgust",
    }
    return alt.get(v, "neutral")


# One compact, strict prompt. We keep it short to minimize tokens/cost while
# being explicit about the code-mixed requirement and the JSON schema.
_SYSTEM = (
    "You are a multilingual product-review analyst. You are fluent in code-mixed and "
    "romanized text, including Hinglish (Hindi written in Latin letters), romanized "
    "Tamil/Telugu/Bengali/Urdu, Spanish, Indonesian, Arabic, etc. You never hallucinate "
    "a translation; if a phrase is ambiguous you translate literally and conservatively. "
    "You judge sentiment and category from the writer's ACTUAL intent, not surface words."
)

def _build_prompt(text: str, product: str, source_title: str = "") -> str:
    product_line = f'The product being reviewed is: "{product}".\n' if product else ""
    # When the comment comes from a YouTube video, tell the model so it scores the
    # PRODUCT feedback and treats video-specific reactions ("great video!", "loved
    # this breakdown") as off-topic / Neutral rather than product praise.
    title_line = (
        f'This comment is from a YouTube video titled: "{source_title}". '
        "Analyze the comment as PRODUCT feedback, ignoring video-specific reactions "
        "(praise of the video/creator, 'first', timestamps, like/subscribe).\n"
        if source_title else ""
    )
    return (
        f"{product_line}"
        f"{title_line}"
        "Analyze this ONE review and return a JSON object with EXACTLY these keys:\n"
        '  "language": BCP-47-ish code of the ORIGINAL text. Use "hi-Latn" for romanized Hindi, '
        '"en" for English, "id" for Indonesian, etc.\n'
        '  "english": a faithful, literal English translation (if already English, repeat it). '
        "Never invent content that is not in the original.\n"
        '  "category": one of "Praise","Complaint","Suggestion","Prediction","Neutral".\n'
        '  "stars": integer 1-5 reflecting the writer\'s sentiment toward the product '
        "(5=loves it, 1=hates it, 3=neutral/mixed).\n"
        '  "polarity": number from -1.0 (very negative) to 1.0 (very positive).\n'
        '  "emotion": one of "joy","anger","sadness","fear","surprise","disgust","neutral".\n'
        '  "is_sarcastic": true/false.\n'
        '  "is_relevant": true/false — is this actually about the product (false for spam, '
        "pure emojis, off-topic chatter)?\n"
        '  "reason": a short (<=10 word) plain-English phrase capturing the key point, or "".\n'
        '  "confidence": number 0.0-1.0 — how confident you are in this judgment.\n\n'
        "Rules:\n"
        "- A compliment phrased as a request (e.g. 'please keep making these, so good') is Praise.\n"
        "- Mixed praise+complaint → pick the DOMINANT one; if truly balanced use Neutral.\n"
        "- Pure emoji / one-word hype ('nice', '😍') with no substance → Neutral, low confidence.\n"
        "- Judge romanized text by meaning, not by English look-alike words.\n\n"
        f'Review: "{text}"'
    )


def understand_review(text: str, product: str = "", source_title: str = "") -> Optional[Dict[str, Any]]:
    """
    Return a structured understanding dict for one review, or None if no LLM
    backend is available or the call fails. Cached permanently by llm_client.

    `source_title` (optional): the YouTube video title the comment came from, so the
    model can distinguish product feedback from video reactions.
    """
    text = (text or "").strip()
    if not text:
        return None
    if not llm_available():
        return None

    prompt = _build_prompt(text[:1200], product or "", (source_title or "")[:160])
    parsed = llm_client.chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=300,
    )
    if not isinstance(parsed, dict):
        return None

    stars = _coerce_stars(parsed.get("stars"))
    category = _coerce_category(parsed.get("category"))
    if stars is None and category is None:
        # Useless response; let the caller fall back to transformers
        return None

    # Derive any missing piece from the other when possible
    if stars is None and category is not None:
        stars = {"Praise": 5, "Complaint": 2, "Suggestion": 3, "Prediction": 3, "Neutral": 3}[category]
    if category is None and stars is not None:
        category = "Praise" if stars >= 4 else ("Complaint" if stars <= 2 else "Neutral")

    # ---- Internal stars<->category consistency guard ----
    # Even a single LLM call sometimes returns a contradictory pair (e.g.
    # category="Complaint" with stars=5 on a sarcastic one-liner). The category
    # reflects the writer's intent and is the higher-confidence signal, so we
    # pull the stars into the category's valid band before returning. This means
    # NOTHING downstream — analyzer, cache, exports — ever sees a 5-star
    # complaint. "Honest by default" starts at the source of truth.
    if category is not None and stars is not None:
        if category == "Complaint" and stars >= 4:
            stars = 2
        elif category == "Praise" and stars <= 2:
            stars = 5
        elif category in ("Suggestion", "Prediction", "Neutral") and (stars <= 1 or stars >= 5):
            stars = 3

    try:
        polarity = float(parsed.get("polarity"))
        polarity = max(-1.0, min(1.0, polarity))
    except Exception:
        polarity = {1: -1.0, 2: -0.5, 3: 0.0, 4: 0.5, 5: 1.0}.get(stars, 0.0)

    try:
        confidence = float(parsed.get("confidence"))
        confidence = max(0.0, min(1.0, confidence))
    except Exception:
        confidence = 0.6

    english = parsed.get("english")
    if not isinstance(english, str) or not english.strip():
        english = None

    language = parsed.get("language")
    if not isinstance(language, str) or not language.strip():
        language = "unknown"
    language = language.strip()

    reason = parsed.get("reason")
    if not isinstance(reason, str):
        reason = ""
    reason = reason.strip()[:120]

    return {
        "language": language,
        "english": english.strip() if english else None,
        "category": category,
        "stars": stars,
        "stars_label": _STAR_LABELS.get(stars, "3 stars"),
        "polarity": round(polarity, 3),
        "emotion": _coerce_emotion(parsed.get("emotion")),
        "is_sarcastic": bool(parsed.get("is_sarcastic")),
        "is_relevant": bool(parsed.get("is_relevant", True)),
        "reason": reason,
        "confidence": round(confidence, 3),
        "source": "llm",
    }


# ---- Lightweight romanized-language heuristic (used even without an LLM) ----
# This won't fix sentiment, but it stops us from MIS-LABELING romanized Hindi as
# English, and it suppresses the hallucinating translator for such text.

# High-signal Hinglish / romanized-Indic function words & frequent tokens.
_HINGLISH_TOKENS = {
    "hai", "nahi", "nahin", "kya", "ka", "ki", "ke", "ko", "se", "me", "mein",
    "aap", "tum", "hum", "bhai", "yaar", "acche", "accha", "achha", "bahut",
    "bohot", "kitna", "kaise", "kyun", "matlab", "sahi", "galat", "paisa",
    "paise", "lag", "raha", "raha", "rahe", "hoga", "hota", "karo", "karna",
    "bana", "bante", "bante", "wah", "wow", "mast", "bekar", "bakwas",
    "jamun", "gulab", "paneer", "chai", "khana", "swad", "taste", "sath",
    "saath", "beta", "bacche", "bacha", "pasine", "hatho", "hi",
}

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")  # actual Hindi script


def looks_romanized_indic(text: str) -> bool:
    """
    Heuristic: does this ASCII text look like romanized Hindi/Hinglish?
    Returns True if >= 2 distinct Hinglish marker tokens are present.
    Conservative on purpose — we only want to catch clear cases.
    """
    if not text:
        return False
    low = re.sub(r"[^a-z\s]", " ", text.lower())
    toks = set(low.split())
    hits = toks & _HINGLISH_TOKENS
    return len(hits) >= 2


def detect_language_smart(text: str, langdetect_fn) -> str:
    """
    Better language detection that catches romanized Indic text BEFORE handing
    off to langdetect (which would wrongly say 'en' for ASCII Hinglish).

    `langdetect_fn` is the existing `detect` callable; we only call it as a
    fallback so behavior for real English/other-script text is unchanged.
    """
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _DEVANAGARI_RE.search(t):
        return "hi"
    if looks_romanized_indic(t):
        return "hi-Latn"
    try:
        return langdetect_fn(t)
    except Exception:
        return "unknown"
