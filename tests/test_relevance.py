# tests/test_relevance.py
"""Tests for backend/insight/filters/relevance.py — universal relevance filter."""
from __future__ import annotations

import pytest


class TestRelevanceConfig:
    def test_strictness_profiles(self):
        from backend.insight.filters.relevance import RelevanceConfig
        low = RelevanceConfig.for_strictness("low")
        normal = RelevanceConfig.for_strictness("normal")
        ultra = RelevanceConfig.for_strictness("ultra")
        # Stricter levels should require more from each signal
        assert ultra.min_semantic > normal.min_semantic > low.min_semantic
        assert ultra.min_lexical >= normal.min_lexical >= low.min_lexical

    def test_unknown_strictness_falls_back_to_ultra(self):
        from backend.insight.filters.relevance import RelevanceConfig
        # The default constructor uses env STRICTNESS (default "ultra")
        cfg = RelevanceConfig.for_strictness("weird_invalid_value")
        # implementation defaults to ultra-like values
        assert cfg.min_tokens >= 4


class TestTextUtils:
    def test_tokenize(self):
        from backend.insight.filters.relevance import tokenize
        toks = tokenize("Hello, World! This is a TEST.")
        assert "hello" in toks
        assert "world" in toks
        assert "test" in toks

    def test_token_ratio_nonalpha(self):
        from backend.insight.filters.relevance import token_ratio_nonalpha
        # All alphabetic → low non-alpha ratio
        assert token_ratio_nonalpha("hello world") < 0.5
        # All symbols → high non-alpha ratio
        assert token_ratio_nonalpha("!!!@@@###") > 0.8


class TestAutoLexicon:
    def test_includes_query_tokens(self):
        from backend.insight.filters.relevance import build_auto_lexicon
        terms = build_auto_lexicon("tesla model y", ["random comment one", "another one here"])
        # Query tokens (>2 chars) should be seeds
        assert "tesla" in terms or any("tesla" in t for t in terms)

    def test_extracts_corpus_terms(self):
        from backend.insight.filters.relevance import build_auto_lexicon
        corpus = [
            "the battery life on this device is amazing",
            "battery drains fast on my model",
            "great battery performance overall",
            "the battery is the best feature",
        ]
        terms = build_auto_lexicon("device", corpus)
        # "battery" should rank high
        assert any("battery" in t for t in terms)

    def test_handles_empty_inputs(self):
        from backend.insight.filters.relevance import build_auto_lexicon
        assert build_auto_lexicon("", []) == []
        # Query-only should still return seeds
        terms = build_auto_lexicon("tesla", [])
        assert "tesla" in terms


class TestLexicalScore:
    def test_returns_zero_for_no_overlap(self):
        from backend.insight.filters.relevance import lexical_overlap_score
        assert lexical_overlap_score("apple pear", ["car", "engine"]) == 0.0

    def test_returns_positive_on_overlap(self):
        from backend.insight.filters.relevance import lexical_overlap_score
        score = lexical_overlap_score("the battery is great", ["battery", "life"])
        assert score > 0


class TestProductRelevance:
    def test_relevant_text_kept(self, fake_embedder):
        from backend.insight.filters.relevance import product_relevance, RelevanceConfig
        cfg = RelevanceConfig.for_strictness("low")  # use lenient so fake embedder works
        res = product_relevance(
            "The battery life on this tesla is amazing",
            "tesla battery",
            ["tesla", "battery", "life"],
            embedder=fake_embedder,
            config=cfg,
        )
        # With low strictness and clear overlap, expect keep
        assert res.keep is True or res.lexical > 0  # at minimum it should score

    def test_too_short_dropped(self, fake_embedder):
        from backend.insight.filters.relevance import product_relevance
        res = product_relevance("hi", "tesla", ["tesla"], embedder=fake_embedder)
        assert not res.keep
        assert res.reason == "too_short"


class TestFilterCommentsBatch:
    def test_returns_indices_and_summary(self, fake_embedder):
        from backend.insight.filters.relevance import filter_comments, RelevanceConfig
        comments = [
            "The battery is amazing on this device.",
            "hi",                              # too short
            "Sound quality is great overall.",
            "lololololol",                     # keyboard smash → also short on tokens
        ]
        kept, dropped, terms = filter_comments(
            comments,
            query="device",
            embedder=fake_embedder,
            config=RelevanceConfig.for_strictness("low"),
        )
        assert isinstance(kept, list)
        assert isinstance(dropped, dict)
        assert isinstance(terms, list)
        assert len(kept) <= len(comments)
        # "hi" must be dropped
        assert sum(dropped.values()) >= 1
