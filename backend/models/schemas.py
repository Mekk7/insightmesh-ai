# backend/models/schemas.py
"""
Shared Pydantic models for InsightMesh AI.

Currently empty — schemas live next to their endpoints (e.g., ReviewsInput in
analyze_reviews.py, RunInput in run_pipeline.py). When schemas start being
shared across more than one endpoint, move them here.

Planned future schemas:
    - RunResult: the canonical "final_report" shape (currently a dict)
    - ClusterBlock: canonical_clusters item shape
    - PerReviewItem: row shape inside analysis.per_review
"""
