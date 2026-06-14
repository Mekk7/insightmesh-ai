"""
Reviewer Credibility Intelligence.

Scores every reviewer on how much their opinion should be trusted (0.0-1.0)
from 5 signals — ownership evidence, claim specificity, review depth, emotional
calibration, and a platform baseline — then computes credibility-weighted
alternatives to the raw sentiment average.

Hard rules (see CLAUDE.md "What NOT to Do"):
  • Pure computation — NO LLM / network / disk calls. Regex + heuristics over
    signals already on each per_review item.
  • Runs for ALL depth modes (no quick_mode guard).
  • Additive only: attaches `_credibility` to each review; never mutates other fields.
  • Fail-open: any error degrades to a neutral score rather than breaking analysis.

Data note: this runs in analyze_core right after the per-review loop, BEFORE the
deferred deep-classification phase. So the deep-classifier nested signals
(`deep`/`deep_signals`, `understanding`) are usually absent here (and entirely
absent in Quick mode, which skips deep classify). The scorer therefore leans on
the text/platform/emotion signals that ARE present, using the deep signals only
as an opportunistic bonus when they happen to be attached.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---- shared star helper (mirrors synthesizer._stars_of / analyze_core stars) ----
# per_review carries `sentiment` as a label ("4 stars") and `sentiment_score` in
# 0..1. The dashboard's average_sentiment is on the 1..5 scale, so all the weighted
# metrics below MUST be in stars too (not the raw 0..1 score).
_STAR_RE = re.compile(r"\s*(\d+)")


def _stars(review: Dict[str, Any]) -> float:
    s = review.get("sentiment")
    if isinstance(s, (int, float)):
        # already a star-ish number (1..5) — clamp defensively
        return max(1.0, min(5.0, float(s)))
    if isinstance(s, str):
        m = _STAR_RE.match(s)
        if m:
            try:
                return float(int(m.group(1)))
            except ValueError:
                pass
    sc = review.get("sentiment_score")
    if isinstance(sc, (int, float)):
        return max(1.0, min(5.0, 1.0 + 4.0 * float(sc)))
    return 3.0


def _experience_stage(deep: Dict[str, Any], understanding: Dict[str, Any]) -> str:
    """experience_stage can be a bare string or a {"stage": ...} dict."""
    for src in (deep, understanding):
        es = src.get("experience_stage")
        if isinstance(es, dict):
            es = es.get("stage")
        if isinstance(es, str) and es:
            return es
    return ""


def score_credibility(review: Dict[str, Any], all_reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return {credibility, credibility_label, factors{...}} for one review."""
    try:
        text = review.get("translated_text") or review.get("original", "") or ""
        understanding = review.get("understanding") or {}
        deep = review.get("deep") or review.get("deep_signals") or {}
        if not isinstance(understanding, dict):
            understanding = {}
        if not isinstance(deep, dict):
            deep = {}

        # 1. OWNERSHIP EVIDENCE (0-0.25) — does this person actually own/use it?
        ownership = 0.0
        experience_stage = _experience_stage(deep, understanding)
        if experience_stage in ("long_term", "expert", "repeat_buyer"):
            ownership = 0.25
        elif experience_stage in ("short_term",):
            ownership = 0.20
        elif experience_stage in ("first_impression",):
            ownership = 0.10
        else:
            has_own = bool(re.search(
                r'(i (bought|purchased|own|have|got|ordered|received|use daily|returned))',
                text, re.I))
            has_duration = bool(re.search(
                r'(for|after|since)\s+\d+\s*(day|week|month|year)', text, re.I))
            if has_own and has_duration:
                ownership = 0.20
            elif has_own:
                ownership = 0.12
            elif has_duration:
                ownership = 0.10
            else:
                ownership = 0.02

        # 2. SPECIFICITY OF CLAIMS (0-0.25) — verifiable claims > vague opinions
        claims = deep.get("verified_claims") or []
        opinions_only = deep.get("claims_vs_opinions", "") == "opinion_only"
        has_measurements = bool(re.search(
            r'\d+\s*(hour|hr|min|gb|tb|inch|mm|fps|hz|ms|mbps|percent|%|dollar|\$)',
            text, re.I))
        has_specific_feature = bool(re.search(
            r'(battery|display|screen|trackpad|joystick|trigger|speaker|mic|wifi|'
            r'bluetooth|usb|hdmi|dock|fan|thermal|fps|resolution)', text, re.I))
        has_comparison_detail = bool(re.search(
            r'(compared to|better than|worse than|faster than|slower than|unlike|whereas)',
            text, re.I))
        if claims and len(claims) >= 2:
            specificity = 0.25
        elif has_measurements and has_specific_feature:
            specificity = 0.22
        elif has_measurements or has_comparison_detail:
            specificity = 0.15
        elif has_specific_feature:
            specificity = 0.10
        elif opinions_only:
            specificity = 0.03
        else:
            specificity = 0.05

        # 3. REVIEW DEPTH (0-0.20) — length + structure indicate effort
        word_count = len(text.split())
        if word_count >= 100:
            depth = 0.20
        elif word_count >= 60:
            depth = 0.16
        elif word_count >= 30:
            depth = 0.10
        elif word_count >= 15:
            depth = 0.06
        else:
            depth = 0.02
        has_structure = bool(re.search(
            r'(pros?:|cons?:|however|but|although|on the other hand)', text, re.I))
        aspects_mentioned = sum(
            1 for a in ["battery", "display", "price", "comfort", "weight", "performance",
                        "build", "software", "content", "design", "screen", "audio",
                        "camera", "storage", "charging"]
            if a in text.lower())
        if has_structure:
            depth = min(0.20, depth + 0.04)
        if aspects_mentioned >= 3:
            depth = min(0.20, depth + 0.03)

        # 4. EMOTIONAL CALIBRATION (0-0.15) — balanced > one-sided rant
        intents = deep.get("multi_intent") or understanding.get("intents") or []
        if isinstance(intents, list):
            has_praise = any(i in ("praise", "recommendation") for i in intents)
            has_complaint = any(i in ("complaint", "criticism") for i in intents)
        else:
            has_praise = "praise" in str(intents).lower()
            has_complaint = "complaint" in str(intents).lower()
        # Fallback when no deep intents are present (the common case at this stage,
        # and ALWAYS in Quick mode): detect both-sidedness from the text itself.
        if not (has_praise or has_complaint):
            has_praise = bool(re.search(
                r'\b(love|great|excellent|amazing|impressive|recommend|worth it|'
                r'fantastic|solid|enjoy|good)\b', text, re.I))
            has_complaint = bool(re.search(
                r'\b(hate|terrible|awful|disappointing|broke|broken|issue|problem|'
                r'bug|return|refund|waste|annoying|bad|worse|flaw)\b', text, re.I))
        if has_praise and has_complaint:
            calibration = 0.15
        elif has_praise or has_complaint:
            calibration = 0.08
        else:
            calibration = 0.04
        # Extreme emotion + very short text = rant → penalize. per_review stores the
        # emotion LABEL in `emotion` and the confidence in `emotion_score`.
        emotion = review.get("emotion")
        if isinstance(emotion, dict):
            emotion_score = float(emotion.get("score") or 0)
        else:
            try:
                emotion_score = float(review.get("emotion_score") or 0)
            except (TypeError, ValueError):
                emotion_score = 0.0
        if emotion_score > 0.9 and word_count < 20:
            calibration = max(0.0, calibration - 0.05)

        # 5. PLATFORM CREDIBILITY BASELINE (0-0.15)
        platform = (review.get("platform")
                    or (review.get("meta") or {}).get("platform") or "").lower()
        if platform == "appstore":
            platform_score = 0.12
        elif platform == "reddit":
            platform_score = 0.11 if review.get("subreddit") else 0.08
        elif platform == "youtube":
            platform_score = 0.05
        else:
            platform_score = 0.07

        raw = ownership + specificity + depth + calibration + platform_score
        composite = round(min(0.95, raw), 2)
        label = "HIGH" if composite >= 0.60 else "MEDIUM" if composite >= 0.35 else "LOW"

        return {
            "credibility": composite,
            "credibility_label": label,
            "factors": {
                "ownership": round(ownership, 2),
                "specificity": round(specificity, 2),
                "depth": round(depth, 2),
                "calibration": round(calibration, 2),
                "platform": round(platform_score, 2),
            },
        }
    except Exception:
        return {
            "credibility": 0.5, "credibility_label": "MEDIUM",
            "factors": {"ownership": 0.0, "specificity": 0.0, "depth": 0.0,
                        "calibration": 0.0, "platform": 0.0},
        }


