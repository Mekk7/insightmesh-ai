# backend/insight/priorities/reweight.py
"""
Personal Priorities Reweighter.

The killer consumer feature: the user picks 2-4 aspects they actually care
about ("battery", "design", "price") — and the whole dashboard re-weights
around those. Their TrustScore changes. Their verdict changes. "Things to
watch out for" surfaces only the aspects they care about.

This is mostly a frontend-driven reweight (the React layer recomputes from
existing overview.aspect_sentiment + canonical_clusters), but this module
provides the backend reweight helper that's exposed as an API endpoint so:
  - We can compute "decision flip" insights ("buying would flip to wait if
    you prioritize battery")
  - The math is shared and consistent
  - Future personalization (per-user saved priorities) can plug into the same
    function

Public API:
  reweight_for_priorities(overview, priorities) -> {
    "personalized_trust_score": {...},   # new TrustScore for the user
    "decision_flip": str | None,         # if priorities flip the verdict
    "highlighted_aspects": [...],        # only the ones in their priorities
    "matters_to_you": [...],             # what they should pay attention to
  }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Re-use the TrustScore engine — we just feed it filtered inputs
try:
    from backend.insight.trust.score import compute_trust_score
except Exception:
    compute_trust_score = None


def _filter_aspects(aspect_sentiment: Dict[str, Any], priorities: List[str]) -> Dict[str, Any]:
    """Return aspect_sentiment containing only the user's priority aspects."""
    if not aspect_sentiment or not priorities:
        return {"domain": (aspect_sentiment or {}).get("domain"), "aspects": []}
    priorities_lower = {p.strip().lower() for p in priorities if p and p.strip()}
    aspects = (aspect_sentiment or {}).get("aspects") or []
    matched = [a for a in aspects if (a.get("aspect", "").lower() in priorities_lower)]
    return {"domain": aspect_sentiment.get("domain"), "aspects": matched}


def _weighted_sentiment_for_priorities(
    overview_average: Optional[float],
    aspects: List[Dict[str, Any]],
) -> Optional[float]:
    """
    Compute a sentiment number weighted by the user's priorities:
      - if the user picked aspects we have ABSA data on, use the mention-weighted
        avg of just those aspects' avg_sentiment_stars
      - otherwise fall back to the overview's average
    """
    if not aspects:
        return overview_average
    weighted_sum = 0.0
    total_mentions = 0
    for a in aspects:
        stars = a.get("avg_sentiment_stars")
        mentions = int(a.get("mentions", 0) or 0)
        if stars is None or mentions <= 0:
            continue
        weighted_sum += float(stars) * mentions
        total_mentions += mentions
    if total_mentions == 0:
        return overview_average
    return round(weighted_sum / total_mentions, 2)


def _detect_decision_flip(
    baseline_score: float,
    personalized_score: float,
    aspects: List[Dict[str, Any]],
) -> Optional[str]:
    """Generate a narrative if the user's priorities meaningfully change the picture."""
    delta = personalized_score - baseline_score
    if abs(delta) < 8:
        return None
    bad_aspects = [a for a in aspects if (a.get("avg_sentiment_stars") or 5) < 3]
    good_aspects = [a for a in aspects if (a.get("avg_sentiment_stars") or 0) >= 4]
    if delta <= -8:
        # User's picks pull the score DOWN — they're paying attention to weak spots
        focus = bad_aspects[0]["aspect"] if bad_aspects else "the aspects you care about"
        return (
            f"What you care about pulls the picture down. "
            f"This product is weakest in {focus} — the overall verdict may not apply to you."
        )
    if delta >= 8:
        # User's picks pull it UP — they're focused on strengths
        focus = good_aspects[0]["aspect"] if good_aspects else "the aspects you care about"
        return (
            f"What you care about is where this product shines. "
            f"It's strongest in {focus} — better fit for you than the overall score suggests."
        )
    return None


def reweight_for_priorities(
    overview: Dict[str, Any],
    priorities: List[str],
) -> Dict[str, Any]:
    """
    Recompute TrustScore + decision flip based on user's stated priorities.

    Args:
      overview: the full overview dict from a final_report
      priorities: list of aspect names the user cares about, e.g.
                  ["battery", "price", "design"]

    Returns:
      {
        "personalized_trust_score": {...} | None,
        "decision_flip": str | None,
        "matters_to_you": [aspect dicts the user picked, with ABSA data],
        "missing_priorities": [user-stated priorities we have no data on],
      }
    """
    if not overview or not priorities:
        return {
            "personalized_trust_score": None,
            "decision_flip": None,
            "matters_to_you": [],
            "missing_priorities": list(priorities or []),
        }

    aspect_sentiment = overview.get("aspect_sentiment") or {"aspects": []}
    matched = _filter_aspects(aspect_sentiment, priorities)
    matched_aspects = matched.get("aspects") or []
    matched_keys = {a.get("aspect", "").lower() for a in matched_aspects}
    missing = [p for p in priorities if p.lower() not in matched_keys]

    # Reweighted sentiment for TrustScore baseline
    base_avg = overview.get("average_sentiment")
    weighted_avg = _weighted_sentiment_for_priorities(base_avg, matched_aspects)

    personalized_score = None
    if compute_trust_score:
        try:
            personalized_score = compute_trust_score(
                average_sentiment=weighted_avg,
                sample_size=(overview.get("sarcasm_stats") or {}).get("total", 0),
                language_count=len(overview.get("language_distribution") or {}),
                astroturf_flag=bool((overview.get("astroturf_signals") or {}).get("flag")),
                sarcasm_stats=overview.get("sarcasm_stats"),
                decision_health=(overview.get("buyer_intent_summary") or {}).get("decision_health"),
                # Only count clusters whose reason touches a priority aspect
                canonical_clusters=[
                    c for c in (overview.get("canonical_clusters") or [])
                    if any(p.lower() in (c.get("reason") or "").lower() for p in priorities)
                ] or overview.get("canonical_clusters"),
                sentiment_over_time=overview.get("sentiment_over_time"),
            )
        except Exception:
            personalized_score = None

    # Decision flip narrative
    baseline = (overview.get("trust_score") or {}).get("score") if overview.get("trust_score") else None
    flip = None
    if personalized_score and baseline is not None:
        flip = _detect_decision_flip(
            baseline,
            personalized_score.get("score", baseline),
            matched_aspects,
        )

    return {
        "personalized_trust_score": personalized_score,
        "decision_flip": flip,
        "matters_to_you": matched_aspects,
        "missing_priorities": missing,
    }
