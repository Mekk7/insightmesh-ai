# backend/insight/counterfactual/impact.py
"""
Counterfactual Impact Analyzer.

For each item on the next-version roadmap, answer:
  "If you fixed this, what would happen to overall sentiment and TrustScore?"

The roadmap currently shows complaints ranked by priority. The Company team
asks the obvious next question: "what do I get if I fix #1 vs #2 vs #3?" This
module quantifies that.

Method:

  For each complaint cluster C with share_pct% of reviews:
    Assume fixing C converts that cluster's negative reviews into neutral/mildly
    positive ones (3.5★ instead of their current ~2★). We model:

      projected_avg_sentiment  = (avg_sentiment_now * (1 - fix_share)) + (recovered_sentiment * fix_share)
      projected_mood_delta     = projected_avg − avg_now
      projected_trust_delta    = severity_weighted_uplift

    Where:
      fix_share          = share_pct / 100  (the slice of reviews "recovered")
      recovered_sentiment = 3.5 (assumes fix turns a 2★ complaint into a mildly positive 3.5★)
      severity_weight    = CRITICAL→1.5x, HIGH→1.2x, MEDIUM→1.0x, LOW→0.7x

  We then compute a confidence interval based on cluster cohesion (support),
  solution confidence, and sample size — wider CI when data is noisier.

This is heuristic, not a randomized causal estimate (that's impossible without
A/B). But it gives the team a *directional* answer in the same units they care
about (stars, mood, TrustScore points). That's significantly more useful than
ranked-list-of-complaints.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _severity_weight(severity_tier: str) -> float:
    return {
        "CRITICAL": 1.5,
        "HIGH": 1.2,
        "MEDIUM": 1.0,
        "LOW": 0.7,
    }.get((severity_tier or "MEDIUM").upper(), 1.0)


def _confidence_band(cluster: Dict[str, Any], roadmap_item: Dict[str, Any]) -> float:
    """
    Wider band when the cluster is small, weakly cohesive, or low-confidence solution.
    Returns the +/- value (in stars) for the projected sentiment.
    """
    support = _safe_float(cluster.get("support", 0.5), 0.5)
    sol_confidence = _safe_float(roadmap_item.get("confidence", 0.5), 0.5)
    mentions = max(1, int(cluster.get("count", 1) or 1))

    # Base width 0.20★. Tighten with good signals.
    band = 0.20
    band -= 0.07 * support           # cohesive cluster → tighter
    band -= 0.05 * sol_confidence     # confident fix → tighter
    band -= min(0.05, mentions / 1000)  # large cluster → tighter
    return round(max(0.04, band), 3)


def compute_counterfactuals(
    *,
    roadmap_items: List[Dict[str, Any]],
    canonical_clusters: List[Dict[str, Any]],
    average_sentiment: Optional[float],
    current_trust_score: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Returns roadmap_items with an added `counterfactual` block on each:

      {
        "projected_avg_sentiment": 4.34,
        "sentiment_delta": +0.24,            # vs current avg
        "trust_delta": +6.2,                  # estimated TrustScore points gained
        "sentiment_ci": [0.16, 0.32],         # +/- band, in stars
        "narrative": "Fixing this could lift overall sentiment by ~0.24★ (4.10 → 4.34)"
      }

    Also returns the "cumulative if you fixed top-3" scenario as a separate
    summary item the dashboard can render below the per-item list.
    """
    if not roadmap_items or not canonical_clusters or average_sentiment is None:
        return roadmap_items

    # Build cluster lookup by id
    cluster_by_id = {int(c.get("cluster_id", -1)): c for c in canonical_clusters}

    base_avg = _safe_float(average_sentiment, 3.0)
    recovered = 3.5  # Assumed sentiment after a fix turns the complaint into a mild positive

    enriched: List[Dict[str, Any]] = []
    for item in roadmap_items:
        cid = item.get("cluster_id")
        cluster = cluster_by_id.get(int(cid) if cid is not None else -1)
        share = _safe_float(item.get("share_pct", 0)) / 100.0
        severity_tier = (item.get("severity") or "MEDIUM").upper()
        weight = _severity_weight(severity_tier)

        # Project the new average assuming the fix recovers `share` of the corpus
        # from current to `recovered`. We model the cluster's current contribution
        # as roughly mirroring the complaint share scaled by severity weight.
        effective_share = min(0.6, share * weight)  # cap so a single fix isn't oversold
        projected_avg = base_avg * (1 - effective_share) + recovered * effective_share
        # The recovered uplift can't go below 0
        delta = max(0.0, projected_avg - base_avg)
        band = _confidence_band(cluster or {}, item)

        # TrustScore delta — rough mapping: each +1★ ≈ 8.75 TrustScore points (baseline sentiment cap is 35 over 4★)
        # Plus a small bonus if severity was CRITICAL (severity tax was high)
        trust_delta = delta * 8.75
        if severity_tier == "CRITICAL":
            trust_delta += 8.0   # we erase up to -10 severity tax
        elif severity_tier == "HIGH":
            trust_delta += 3.0

        narrative = (
            f"Fixing this could lift overall sentiment by ~{delta:.2f}★ "
            f"({base_avg:.2f} → {projected_avg:.2f}). "
            f"Estimated TrustScore gain: +{trust_delta:.1f} points."
        )

        item = dict(item)  # don't mutate caller's data
        item["counterfactual"] = {
            "projected_avg_sentiment": round(projected_avg, 2),
            "sentiment_delta": round(delta, 2),
            "trust_delta": round(trust_delta, 1),
            "sentiment_ci": [round(max(0.0, delta - band), 2), round(delta + band, 2)],
            "severity_weight": weight,
            "narrative": narrative,
        }
        enriched.append(item)

    return enriched


