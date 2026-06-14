# backend/utils/astroturf.py
"""
Heuristic detection of coordinated/spammed reviews.

This is not a deep classifier; it's a fast pattern-matcher that flags
two common attack shapes in product-review datasets:

  1) **Same lexical signature, multiple comments**
     Reviewers using a copy-paste template (or a botnet doing it for them)
     produce comments whose normalized token-set looks nearly identical.
     If 3+ comments share a signature inside the same batch, flag.

  2) **Same author, many comments**
     One author posting many separate comments about the same product is
     either a superfan or a sockpuppet farm. Both deserve attention.

  3) **Burst timing on same signature**
     A spike of identical/near-identical comments inside a short window
     (24h) — strongest astroturf signal.

The output is informational, never destructive. We don't drop comments;
we surface a flag on the dashboard so a human can decide.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import re


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_STOP = {
    "the", "a", "an", "is", "it", "was", "are", "this", "that", "and", "or",
    "but", "for", "to", "of", "in", "on", "at", "by", "with", "as", "i", "you",
    "he", "she", "we", "they", "my", "your", "our", "their", "be", "been",
    "have", "has", "had", "do", "does", "did", "so", "if", "not", "no", "yes",
}


def _signature(text: str, n_tokens: int = 8) -> str:
    """
    Build a normalized token-set signature. Same words in any order → same signature.
    Drops stopwords and very short tokens; takes the alphabetized first-N unique tokens.
    """
    if not text:
        return ""
    t = _PUNCT_RE.sub(" ", text.lower())
    t = _WS_RE.sub(" ", t).strip()
    toks = [w for w in t.split() if len(w) > 2 and w not in _STOP]
    if len(toks) < 3:
        return ""
    unique_sorted = sorted(set(toks))[:n_tokens]
    return " ".join(unique_sorted)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _max_window_hours(dts: List[datetime]) -> float:
    """Return the span (in hours) of the *tightest* cluster of timestamps via simple min/max."""
    if not dts:
        return 0.0
    dts_sorted = sorted(dts)
    span = (dts_sorted[-1] - dts_sorted[0]).total_seconds() / 3600.0
    return round(span, 2)


def detect_astroturf(per_review: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scan `per_review` entries (with attached metadata) and return:
      {
        "flag": bool,                    # at least one signal is suspicious
        "summary": str,                  # human-readable headline
        "suspicious_clusters": [...],    # lexical-signature clusters
        "repeat_authors": [...],         # authors with too many comments
      }
    """
    if not isinstance(per_review, list) or len(per_review) < 3:
        return {"flag": False, "summary": "Not enough comments to assess", "suspicious_clusters": [], "repeat_authors": []}

    # ---- Lexical signatures ----
    sig_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, r in enumerate(per_review):
        sig = _signature(r.get("original") or "")
        if sig:
            sig_to_indices[sig].append(i)

    suspicious_clusters: List[Dict[str, Any]] = []
    for sig, idxs in sig_to_indices.items():
        if len(idxs) < 3:
            continue
        # Look at timestamps if available
        dts = [_parse_iso(per_review[i].get("published_at")) for i in idxs]
        dts = [d for d in dts if d is not None]
        burst_h = _max_window_hours(dts) if dts else None
        authors = sorted({per_review[i].get("author") for i in idxs if per_review[i].get("author")})
        suspicious_clusters.append({
            "signature": sig,
            "count": len(idxs),
            "unique_authors": len(authors),
            "burst_window_hours": burst_h,
            "sample": (per_review[idxs[0]].get("original") or "")[:240],
        })

    suspicious_clusters.sort(key=lambda c: (-c["count"], -(c.get("unique_authors") or 0)))

    # ---- Repeat-author signal ----
    author_counts = Counter(r.get("author") for r in per_review if r.get("author"))
    repeat_authors = [
        {"author": a, "count": c}
        for a, c in author_counts.most_common(5)
        if a and c >= 3
    ]

    # ---- Final flag + summary ----
    flag = bool(suspicious_clusters) or bool(repeat_authors)
    if suspicious_clusters and any((c.get("burst_window_hours") or 999) <= 24 for c in suspicious_clusters):
        summary = (
            f"Possible coordinated review activity: {len(suspicious_clusters)} lexical clusters "
            f"with burst timing under 24h. Review the suspicious_clusters list before publishing."
        )
    elif suspicious_clusters:
        summary = f"{len(suspicious_clusters)} comment clusters share near-identical wording. May be templated reviews."
    elif repeat_authors:
        summary = f"{len(repeat_authors)} authors posted 3+ comments in this batch. Could be superfans or sockpuppets."
    else:
        summary = "No coordinated-review patterns detected."

    return {
        "flag": flag,
        "summary": summary,
        "suspicious_clusters": suspicious_clusters[:5],
        "repeat_authors": repeat_authors,
    }
