# backend/insight/deep_classify/__init__.py
from backend.insight.deep_classify.classifier import (
    deep_classify_reviews,
    aggregate_deep_signals,
)

__all__ = ["deep_classify_reviews", "aggregate_deep_signals"]
