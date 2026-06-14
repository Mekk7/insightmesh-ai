# backend/insight/trust/score.py
"""
TrustScore — a single 0-100 number for "should I buy this?".

Why this exists:
  A raw 4.1-star average is not the answer. It doesn't account for:
    - sarcastic 5-star reviews that are actually negative
    - astroturfed praise-bombs
    - "47% of buyers said they're returning it" (worse than the stars suggest)
    - tiny sample size (5 reviews → low confidence)
    - lopsided language distribution (only English heard)
    - very old reviews dragging the average

TrustScore synthesizes all of those signals into one number that the consumer
can act on, with a clear breakdown so they can interrogate WHY.

The score is composed (each contributes a portion):
    +35  baseline sentiment       (avg_sentiment 1..5 → 0..35)
    +20  decision-health signal   (recommend% - return% - avoid%, normalized)
    +15  sample-size confidence   (logarithmic, caps at ~200 reviews)
    +10  multilingual coverage    (more languages = more confidence)
    +10  recency / freshness      (recent reviews score higher than old)
    -10  astroturf penalty        (if astroturf flag fires)
    -10  sarcasm prevalence       (high % flagged → reduce trust in stars)
    -10  severity tax             (CRITICAL clusters anywhere → significant hit)

Final score clamped to 0..100. We also return a "grade" and a "verdict" string,
plus the full breakdown so the UI can show exactly what's pulling the number up
or down.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _grade_from_score(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Strong"
    if score >= 55:
        return "Mixed"
    if score >= 40:
        return "Risky"
    return "Avoid"


def _verdict_from_score(score: float, mode: str = "customer") -> str:
    if mode == "company":
        if score >= 85:
            return "Healthy product. Maintain investment, focus on growth."
        if score >= 70:
            return "Solid but not perfect. Address top complaint to push to excellent."
        if score >= 55:
            return "Significant gaps. The roadmap will tell you which to fix first."
        if score >= 40:
            return "Trust is breaking down. Treat the roadmap as urgent."
        return "Crisis territory. Immediate intervention required."
    # consumer
    if score >= 85:
        return "Strong buy. Real reviewers consistently happy across the board."
    if score >= 70:
        return "Solid buy. Some flaws but most owners stand behind it."
    if score >= 55:
        return "Cautious buy. Reviewers are mixed — check the aspect breakdown."
    if score >= 40:
        return "Wait. Multiple red flags from real buyers."
    return "Skip for now. Pattern of strong negative signals."


def compute_trust_score(
    *,
    average_sentiment: Optional[float],
    sample_size: int,
    language_count: int,
    astroturf_flag: bool,
    sarcasm_stats: Optional[Dict[str, Any]] = None,
    decision_health: Optional[Dict[str, Any]] = None,
    canonical_clusters: Optional[List[Dict[str, Any]]] = None,
    sentiment_over_time: Optional[List[Dict[str, Any]]] = None,
    mode: str = "customer",
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the TrustScore + a transparent breakdown.

    Returns:
      {
        "score": 0..100,
        "grade": "Excellent" | "Strong" | "Mixed" | "Risky" | "Avoid",
        "verdict": str,
        "confidence": "high" | "medium" | "low",
        "breakdown": [
          {"label": "Baseline sentiment", "delta": +28.0, "max": 35.0, "note": "4.1★ across 247 reviews"},
          ...
        ],
        "components": { ... raw inputs for debugging ... }
      }
    """
    breakdown: List[Dict[str, Any]] = []

    # ---- 1. Baseline sentiment (0..35) ----
    avg = _safe_float(average_sentiment, 3.0)
    sent_contrib = max(0.0, min(35.0, (avg - 1.0) * (35.0 / 4.0)))  # 1★→0, 5★→35
    breakdown.append({
        "label": "Baseline sentiment",
        "delta": round(sent_contrib, 1),
        "max": 35.0,
        "note": f"{avg:.1f}★ across {sample_size} reviews",
    })

    # ---- 2. Decision health (0..20) ----
    dh = decision_health or {}
    rec = _safe_float(dh.get("recommend_pct", 0)) / 100.0
    ret = _safe_float(dh.get("return_pct", 0)) / 100.0
    avo = _safe_float(dh.get("avoid_pct", 0)) / 100.0
    net = max(-1.0, min(1.0, (rec - ret - avo)))
    # Map -1..+1 to 0..20 (centered at 10)
    dh_contrib = 10.0 + (net * 10.0)
    dh_note_parts = []
    if rec > 0:
        dh_note_parts.append(f"{rec*100:.0f}% recommending")
    if ret > 0:
        dh_note_parts.append(f"{ret*100:.0f}% returning")
    if avo > 0:
        dh_note_parts.append(f"{avo*100:.0f}% warning others")
    breakdown.append({
        "label": "What buyers are doing",
        "delta": round(dh_contrib, 1),
        "max": 20.0,
        "note": " · ".join(dh_note_parts) or "no stated actions detected",
    })

    # ---- 3. Sample-size confidence (0..15) ----
    # Logarithmic: 5 reviews → ~4, 50 → ~10, 200+ → 15
    n = max(0, int(sample_size or 0))
    if n <= 0:
        size_contrib = 0.0
    else:
        size_contrib = min(15.0, 3.4 * math.log10(n + 1) * 2.0)
    breakdown.append({
        "label": "Sample size confidence",
        "delta": round(size_contrib, 1),
        "max": 15.0,
        "note": (
            "Very small sample — directional only" if n < 15
            else "Moderate sample" if n < 60
            else "Robust sample" if n < 200
            else "Strong sample"
        ),
    })

    # ---- 4. Multilingual coverage (0..10) ----
    lang_n = max(1, int(language_count or 1))
    # 1 lang → 4, 3 → 7, 5+ → 10
    lang_contrib = min(10.0, 4.0 + (lang_n - 1) * 1.5)
    breakdown.append({
        "label": "Cross-language coverage",
        "delta": round(lang_contrib, 1),
        "max": 10.0,
        "note": f"Heard across {lang_n} language{'s' if lang_n != 1 else ''}",
    })

    # ---- 5. Recency / freshness (0..10) ----
    series = sentiment_over_time or []
    if series:
        # Recent slice (last 30% of points) vs earliest slice
        cutoff = max(1, len(series) // 3)
        recent_avg = sum(_safe_float(p.get("avg_sentiment")) for p in series[-cutoff:]) / cutoff
        older_avg = sum(_safe_float(p.get("avg_sentiment")) for p in series[:cutoff]) / cutoff
        delta = recent_avg - older_avg
        # Reward recent sentiment being stable or rising
        if delta >= 0.1:
            recency_contrib = 10.0
            recency_note = f"Sentiment improving recently (+{delta:.2f}★)"
        elif delta >= -0.1:
            recency_contrib = 8.0
            recency_note = "Sentiment stable across the window"
        elif delta >= -0.3:
            recency_contrib = 5.0
            recency_note = f"Slight recent decline ({delta:+.2f}★)"
        else:
            recency_contrib = 2.0
            recency_note = f"Sentiment declining ({delta:+.2f}★)"
    else:
        recency_contrib = 5.0
        recency_note = "No timestamped signal — neutral"
    breakdown.append({
        "label": "Recency / momentum",
        "delta": round(recency_contrib, 1),
        "max": 10.0,
        "note": recency_note,
    })

    # ---- 6. Astroturf penalty (0 to -10) ----
    if astroturf_flag:
        astroturf_delta = -10.0
        astroturf_note = "Coordinated review patterns detected"
    else:
        astroturf_delta = 0.0
        astroturf_note = "No coordinated review patterns"
    breakdown.append({
        "label": "Astroturf penalty",
        "delta": round(astroturf_delta, 1),
        "max": 0.0,
        "note": astroturf_note,
    })

    # ---- 7. Sarcasm prevalence penalty (0 to -10) ----
    sarc = sarcasm_stats or {}
    flagged = int(sarc.get("flagged_count", 0) or 0)
    total = max(1, int(sarc.get("total", n) or n or 1))
    sarc_ratio = flagged / total
    if sarc_ratio >= 0.20:
        sarc_delta = -10.0
        sarc_note = f"High sarcasm rate ({sarc_ratio*100:.0f}%) — star ratings unreliable"
    elif sarc_ratio >= 0.10:
        sarc_delta = -5.0
        sarc_note = f"Moderate sarcasm ({sarc_ratio*100:.0f}%) — some star inflation"
    elif sarc_ratio > 0:
        sarc_delta = -2.0
        sarc_note = f"Light sarcasm ({sarc_ratio*100:.0f}%)"
    else:
        sarc_delta = 0.0
        sarc_note = "No sarcasm detected"
    breakdown.append({
        "label": "Sarcasm adjustment",
        "delta": round(sarc_delta, 1),
        "max": 0.0,
        "note": sarc_note,
    })

    # ---- 8. Severity tax (0 to -10) ----
    clusters = canonical_clusters or []
    has_critical = any((c.get("severity") or {}).get("severity") == "CRITICAL" for c in clusters)
    has_high = any((c.get("severity") or {}).get("severity") == "HIGH" for c in clusters)
    has_safety = any((c.get("severity") or {}).get("is_safety") for c in clusters)
    if has_safety:
        sev_delta = -10.0
        sev_note = "Safety-related complaints present"
    elif has_critical:
        sev_delta = -8.0
        sev_note = "Critical-severity complaints present"
    elif has_high:
        sev_delta = -4.0
        sev_note = "High-severity complaints present"
    else:
        sev_delta = 0.0
        sev_note = "No critical-severity complaints"
    breakdown.append({
        "label": "Severity tax",
        "delta": round(sev_delta, 1),
        "max": 0.0,
        "note": sev_note,
    })

    # ---- Final score ----
    raw_score = sum(b["delta"] for b in breakdown)
    score = max(0.0, min(100.0, raw_score))

    # ---- Confidence label (data quality) ----
    if n < 15:
        confidence = "low"
    elif n < 60:
        confidence = "medium"
    else:
        confidence = "high"

    # ---- HONESTY GATE ----------------------------------------------------
    # If the evidence layer says we don't have enough real reviews, we REFUSE to
    # present this as a confident product verdict. A low score driven by thin or
    # junk data must never be narrated as "Risky / Avoid / red flags" — that's the
    # fake precision we're killing. Instead we mark it provisional and say so.
    ev = evidence or {}
    insufficient = bool(ev.get("insufficient"))
    if insufficient:
        n_rel = int(ev.get("n_relevant", 0) or 0)
        confidence = "low"
        grade = "Unrated"
        if n_rel == 0:
            verdict = (
                "Not enough real reviews to judge this yet. What we found is mostly "
                "off-topic chatter, not product feedback."
            )
        else:
            verdict = (
                f"Too little signal for a confident verdict — based on just {n_rel} "
                f"real review{'s' if n_rel != 1 else ''}. Treat the below as a first "
                "impression, not a recommendation."
            )
        return {
            "score": round(score, 1),
            "grade": grade,
            "verdict": verdict,
            "confidence": confidence,
            "insufficient_data": True,
            "provisional": True,
            "evidence": ev,
            "breakdown": breakdown,
            "components": {
                "average_sentiment": avg,
                "sample_size": n,
                "relevant_sample_size": ev.get("n_relevant"),
                "language_count": lang_n,
                "astroturf_flag": astroturf_flag,
                "sarcasm_ratio": round(sarc_ratio, 3),
                "decision_net_intent": round(net, 3),
                "has_critical": has_critical,
                "has_safety": has_safety,
            },
        }

    return {
        "score": round(score, 1),
        "grade": _grade_from_score(score),
        "verdict": _verdict_from_score(score, mode=mode),
        "confidence": confidence,
        "insufficient_data": False,
        "provisional": False,
        "evidence": ev or None,
        "breakdown": breakdown,
        "components": {
            "average_sentiment": avg,
            "sample_size": n,
            "relevant_sample_size": ev.get("n_relevant") if ev else None,
            "language_count": lang_n,
            "astroturf_flag": astroturf_flag,
            "sarcasm_ratio": round(sarc_ratio, 3),
            "decision_net_intent": round(net, 3),
            "has_critical": has_critical,
            "has_safety": has_safety,
        },
    }
