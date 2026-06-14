# Marks "backend.insight" as a package.
# Re-export common entry points for convenience.
from .filters.relevance import RelevanceConfig, filter_comments  # noqa: F401
from .reasons.cluster import ClusterConfig, canonical_clusters   # noqa: F401
from .solutions.generator import ClusterInput, generate_solutions  # noqa: F401

__all__ = [
    "RelevanceConfig", "filter_comments",
    "ClusterConfig", "canonical_clusters",
    "ClusterInput", "generate_solutions",
]
