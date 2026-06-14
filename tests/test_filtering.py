# tests/test_filtering.py
"""Tests for backend/utils/filtering.py — comment validation/dedupe layer."""
from __future__ import annotations

import pytest

from backend.utils.filtering import (
    detect_lang,
    filter_and_metrics,
    filter_comments,
    is_clean_sentence,
    validate_with_reason,
)


class TestValidateWithReason:
    @pytest.mark.parametrize("text", [
        "Great product, the noise cancellation is incredible.",
        "Battery dies way too fast on this device, very disappointing.",
        "Wish they would add a sleep timer to the app.",
    ])
    def test_accepts_good_reviews(self, text):
        ok, reason, lang = validate_with_reason(text, strictness="normal")
        assert ok, f"expected to keep, got drop with reason={reason}"
        assert reason == "ok"

    def test_drops_too_short(self):
        ok, reason, _ = validate_with_reason("ok", strictness="normal")
        assert not ok
        assert reason == "too_short"

    def test_drops_url_only(self):
        ok, reason, _ = validate_with_reason("https://example.com", strictness="normal")
        assert not ok
        assert reason == "url_only"

    def test_drops_glyph_noise(self):
        ok, reason, _ = validate_with_reason("!!!???***", strictness="normal")
        assert not ok
        assert reason == "glyph_noise"

    def test_drops_keyboard_smash(self):
        ok, reason, _ = validate_with_reason("lololololol", strictness="normal")
        assert not ok
        assert reason in {"keyboard_smash", "too_few_words"}

    def test_drops_emoji_heavy(self):
        ok, reason, _ = validate_with_reason("🔥🔥🔥🔥🔥🔥🔥🔥", strictness="normal")
        assert not ok
        # could land in emoji_heavy, glyph_noise, or low_alpha depending on count
        assert reason in {"emoji_heavy", "glyph_noise", "low_alpha", "too_few_words"}

    def test_drops_spam_phrase(self):
        ok, reason, _ = validate_with_reason(
            "Hey guys subscribe to my channel for more reviews!",
            strictness="normal"
        )
        assert not ok
        assert reason == "spam_phrase"

    def test_high_strictness_drops_marginal_reviews(self):
        marginal = "ok cool"  # 2 words
        ok_normal, _, _ = validate_with_reason(marginal, strictness="normal")
        ok_high,   _, _ = validate_with_reason(marginal, strictness="high")
        # Normal might keep; high should drop (min_words=4)
        assert ok_high is False


class TestDetectLang:
    def test_returns_lang_code(self):
        # langdetect can be flaky on very short strings, so use a clear one
        out = detect_lang("This is a clear english sentence for language detection.")
        assert out == "en"

    def test_returns_unk_for_garbage(self):
        out = detect_lang("")
        assert out in {"unk", ""}


class TestFilterComments:
    def test_deduplicates(self):
        comments = ["The battery life is excellent.", "The battery life is excellent."]
        kept = filter_comments(comments, desired_count=10, strictness="normal")
        assert len(kept) == 1

    def test_respects_desired_count(self):
        good = [
            "Great battery life, lasts all day.",
            "Sound quality is fantastic, would buy again.",
            "Comfort is amazing for long sessions.",
            "Noise cancellation is best in class.",
        ]
        kept = filter_comments(good, desired_count=2, strictness="normal")
        assert len(kept) == 2

    def test_drops_mixed_garbage(self, sample_reviews):
        # The sample has 5 good + 5 bad-ish entries
        kept = filter_comments(sample_reviews, desired_count=100, strictness="normal")
        # All the obvious garbage should be gone
        assert all(not s.startswith("http") for s in kept)
        assert "ok" not in kept
        assert "🔥🔥🔥🔥🔥" not in kept


class TestFilterAndMetrics:
    def test_returns_drop_reasons(self, sample_reviews):
        kept, dropped, langs = filter_and_metrics(
            sample_reviews, desired_count=100, strictness="normal"
        )
        assert isinstance(kept, list)
        assert isinstance(dropped, dict)
        assert isinstance(langs, dict)
        # At least one drop reason should fire
        assert sum(dropped.values()) > 0

    def test_lang_hist_populates(self):
        kept, _, langs = filter_and_metrics(
            ["This is a clean english sentence about the product."] * 3,
            desired_count=10, strictness="normal"
        )
        assert sum(langs.values()) >= 1


class TestIsCleanSentence:
    def test_basic(self):
        assert is_clean_sentence("Great product, would buy again.")
        assert not is_clean_sentence("ok")
        assert not is_clean_sentence("https://example.com")
