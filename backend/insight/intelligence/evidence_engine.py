# backend/insight/intelligence/evidence_engine.py
# Per-Insight Evidence Engine.
#
# A pure post-processing layer: takes the existing analyze_core `overview` dict
# plus the `per_review` results and stamps an `_evidence` metadata block onto
# every enrichable insight (clusters, aspects, roadmap items) and an
# `_analysis_confidence` block onto the overview root.
#
# Hard rules (see CLAUDE.md "What NOT to Do"):
#   - NO LLM calls, NO network, NO disk. Pure math on data already in the dicts.
#   - Runs in milliseconds.
#   - Does NOT change the existing JSON shape — it only ADDS new `_evidence` /
#     `_analysis_confidence` fields, never removes or renames anything.
#   - Runs for ALL depth modes (quick/balanced/deep) — no quick_mode guard.
#
# It is deliberately defensive: every field is read with .get and missing data
# degrades to a low-confidence-but-valid result rather than raising.

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Confidence is capped below 1.0 — we never claim certainty.
_CONF_CAP = 0.95


# -------------------- small helpers --------------------

def _clamp(x: float, lo: float = 0.0, hi: float = _CONF_CAP) -> float:
    return max(lo, min(hi, x))


def _conf_label(conf: float) -> str:
    if conf >= 0.7:
        return "HIGH"
    if conf >= 0.4:
        return "MEDIUM"
    return "LOW"


def _overall_label(conf: float) -> str:
    if conf >= 0.7:
        return "STRONG"
    if conf >= 0.4:
        return "MODERATE"
    return "LIMITED"


_STAR_RE = re.compile(r"\s*(\d+)")


def _stars_of(r: Dict[str, Any]) -> Optional[float]:
    """Best-effort star rating (1-5) for a per_review result.

    Prefers the textual `sentiment` ("3 stars" / "1 star"), falls back to the
    0..1 `sentiment_score` mapped onto 1..5. Returns None when nothing usable.
    """
    s = r.get("sentiment")
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = _STAR_RE.match(s)
        if m:
            try:
                return float(int(m.group(1)))
            except ValueError:
                pass
    sc = r.get("sentiment_score")
    if isinstance(sc, (int, float)):
        return 1.0 + 4.0 * float(sc)
    return None


def detect_conflict(star_ratings: List[float]) -> Dict[str, Any]:
    """Sentiment polarization for a set of star ratings.

    Polarized = high variance OR a bimodal split (people at both extremes with
    a thin middle). Needs >= 3 ratings to mean anything.
    """
    ratings = [s for s in star_ratings if isinstance(s, (int, float))]
    if len(ratings) < 3:
        return {"polarized": False, "variance": 0.0,
                "distribution": _distribution(ratings)}
    try:
        variance = statistics.variance(ratings)
    except statistics.StatisticsError:
        variance = 0.0
    low = sum(1 for s in ratings if s <= 2)
    high = sum(1 for s in ratings if s >= 4)
    mid = len(ratings) - low - high
    polarized = (variance > 1.5) or (low > 0 and high > 0 and mid < max(low, high))
    return {
        "polarized": bool(polarized),
        "variance": round(variance, 2),
        "distribution": {"positive": high, "neutral": mid, "negative": low},
    }


def _distribution(ratings: List[float]) -> Dict[str, int]:
    low = sum(1 for s in ratings if s <= 2)
    high = sum(1 for s in ratings if s >= 4)
    return {"positive": high, "neutral": len(ratings) - low - high, "negative": low}


# -------------------- per-cluster membership index --------------------

def _index_by_cluster(per_review: List[Dict[str, Any]]) -> Dict[int, List[int]]:
    """Group per_review indices by their cluster_id (clusters carry no member
    list of their own — membership lives on each review)."""
    idx: Dict[int, List[int]] = defaultdict(list)
    for i, r in enumerate(per_review or []):
        try:
            cid = int(r.get("cluster_id", 0))
        except (TypeError, ValueError):
            cid = 0
        idx[cid].append(i)
    return idx


