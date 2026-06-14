# backend/utils/roadmap.py
"""
Next-version improvement roadmap builder.

Takes the canonical_clusters from a finished analysis (each cluster already has
a `solution` block from `backend/insight/solutions/generator.py`) and produces
a prioritized, decorated list of recommended improvements for the next product
version. This is what powers the "Next-version improvements" card in Company
mode of the dashboard.

We don't re-run any model here — pure aggregation + scoring on existing data.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _impact_label(share_pct: float) -> str:
    if share_pct >= 18:
        return "high"
    if share_pct >= 9:
        return "medium"
    return "low"


def _effort_guess(theme_bullets: List[str], reason: str) -> str:
    """Crude effort estimate based on lexical hints. Conservative defaults to 'medium'."""
    blob = (" ".join(theme_bullets) + " " + (reason or "")).lower()
    # Quick wins
    quick_hints = ["faq", "doc", "tooltip", "banner", "copy", "in-app note", "warning", "label", "rename"]
    # Hard / hardware changes
    hard_hints = ["hardware", "redesign", "battery", "physical", "chassis", "module", "form factor", "lens", "circuit"]
    if any(h in blob for h in hard_hints):
        return "high"
    if any(h in blob for h in quick_hints):
        return "low"
    return "medium"


def _priority_score(share_pct: float, complaints: int, support: float, high_risk: bool, confidence: float, severity_tier: str = "MEDIUM") -> float:
    """
    A simple, transparent priority number (0..1) so the UI can rank.
    Weighted blend: share is the dominant driver, support and confidence finetune,
    high_risk and severity tier add bonuses.
    """
    s = 0.0
    s += min(1.0, share_pct / 30.0) * 0.45              # share of voice (capped at 30%)
    s += min(1.0, complaints / 50.0) * 0.15               # raw volume signal
    s += float(support or 0.0) * 0.10                     # how concentrated the cluster is
    s += float(confidence or 0.0) * 0.10                  # solution confidence
    if high_risk:
        s += 0.10
    # Severity bonus — critical issues should always rise to the top
    sev_bonus = {"CRITICAL": 0.25, "HIGH": 0.15, "MEDIUM": 0.05, "LOW": 0.0}.get((severity_tier or "MEDIUM").upper(), 0.05)
    s += sev_bonus
    return round(min(1.0, s), 3)


def build_next_version_roadmap(canonical_clusters: List[Dict[str, Any]], *, max_items: int = 6) -> List[Dict[str, Any]]:
    """
    Turn canonical_clusters into a ranked next-version roadmap.

    Returns a list of items:
      {
        "rank": int,
        "complaint": str,              # the cluster reason
        "share_pct": float,             # % of reviews mentioning this
        "mentions": int,                # raw count
        "impact": "high" | "medium" | "low",
        "effort": "high" | "medium" | "low",
        "priority": float,              # 0..1, used to sort
        "high_risk": bool,
        "suggested_actions": [str],     # concrete next-version improvements (from solution.bullets)
        "backlog_note": str | None,
        "confidence": float,            # solution confidence
        "sources": [{path, preview}],   # RAG sources, if any
        "sample_quote": str | None,     # one verbatim user quote
      }
    """
    if not isinstance(canonical_clusters, list) or not canonical_clusters:
        return []

    items: List[Dict[str, Any]] = []
    for c in canonical_clusters:
        sol = c.get("solution") or {}
        bullets = sol.get("bullets") or []
        # Skip clusters where the solution generator produced nothing useful
        # (these are usually neutral/praise clusters or below the complaint threshold)
        if not bullets:
            continue

        share = float(c.get("share_%", 0) or 0)
        count = int(c.get("count", 0) or 0)
        support = float(c.get("support", 0.0) or 0.0)
        high_risk = bool(sol.get("high_risk", False))
        confidence = float(sol.get("confidence", 0.0) or 0.0)
        severity_obj = c.get("severity") or {}
        severity_tier = severity_obj.get("severity") or "MEDIUM"
        is_safety = bool(severity_obj.get("is_safety"))
        is_a11y = bool(severity_obj.get("is_accessibility"))

        quotes = c.get("quotes") or []
        sample_q = None
        for q in quotes[:3]:
            if isinstance(q, str) and q.strip():
                sample_q = q.strip()
                break
            if isinstance(q, dict) and isinstance(q.get("quote"), str):
                sample_q = q["quote"].strip()
                break

        items.append({
            "complaint": c.get("reason") or "Unnamed complaint",
            "share_pct": round(share, 1),
            "mentions": count,
            "impact": _impact_label(share),
            "effort": _effort_guess(bullets, c.get("reason") or ""),
            "priority": _priority_score(share, count, support, high_risk, confidence, severity_tier),
            "high_risk": high_risk,
            "severity": severity_tier,
            "is_safety": is_safety,
            "is_accessibility": is_a11y,
            "suggested_actions": list(bullets)[:3],
            "backlog_note": sol.get("backlog"),
            "confidence": round(confidence, 2),
            "sources": list(sol.get("source") or []),
            "sample_quote": sample_q,
            "cluster_id": c.get("cluster_id"),
        })

    items.sort(key=lambda x: (-x["priority"], -x["share_pct"]))
    for i, it in enumerate(items[:max_items], start=1):
        it["rank"] = i
    return items[:max_items]


def what_users_love(per_review: List[Dict[str, Any]], *, max_items: int = 4) -> List[Dict[str, Any]]:
    """
    Consumer-facing aggregator: surface the positive themes — where reviewers
    expressed genuine praise. Returns up to `max_items` themes with mention
    counts and a representative quote.

    IMPORTANT (honesty fix): the theme for a praise item must describe what was
    PRAISED, not the shared cluster label. The cluster's `canonical_reason` is
    the same string for every review in the cluster regardless of category, so
    using it here caused a praise theme and a complaint theme to share the exact
    same name — which then rendered the SAME item in both "What people love" and
    "Things to watch out for" simultaneously. We therefore prefer the per-review
    LLM praise reason / keyphrase, and only fall back to the cluster label when
    the cluster is genuinely praise-dominated.
    """
    if not isinstance(per_review, list) or not per_review:
        return []

    # Determine each cluster's dominant category so we don't borrow a
    # complaint-dominated cluster's label as a "loved" theme.
    from collections import Counter
    cluster_cats: Dict[Any, Counter] = {}
    for r in per_review:
        cid = r.get("cluster_id")
        if cid is None:
            continue
        cluster_cats.setdefault(cid, Counter())[(r.get("review_category") or "Neutral")] += 1

    def _cluster_is_praise_dominated(cid: Any) -> bool:
        cc = cluster_cats.get(cid)
        if not cc:
            return False
        return cc.most_common(1)[0][0] == "Praise"

    # Theme priority for a praise review:
    #   1) LLM understanding_reason (clean, specific, per-review)
    #   2) top keyphrase
    #   3) cluster canonical_reason ONLY if that cluster is praise-dominated
    theme_counts: Dict[str, Dict[str, Any]] = {}
    for r in per_review:
        if r.get("review_category") != "Praise":
            continue
        theme = (r.get("understanding_reason") or "").strip()
        if not theme:
            kps = r.get("keyphrases") or []
            theme = (kps[0].strip() if kps and isinstance(kps[0], str) else "")
        if not theme and _cluster_is_praise_dominated(r.get("cluster_id")):
            theme = (r.get("canonical_reason") or "").strip()
        if not theme:
            # No trustworthy positive theme for this review — skip rather than
            # mislabel it with a complaint cluster's name.
            continue
        key = theme.lower()
        bucket = theme_counts.setdefault(key, {"theme": theme, "count": 0, "quotes": []})
        bucket["count"] += 1
        if len(bucket["quotes"]) < 2 and r.get("original"):
            bucket["quotes"].append(r["original"][:240])

    ranked = sorted(theme_counts.values(), key=lambda x: -x["count"])
    return ranked[:max_items]
