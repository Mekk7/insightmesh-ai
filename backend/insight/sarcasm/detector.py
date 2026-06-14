# backend/insight/sarcasm/detector.py
"""
Sarcasm / Irony detection.

Why it matters:
  "Yeah, GREAT idea putting the charger port behind a flap that breaks weekly."
  A naive sentiment classifier reads this as 4-star praise because of "GREAT".
  A sarcasm-aware pipeline flips that signal and prevents inflated sentiment.

Implementation:
  - Lazy-loaded transformer: cardiffnlp/twitter-roberta-base-irony (English-trained,
    free, ~500MB on first use, then cached).
  - Cheap regex pre-filter that gates the expensive transformer call to comments
    with sarcasm-suggesting cues (CAPS bursts, scare quotes, "love how", "great job"
    + a negative consequence, etc). This avoids running the model on every comment.
  - Returns a per-review label: {"is_sarcastic": bool, "score": float} plus a
    suggested sentiment adjustment ("flip" or "soften" or "none").
  - Configurable via env: USE_SARCASM_DETECTOR=1 to enable. Defaults off so first-time
    runs don't trigger a model download unexpectedly.

Public API:
  detect(text) -> {"is_sarcastic": bool, "score": float, "adjustment": str}
  detect_batch(texts) -> List[same shape as detect]
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

USE_SARCASM = os.getenv("USE_SARCASM_DETECTOR", "0") in ("1", "true", "yes")
SARCASM_MODEL_NAME = os.getenv("SARCASM_MODEL", "cardiffnlp/twitter-roberta-base-irony")

_pipeline = None
_pipeline_failed = False


def _load_pipeline():
    """Lazy-load the irony classifier. Returns None if disabled or failed."""
    global _pipeline, _pipeline_failed
    if not USE_SARCASM or _pipeline_failed:
        return None
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline(
            "text-classification",
            model=SARCASM_MODEL_NAME,
            top_k=None,
            truncation=True,
            max_length=128,
        )
        return _pipeline
    except Exception:
        _pipeline_failed = True
        return None


# --- Cheap pre-filter -------------------------------------------------------
# Patterns that suggest sarcasm. If none match, skip the transformer entirely.

_CAPS_BURST = re.compile(r"\b[A-Z]{3,}\b")
_SCARE_QUOTES = re.compile(r"\"[^\"]{2,40}\"|'[^']{2,40}'")
_SARCASM_PHRASES = re.compile(
    r"\b("
    r"yeah right|sure thing|love how|so glad|big surprise|"
    r"thanks(?:\s+a\s+lot)?(?:\s+for)?(?=[^a-z]|$)|"
    r"oh great|just great|wonderful idea|brilliant idea|"
    r"genius move|what a (?:treat|surprise|joy)|"
    r"good luck (?:with|getting)|enjoy (?:the|your)|"
    r"because (?:that's|that\sis) what"
    r")\b",
    re.IGNORECASE,
)
_CONTRADICTION_CUES = re.compile(
    r"(love|great|amazing|perfect|wonderful|brilliant|fantastic)[^.!?]{1,80}(?:break|crash|fail|garbage|trash|terrible|awful|never works|won'?t work|broke|broken|return(?:ed|ing)|refund)",
    re.IGNORECASE,
)


def _cheap_prefilter(text: str) -> bool:
    """Return True if the text has any sarcasm-suggesting signal worth checking."""
    if not text or len(text) < 6:
        return False
    if _SARCASM_PHRASES.search(text):
        return True
    if _CONTRADICTION_CUES.search(text):
        return True
    if _SCARE_QUOTES.search(text) and any(w in text.lower() for w in ("feature", "fix", "improvement", "premium", "luxury")):
        return True
    caps = _CAPS_BURST.findall(text)
    if len(caps) >= 2 and any(w in text.lower() for w in ("great", "love", "amazing", "perfect", "wonderful")):
        return True
    return False


def _empty_result() -> Dict[str, Any]:
    return {"is_sarcastic": False, "score": 0.0, "adjustment": "none"}


def _decide_adjustment(score: float) -> str:
    if score >= 0.80:
        return "flip"
    if score >= 0.60:
        return "soften"
    return "none"


def detect(text: str) -> Dict[str, Any]:
    """Classify one piece of text. Returns dict with is_sarcastic/score/adjustment."""
    if not text or not text.strip():
        return _empty_result()
    if not USE_SARCASM:
        # Pure regex fallback when transformer disabled — only confident matches
        if _SARCASM_PHRASES.search(text) and _CONTRADICTION_CUES.search(text):
            return {"is_sarcastic": True, "score": 0.7, "adjustment": "soften"}
        return _empty_result()
    if not _cheap_prefilter(text):
        return _empty_result()
    pipe = _load_pipeline()
    if pipe is None:
        return _empty_result()
    try:
        out = pipe(text[:512])
        # Result shape: [[{label, score}, ...]] with top_k=None
        if isinstance(out, list) and out and isinstance(out[0], list):
            scores = {item["label"].lower(): float(item["score"]) for item in out[0]}
        elif isinstance(out, list) and out and isinstance(out[0], dict):
            scores = {out[0]["label"].lower(): float(out[0]["score"])}
        else:
            scores = {}
        # Cardiffnlp labels: "irony" / "non_irony"
        sarcasm_score = scores.get("irony", scores.get("sarcastic", 0.0))
        is_sarcastic = sarcasm_score >= 0.5
        return {
            "is_sarcastic": bool(is_sarcastic),
            "score": round(sarcasm_score, 3),
            "adjustment": _decide_adjustment(sarcasm_score),
        }
    except Exception:
        return _empty_result()


def detect_batch(texts: List[str]) -> List[Dict[str, Any]]:
    """Batch wrapper. Skips transformer for any text that fails the prefilter."""
    if not texts:
        return []
    if not USE_SARCASM:
        # Cheap path: just regex fallback
        return [detect(t) for t in texts]
    pipe = _load_pipeline()
    if pipe is None:
        return [_empty_result() for _ in texts]

    # Pre-filter so we only run the model on candidates
    candidate_idx = [i for i, t in enumerate(texts) if t and _cheap_prefilter(t)]
    out: List[Dict[str, Any]] = [_empty_result() for _ in texts]
    if not candidate_idx:
        return out
    try:
        batch = [texts[i][:512] for i in candidate_idx]
        results = pipe(batch, batch_size=8)
        for slot, res in zip(candidate_idx, results):
            if isinstance(res, list):
                scores = {item["label"].lower(): float(item["score"]) for item in res}
            elif isinstance(res, dict):
                scores = {res["label"].lower(): float(res["score"])}
            else:
                scores = {}
            sarcasm_score = scores.get("irony", scores.get("sarcastic", 0.0))
            out[slot] = {
                "is_sarcastic": sarcasm_score >= 0.5,
                "score": round(sarcasm_score, 3),
                "adjustment": _decide_adjustment(sarcasm_score),
            }
    except Exception:
        pass
    return out


def adjust_sentiment(original_stars: int, sarcasm: Dict[str, Any]) -> int:
    """Apply sarcasm adjustment to a 1-5 star sentiment."""
    if not sarcasm or not sarcasm.get("is_sarcastic"):
        return original_stars
    adj = sarcasm.get("adjustment", "none")
    if adj == "flip":
        return max(1, 6 - original_stars)  # 1↔5, 2↔4, 3 stays
    if adj == "soften":
        if original_stars >= 4:
            return original_stars - 1
        if original_stars <= 2:
            return original_stars + 1
    return original_stars
