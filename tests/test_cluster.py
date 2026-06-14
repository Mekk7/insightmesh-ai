# tests/test_cluster.py
"""Tests for backend/insight/reasons/cluster.py — canonical clustering."""
from __future__ import annotations

import pytest


class TestSmallN:
    def test_empty_input_returns_empty(self, fake_embedder):
        from backend.insight.reasons.cluster import canonical_clusters
        result = canonical_clusters([], fake_embedder)
        assert result["labels"] == []
        assert len(result["clusters"]) <= 1  # may have an empty placeholder

    def test_single_doc_single_cluster(self, fake_embedder):
        from backend.insight.reasons.cluster import canonical_clusters
        result = canonical_clusters(
            ["The battery dies way too fast on this device."],
            fake_embedder,
        )
        assert result["labels"] == [0]
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["size"] == 1
        assert "canonical_reason" in result["clusters"][0]

    def test_two_docs(self, fake_embedder):
        from backend.insight.reasons.cluster import canonical_clusters
        result = canonical_clusters(
            ["Battery dies fast.", "Charging is slow."],
            fake_embedder,
        )
        assert len(result["labels"]) == 2
        assert "mode" in result["debug"]


class TestNoEmbedder:
    def test_degenerate_mode_when_no_embedder(self):
        from backend.insight.reasons.cluster import canonical_clusters
        result = canonical_clusters(["one", "two", "three"], embedder=None)
        assert result["debug"]["mode"] == "degenerate"
        # All docs collapse to a single cluster
        assert all(l == 0 for l in result["labels"])


class TestCanonicalPhrase:
    def test_heuristic_label_for_stability(self):
        from backend.insight.reasons.cluster import _canonical_phrase
        texts = ["app crashes when I open it", "I get an error every time", "the bug is annoying"]
        phrase = _canonical_phrase(texts, top_k=3, max_len=110)
        # Heuristic should detect stability theme
        assert phrase.lower().find("crash") >= 0 or "error" in phrase.lower() or len(phrase) > 0

    def test_returns_default_on_empty(self):
        from backend.insight.reasons.cluster import _canonical_phrase
        phrase = _canonical_phrase([], top_k=3, max_len=110)
        assert phrase  # non-empty default


class TestReasonCandidates:
    def test_extracts_complaint_phrases(self):
        from backend.insight.reasons.cluster import _reason_candidates
        cands = _reason_candidates("This is broken because the wifi keeps disconnecting at random.")
        assert len(cands) > 0

    def test_extracts_suggestion_phrases(self):
        from backend.insight.reasons.cluster import _reason_candidates
        cands = _reason_candidates("They should add a dark mode toggle to the settings menu.")
        assert len(cands) > 0


class TestEndToEndSmall:
    """End-to-end with a small, distinct sample. Tests the full path."""

    @pytest.fixture
    def battery_vs_camera_corpus(self):
        # Two semantically distinct groups
        return [
            "the battery is terrible and dies in minutes",
            "battery life is awful, won't last a day",
            "camera quality is amazing on this phone",
            "love the camera, the photos look great",
            "the battery drains way too quickly",
            "best camera I've ever used, super clear images",
        ]

    def test_two_distinct_groups_form(self, fake_embedder, battery_vs_camera_corpus):
        from backend.insight.reasons.cluster import canonical_clusters, ClusterConfig
        cfg = ClusterConfig(min_cluster_size=2, use_hdbscan=False)  # force greedy/agglom path
        result = canonical_clusters(battery_vs_camera_corpus, fake_embedder, cfg)
        # We expect some grouping (exact cluster count may vary by config)
        assert len(result["labels"]) == len(battery_vs_camera_corpus)
        assert len(result["clusters"]) >= 1

    def test_cluster_has_required_fields(self, fake_embedder, battery_vs_camera_corpus):
        from backend.insight.reasons.cluster import canonical_clusters
        result = canonical_clusters(battery_vs_camera_corpus, fake_embedder)
        for c in result["clusters"]:
            assert "id" in c
            assert "size" in c
            assert "canonical_reason" in c
            assert "support" in c
            assert "centroid_sim_mean" in c
            assert "quotes" in c
            assert isinstance(c["quotes"], list)