def _platform_counts(per_review: List[Dict[str, Any]]) -> Counter:
    c: Counter = Counter()
    for r in (per_review or []):
        p = r.get("platform")
        if p:
            c[p] += 1
    return c


# -------------------- confidence formulas --------------------

def _cluster_volume(size: int, total_reviews: int) -> float:
    if size <= 1 or total_reviews <= 1:
        return 0.0 if size <= 1 else min(1.0, 1.0 / math.log2(max(2, total_reviews)))
    return min(1.0, math.log2(max(1, size)) / math.log2(max(2, total_reviews)))


def _aspect_confidence(mentions: int, total_reviews: int,
                       agreement: Optional[float]) -> float:
    volume = min(1.0, mentions / max(1, total_reviews * 0.3))
    agr = agreement if agreement is not None else 0.3
    bonus = 0.2 if mentions >= 5 else 0.1
    return _clamp(volume * 0.4 + agr * 0.4 + bonus)


# -------------------- main entry point --------------------

def enrich_with_evidence(
    overview: Dict[str, Any],
    per_review: List[Dict[str, Any]],
    kept_meta: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Add `_evidence` metadata to each enrichable block of `overview` and an
    `_analysis_confidence` block to the overview root. Returns the same dict
    (mutated in place and also returned for convenience). Pure / no I/O.
    """
    if not isinstance(overview, dict):
        return overview
    per_review = per_review or []
    total_reviews = len(per_review)

    members_by_cluster = _index_by_cluster(per_review)
    plat_counts = _platform_counts(per_review)
    total_platforms = len([p for p, c in plat_counts.items() if c > 0])

    # ---- 1. Clusters ----
    cluster_confidences: Dict[int, float] = {}
    clusters = overview.get("canonical_clusters")
    if isinstance(clusters, list):
        for cl in clusters:
            if not isinstance(cl, dict):
                continue
            try:
                cid = int(cl.get("cluster_id", cl.get("id", 0)))
            except (TypeError, ValueError):
                cid = 0
            member_idx = members_by_cluster.get(cid, [])
            # Prefer the cluster's own count; fall back to observed membership.
            size = int(cl.get("count") or cl.get("size") or len(member_idx) or 0)
            cohesion = float(cl.get("centroid_sim_mean") or cl.get("cluster_score") or 0.5)

            members = [per_review[i] for i in member_idx if 0 <= i < total_reviews]
            cl_plats = Counter(m.get("platform") for m in members if m.get("platform"))
            stars = [s for s in (_stars_of(m) for m in members) if s is not None]

            volume = _cluster_volume(size, total_reviews)
            agreement = max(0.0, min(1.0, cohesion))
            if total_platforms > 1:
                diversity = len(cl_plats) / max(1, total_platforms)
            else:
                diversity = 0.5
            confidence = _clamp(volume * 0.3 + agreement * 0.4 + diversity * 0.3)
            cluster_confidences[cid] = confidence

            cl["_evidence"] = {
                "confidence": round(confidence, 2),
                "confidence_label": _conf_label(confidence),
                "review_count": size,
                "review_indices": member_idx,
                "platform_spread": dict(cl_plats),
                "conflict": detect_conflict(stars),
            }

    # ---- 2. Aspects (ABSA) — often empty; enrich only when present ----
    absa = overview.get("aspect_sentiment")
    if isinstance(absa, dict) and isinstance(absa.get("aspects"), list):
        for asp in absa["aspects"]:
            if not isinstance(asp, dict):
                continue
            mentions = int(asp.get("mention_count") or asp.get("mentions")
                           or asp.get("count") or 0)
            # Agreement from any per-aspect star spread we can find; else None.
            scores = asp.get("star_ratings") or asp.get("scores")
            agreement: Optional[float] = None
            conflict = {"polarized": False, "variance": 0.0}
            if isinstance(scores, list) and len(scores) >= 2:
                numeric = [float(s) for s in scores if isinstance(s, (int, float))]
                if len(numeric) >= 2:
                    try:
                        std = statistics.stdev(numeric)
                        agreement = max(0.0, 1.0 - std / 2.0)
                    except statistics.StatisticsError:
                        agreement = None
                    conflict = detect_conflict(numeric)
            confidence = _aspect_confidence(mentions, total_reviews, agreement)
            asp["_evidence"] = {
                "confidence": round(confidence, 2),
                "confidence_label": _conf_label(confidence),
                "review_count": mentions,
                "conflict": conflict,
            }

    # ---- 3. Roadmap items — derive from their source cluster's confidence ----
    PROJECTION_DECAY = 0.7
    roadmap = overview.get("next_version_roadmap")
    if isinstance(roadmap, list):
        for item in roadmap:
            if not isinstance(item, dict):
                continue
            try:
                cid = int(item.get("cluster_id", -1))
            except (TypeError, ValueError):
                cid = -1
            cluster_conf = cluster_confidences.get(cid, 0.5)
            confidence = _clamp(cluster_conf * PROJECTION_DECAY)
            mentions = int(item.get("mentions") or 0)
            item["_evidence"] = {
                "confidence": round(confidence, 2),
                "confidence_label": _conf_label(confidence),
                "note": (f"Projection based on {mentions} review(s) "
                         f"(source cluster confidence: {round(cluster_conf, 2)})"),
            }

    # ---- 4. Overall analysis confidence ----
    overview["_analysis_confidence"] = _overall_confidence(
        total_reviews, total_platforms, cluster_confidences, clusters)

    return overview


def _overall_confidence(total_reviews: int, platform_count: int,
                        cluster_confidences: Dict[int, float],
                        clusters: Any) -> Dict[str, Any]:
    sample_factor = min(1.0, math.log2(max(1, total_reviews)) / math.log2(100)) if total_reviews > 1 else 0.0
    platform_factor = min(1.0, platform_count / 3.0)

    confs = sorted(cluster_confidences.values(), reverse=True)
    top = confs[:5]
    avg_top = (sum(top) / len(top)) if top else 0.0
    overall = _clamp(sample_factor * 0.35 + platform_factor * 0.15 + avg_top * 0.5)

    statement = _confidence_statement(total_reviews, platform_count,
                                      cluster_confidences, clusters)
    return {
        "overall": round(overall, 2),
        "label": _overall_label(overall),
        "factors": {
            "sample_size": {"value": total_reviews, "score": round(sample_factor, 2)},
            "platform_diversity": {"value": platform_count, "score": round(platform_factor, 2)},
            "signal_strength": {"value": round(avg_top, 2), "score": round(avg_top, 2)},
        },
        "statement": statement,
    }


def _confidence_statement(total_reviews: int, platform_count: int,
                          cluster_confidences: Dict[int, float],
                          clusters: Any) -> str:
    plat_word = "platform" if platform_count == 1 else "platforms"
    base = f"Based on {total_reviews} review{'s' if total_reviews != 1 else ''} from {platform_count} {plat_word}."
    if not isinstance(clusters, list) or not clusters or not cluster_confidences:
        return base
    # name strongest / weakest cluster by confidence
    by_cid = {}
    for cl in clusters:
        if isinstance(cl, dict):
            try:
                by_cid[int(cl.get("cluster_id", cl.get("id", 0)))] = cl
            except (TypeError, ValueError):
                continue
    ordered = sorted(cluster_confidences.items(), key=lambda kv: kv[1], reverse=True)
    if not ordered:
        return base
    top_cid, top_conf = ordered[0]
    parts = [base]
    top_name = (by_cid.get(top_cid) or {}).get("reason")
    if top_name:
        parts.append(f"Strongest signal: {top_name} ({round(top_conf, 2)}).")
    if len(ordered) > 1:
        low_cid, low_conf = ordered[-1]
        low_cl = by_cid.get(low_cid) or {}
        low_name = low_cl.get("reason")
        low_count = low_cl.get("count")
        if low_name:
            tail = f"Weakest: {low_name} ({round(low_conf, 2)}"
            if low_count:
                tail += f", only {low_count} review{'s' if low_count != 1 else ''}"
            tail += ")."
            parts.append(tail)
    return " ".join(parts)
