# backend/insight/severity/risk_register.py
"""
Risk Register — what cannot wait for the next version.

The next-version roadmap is great for planned improvements. But some complaints
shouldn't queue up in a backlog: safety issues, data loss, accessibility blockers.
The Risk Register surfaces those separately so they get a different treatment
("address this now, regardless of share %").

Inputs: canonical_clusters with their `severity` blocks attached.

Output: a ranked list of risk items with severity tier, category (safety / data /
accessibility / reliability), suggested timeline (immediate / 30d / 90d), and the
matched cue from the cluster text — so a PM can scan it and act in 30 seconds.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _category_from_cues(severity_obj: Dict[str, Any], reason: str) -> str:
    if severity_obj.get("is_safety"):
        return "safety"
    if severity_obj.get("is_accessibility"):
        return "accessibility"
    reason_l = (reason or "").lower()
    cues = severity_obj.get("matched_cues") or []
    cue_blob = " ".join(str(c) for c in cues).lower() + " " + reason_l
    if any(w in cue_blob for w in ("data loss", "lost data", "files", "photos", "hack", "security", "privacy", "leak")):
        return "data_integrity"
    if any(w in cue_blob for w in ("crash", "fire", "smoke", "broken", "unusable", "won't work", "defective", "lemon")):
        return "reliability"
    if any(w in cue_blob for w in ("return", "refund", "rma")):
        return "returns_pressure"
    return "general"


def _timeline_for(tier: str, category: str) -> str:
    if tier == "CRITICAL" or category == "safety":
        return "immediate"
    if tier == "HIGH" or category in ("data_integrity", "accessibility"):
        return "30 days"
    if tier == "MEDIUM":
        return "90 days"
    return "backlog"


def _category_label(cat: str) -> str:
    return {
        "safety": "Safety",
        "data_integrity": "Data integrity",
        "accessibility": "Accessibility",
        "reliability": "Reliability",
        "returns_pressure": "Returns pressure",
        "general": "General risk",
    }.get(cat, cat.replace("_", " ").title())


def build_risk_register(canonical_clusters: List[Dict[str, Any]], *, max_items: int = 8) -> List[Dict[str, Any]]:
    """
    Returns a list of risk items, sorted by severity tier (CRITICAL→HIGH→MEDIUM)
    and category urgency (safety beats reliability beats general).

    Each item:
      {
        "rank": 1,
        "complaint": "Phantom braking on Autopilot",
        "severity": "CRITICAL",
        "category": "safety",
        "category_label": "Safety",
        "timeline": "immediate",
        "share_pct": 18.0,
        "mentions": 44,
        "matched_cues": ["safety", "scared", "phantom braking"],
        "sample_quote": "Phantom braking on the highway scared me...",
      }
    """
    if not canonical_clusters:
        return []

    tier_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    cat_rank = {"safety": 0, "data_integrity": 1, "accessibility": 2, "reliability": 3, "returns_pressure": 4, "general": 5}

    items: List[Dict[str, Any]] = []
    for c in canonical_clusters:
        sev = c.get("severity") or {}
        tier = (sev.get("severity") or "MEDIUM").upper()
        # Only surface MEDIUM-or-higher; LOW is roadmap material, not risk register
        if tier == "LOW":
            continue

        category = _category_from_cues(sev, c.get("reason", ""))
        # MEDIUM general-category items aren't risks worth flagging — drop them
        if tier == "MEDIUM" and category == "general":
            continue

        # Pick a sample quote
        quotes = c.get("quotes") or []
        sample = None
        for q in quotes[:3]:
            if isinstance(q, str) and q.strip():
                sample = q.strip()
                break
            if isinstance(q, dict) and isinstance(q.get("quote"), str):
                sample = q["quote"].strip()
                break

        items.append({
            "complaint": c.get("reason") or "Unnamed risk",
            "severity": tier,
            "category": category,
            "category_label": _category_label(category),
            "timeline": _timeline_for(tier, category),
            "share_pct": float(c.get("share_%", 0) or 0),
            "mentions": int(c.get("count", 0) or 0),
            "matched_cues": list(sev.get("matched_cues") or [])[:4],
            "sample_quote": (sample or "")[:240],
            "cluster_id": c.get("cluster_id"),
        })

    items.sort(key=lambda x: (tier_rank.get(x["severity"], 9), cat_rank.get(x["category"], 9), -x["share_pct"]))
    for i, it in enumerate(items[:max_items], start=1):
        it["rank"] = i
    return items[:max_items]