def cumulative_impact(roadmap_items: List[Dict[str, Any]], *, top_k: int = 3, average_sentiment: Optional[float] = None) -> Dict[str, Any]:
    """
    What if you fixed the top-k items together? Returns:
      {
        "k": 3,
        "sentiment_delta": +0.45,
        "trust_delta": +14.2,
        "projected_avg_sentiment": 4.55,
        "narrative": "Fixing the top 3 improvements together could lift sentiment to 4.55★ (+0.45★)."
      }

    Combined deltas use a diminishing-returns curve so we don't claim 3×
    independent gains — each successive fix recovers a smaller share of the
    still-unhappy reviewers.
    """
    if not roadmap_items or average_sentiment is None:
        return {"k": 0, "sentiment_delta": 0.0, "trust_delta": 0.0, "projected_avg_sentiment": None, "narrative": ""}

    base = _safe_float(average_sentiment, 3.0)
    top = roadmap_items[:max(1, int(top_k))]

    # Diminishing returns: each subsequent fix gets a 0.7x multiplier
    discount = 1.0
    combined_share = 0.0
    for it in top:
        cf = (it.get("counterfactual") or {})
        share = _safe_float(it.get("share_pct", 0)) / 100.0
        severity = (it.get("severity") or "MEDIUM").upper()
        weight = _severity_weight(severity)
        effective = min(0.5, share * weight) * discount
        combined_share += effective
        discount *= 0.7

    combined_share = min(0.75, combined_share)
    recovered = 3.6
    projected = base * (1 - combined_share) + recovered * combined_share
    delta = max(0.0, projected - base)
    trust_delta = delta * 8.75 + sum(_severity_weight((it.get("severity") or "MEDIUM").upper()) for it in top) * 2.0

    return {
        "k": len(top),
        "sentiment_delta": round(delta, 2),
        "trust_delta": round(trust_delta, 1),
        "projected_avg_sentiment": round(projected, 2),
        "narrative": (
            f"Fixing the top {len(top)} improvements together could lift sentiment "
            f"to {projected:.2f}★ (+{delta:.2f}★) and TrustScore by +{trust_delta:.1f} points."
        ),
    }
