# backend/insight/intelligence/temporal_detector.py
# Temporal Anomaly Detection.
#
# Scans the daily sentiment series for sharp drops — a 14-day window whose average
# star rating sits >0.8 below the preceding baseline, with enough reviews to be
# real — then attributes each drop to the dominant review theme in that window.
# Surfaces things like "Sentiment dropped 1.2 around March 15 — 8 reviews mention
# firmware update".
#
# Pure computation. No LLM, no network. Operates on data already in the overview
# (`sentiment_over_time`) plus `per_review` (for window reviews + dominant theme).

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}
_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _parse_day(ts: Any) -> Optional[date]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(t)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except Exception:
        # tolerate plain YYYY-MM-DD
        try:
            return date.fromisoformat(ts[:10])
        except Exception:
            return None


def _star_of(r: Dict[str, Any]) -> Optional[float]:
    lbl = str(r.get("sentiment") or "")
    if lbl in _STAR_TO_NUM:
        return float(_STAR_TO_NUM[lbl])
    sc = r.get("sentiment_score")
    if isinstance(sc, (int, float)):
        return 1.0 + 4.0 * float(sc)
    return None


def _pretty_date(d: date) -> str:
    return f"{_MONTHS[d.month]} {d.day}, {d.year}"


def _severity(drop: float) -> str:
    if drop >= 1.5:
        return "high"
    if drop >= 1.0:
        return "medium"
    return "low"


def detect_temporal_anomalies(
    sentiment_over_time: List[Dict[str, Any]],
    per_review: List[Dict[str, Any]],
    window_days: int = 14,
    min_drop: float = 0.8,
    min_reviews: int = 3,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Find sentiment drops and attribute them to a dominant theme.

    Returns a list (most severe first):
      {date, severity, star_drop, review_count, dominant_theme, explanation}
    Empty list when there isn't enough dated/star data to judge (fail-open).
    """
    # Dated, star-bearing reviews (carry theme for attribution).
    pts: List[Dict[str, Any]] = []
    for r in (per_review or []):
        d = _parse_day(r.get("published_at"))
        star = _star_of(r)
        if d is None or star is None:
            continue
        pts.append({
            "day": d,
            "star": star,
            "theme": (r.get("canonical_reason") or "").strip() or None,
        })
    if len(pts) < (min_reviews * 2):  # need a window AND a baseline
        return []
    pts.sort(key=lambda p: p["day"])

    # Candidate window starts = each distinct review day (bounded, data-driven).
    start_days = sorted({p["day"] for p in pts})
    raw: List[Dict[str, Any]] = []
    for ws in start_days:
        we = ws + timedelta(days=window_days)
        window = [p for p in pts if ws <= p["day"] < we]
        baseline = [p for p in pts if p["day"] < ws]
        if len(window) < min_reviews or len(baseline) < min_reviews:
            continue
        base_mean = sum(p["star"] for p in baseline) / len(baseline)
        win_mean = sum(p["star"] for p in window) / len(window)
        drop = base_mean - win_mean
        if drop <= min_drop:
            continue
        themes = Counter(p["theme"] for p in window if p["theme"])
        dominant = themes.most_common(1)[0][0] if themes else None
        raw.append({
            "date": ws.isoformat(),
            "_day": ws,
            "severity": _severity(drop),
            "star_drop": round(drop, 2),
            "review_count": len(window),
            "dominant_theme": dominant,
            "explanation": _explain(drop, ws, len(window), dominant),
        })

    if not raw:
        return []

    # Dedupe overlapping windows: keep the most severe, then suppress any other
    # anomaly whose start is within `window_days` of an already-kept one.
    raw.sort(key=lambda a: (-a["star_drop"], a["_day"]))
    kept: List[Dict[str, Any]] = []
    for a in raw:
        if any(abs((a["_day"] - k["_day"]).days) < window_days for k in kept):
            continue
        kept.append(a)
        if len(kept) >= top_k:
            break
    for a in kept:
        a.pop("_day", None)
    return kept


def _explain(drop: float, ws: date, n: int, theme: Optional[str]) -> str:
    base = f"Sentiment dropped {round(drop, 1)}★ around {_pretty_date(ws)} — {n} review{'s' if n != 1 else ''}"
    if theme:
        return f"{base} mention {theme}"
    return f"{base} in this window"