def compute_weighted_metrics(per_review: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Credibility-weighted alternatives to the raw sentiment average.

    All sentiment values are on the 1..5 STAR scale (matching overview.average_sentiment),
    so the dashboard's RAW vs WEIGHTED vs CREDIBLE-ONLY comparison is apples-to-apples.
    """
    per_review = per_review or []
    weighted_sentiment_sum = 0.0
    weight_sum = 0.0
    credible_reviews: List[Dict[str, Any]] = []   # credibility >= 0.5
    casual_reviews: List[Dict[str, Any]] = []      # credibility < 0.3

    for review in per_review:
        if not isinstance(review, dict):
            continue
        cred = (review.get("_credibility") or {}).get("credibility", 0.5)
        try:
            cred = float(cred)
        except (TypeError, ValueError):
            cred = 0.5
        stars = _stars(review)
        weighted_sentiment_sum += stars * cred
        weight_sum += cred
        if cred >= 0.5:
            credible_reviews.append(review)
        elif cred < 0.3:
            casual_reviews.append(review)

    n = len([r for r in per_review if isinstance(r, dict)])
    weighted_avg = round(weighted_sentiment_sum / max(0.01, weight_sum), 2)
    raw_avg = round(sum(_stars(r) for r in per_review if isinstance(r, dict)) / max(1, n), 2)
    credible_avg = (round(sum(_stars(r) for r in credible_reviews) / len(credible_reviews), 2)
                    if credible_reviews else None)
    casual_avg = (round(sum(_stars(r) for r in casual_reviews) / len(casual_reviews), 2)
                  if casual_reviews else None)

    gap = (round(credible_avg - casual_avg, 2)
           if credible_avg is not None and casual_avg is not None else None)

    insight: Optional[str] = None
    if gap is not None and abs(gap) >= 0.5:
        if gap > 0:
            insight = (f"Credible reviewers rate {gap:.1f}★ higher than casual commenters "
                       f"— the product is better than the raw average suggests.")
        else:
            insight = (f"Credible reviewers rate {abs(gap):.1f}★ lower than casual commenters "
                       f"— the product may be worse than the raw average suggests.")

    def _count(lbl: str) -> int:
        return sum(1 for r in per_review
                   if isinstance(r, dict)
                   and (r.get("_credibility") or {}).get("credibility_label") == lbl)

    return {
        "weighted_sentiment": weighted_avg,
        "raw_sentiment": raw_avg,
        "sentiment_gap": gap,
        "credible_count": len(credible_reviews),
        "casual_count": len(casual_reviews),
        "credible_avg": credible_avg,
        "casual_avg": casual_avg,
        "insight": insight,
        "credibility_distribution": {
            "high": _count("HIGH"),
            "medium": _count("MEDIUM"),
            "low": _count("LOW"),
        },
    }
