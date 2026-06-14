# backend/insight/forecast/sentiment_predict.py
"""
Sentiment Forecast.

Takes the daily sentiment_over_time series and projects the next N days. The
forecast band shows where the product's sentiment is heading, which is hugely
useful for both:
  - Consumers: "this is improving, worth waiting" vs "this is declining, skip"
  - Companies: "we are about to cross into red territory" vs "investment is paying off"

We deliberately use a lightweight numpy-only approach (no Prophet) because:
  1. Reviews are noisy — fancy models overfit on 14-30 day windows
  2. Linear/quadratic trend + std-based CI is honest about uncertainty
  3. No new dependencies, fast startup, predictable behavior

Method:
  1. Fit a 1st-degree polynomial (linear regression) on (day_index, avg_sentiment)
  2. Compute residual standard deviation as the 1-sigma band
  3. Project `horizon_days` forward with 1.96-sigma CI (95%)
  4. Clamp to [1, 5] (star rating range)
  5. Return chronological list of {date, forecast, ci_low, ci_high}

Robust to:
  - <3 data points → returns empty forecast (no overfitting on tiny samples)
  - All-same values → flat forecast, narrow CI
  - Missing avg_sentiment values → skips them
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept "2026-05-23" and "2026-05-23T..." forms
        return datetime.fromisoformat(s.split("T")[0]).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def forecast_sentiment(
    sentiment_over_time: List[Dict[str, Any]],
    *,
    horizon_days: int = 14,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "forecast": [{date, forecast, ci_low, ci_high, n: 0}],   # future points
        "trend": "rising" | "falling" | "stable",
        "trend_strength": float,        # |slope| per day
        "narrative": str,
        "method": "linear_ci_95",
        "fit": {"slope": ..., "intercept": ..., "residual_std": ...},
      }
    """
    out_empty = {
        "forecast": [],
        "trend": "stable",
        "trend_strength": 0.0,
        "narrative": "Not enough timestamped data to project a trend.",
        "method": "linear_ci_95",
        "fit": None,
    }

    if not sentiment_over_time or len(sentiment_over_time) < 3:
        return out_empty

    # --- Build (x, y) where x = days since first point ---
    pairs: List = []
    first_date: Optional[datetime] = None
    for pt in sentiment_over_time:
        d = _parse_date(pt.get("date"))
        y = pt.get("avg_sentiment")
        if d is None or y is None:
            continue
        if first_date is None:
            first_date = d
        x = (d - first_date).days
        try:
            pairs.append((float(x), float(y)))
        except Exception:
            continue

    if len(pairs) < 3:
        return out_empty

    try:
        import numpy as np  # local import so module loads without numpy
    except Exception:
        return out_empty

    xs = np.array([p[0] for p in pairs], dtype=float)
    ys = np.array([p[1] for p in pairs], dtype=float)

    # --- Linear fit ---
    try:
        slope, intercept = np.polyfit(xs, ys, deg=1)
    except Exception:
        return out_empty

    fitted = slope * xs + intercept
    residuals = ys - fitted
    resid_std = float(np.std(residuals)) if len(residuals) > 1 else 0.15
    # 95% CI ≈ 1.96 sigma; we add a small floor so the band is visible
    band = max(0.08, 1.96 * resid_std)

    # --- Project horizon_days forward from the last actual date ---
    last_date = first_date + timedelta(days=int(xs.max()))
    horizon_days = max(3, min(int(horizon_days), 60))
    forecast: List[Dict[str, Any]] = []
    for i in range(1, horizon_days + 1):
        future_x = float(xs.max() + i)
        y_hat = float(slope * future_x + intercept)
        # Clamp to star range
        y_hat_c = max(1.0, min(5.0, y_hat))
        low = max(1.0, y_hat - band)
        high = min(5.0, y_hat + band)
        forecast.append({
            "date": (last_date + timedelta(days=i)).date().isoformat(),
            "forecast": round(y_hat_c, 2),
            "ci_low": round(low, 2),
            "ci_high": round(high, 2),
            "n": 0,  # no real comments yet
            "is_forecast": True,
        })

    # --- Trend classification ---
    # slope is stars/day. Multiply by 7 to express weekly delta.
    weekly = slope * 7
    if weekly >= 0.05:
        trend = "rising"
    elif weekly <= -0.05:
        trend = "falling"
    else:
        trend = "stable"
    strength = abs(weekly)

    # --- Narrative ---
    final_avg = forecast[-1]["forecast"]
    current_avg = float(ys[-1])
    if trend == "rising":
        narrative = f"Sentiment trending up ~{weekly:+.2f}★/week. Projected to reach {final_avg:.2f}★ in {horizon_days} days."
    elif trend == "falling":
        narrative = f"Sentiment trending down ~{weekly:+.2f}★/week. If unchecked, projected at {final_avg:.2f}★ in {horizon_days} days."
    else:
        narrative = f"Sentiment stable around {current_avg:.2f}★. No significant trend either way."

    return {
        "forecast": forecast,
        "trend": trend,
        "trend_strength": round(strength, 4),
        "narrative": narrative,
        "method": "linear_ci_95",
        "fit": {
            "slope_per_day": round(float(slope), 4),
            "slope_per_week": round(float(weekly), 4),
            "intercept": round(float(intercept), 3),
            "residual_std": round(resid_std, 3),
            "n_points": len(pairs),
        },
    }
