# tests/conftest.py
"""
Shared pytest fixtures.

Design goals:
- Tests should NOT need an internet connection or API keys.
- Tests should NOT touch the user's real DB or cache.
- Heavy ML-model loading is opt-in via `@pytest.mark.slow`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the project root importable so `from backend.x import y` works
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_db_path(tmp_path, monkeypatch):
    """
    Point the run-history DB at a temp file for the duration of one test.
    Returns the path so the test can assert on it directly.
    """
    db_file = tmp_path / "test_insightmesh.db"
    monkeypatch.setenv("INSIGHTMESH_DB_PATH", str(db_file))

    # Reset the db module's "initialized" flag so it re-creates schema at the new path
    from backend.utils import db as db_module
    db_module._initialized = False  # type: ignore[attr-defined]
    # Also point the module's DEFAULT_DB_PATH at our temp file (since some functions
    # bind it at import time as a default arg).
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", str(db_file))

    yield db_file

    # Reset again after the test so other tests aren't affected
    db_module._initialized = False  # type: ignore[attr-defined]


@pytest.fixture
def fresh_caches():
    """
    Reset module-level singleton caches between tests. Use when a test would
    leak state into other tests.
    """
    from backend.utils import cache as cache_module
    cache_module._scraper_cache = None    # type: ignore[attr-defined]
    cache_module._pipeline_cache = None   # type: ignore[attr-defined]
    yield
    cache_module._scraper_cache = None    # type: ignore[attr-defined]
    cache_module._pipeline_cache = None   # type: ignore[attr-defined]


@pytest.fixture
def sample_reviews():
    """A small, mixed-language, mixed-sentiment sample for filtering/analyzer tests."""
    return [
        "Great product, the battery lasts all day and the noise cancellation is incredible.",
        "Don't buy this, the touch controls are unreliable and software is buggy.",
        "Wish they would add a sleep timer to the app, otherwise great headphones.",
        "Comfort is amazing but the case is way too bulky for daily carry.",
        "It works fine. Nothing special. Average sound quality for the price.",
        "https://example.com",                            # URL-only → should be dropped
        "lololololol",                                    # keyboard smash → should be dropped
        "ok",                                             # too short → should be dropped
        "🔥🔥🔥🔥🔥",                                       # emoji-heavy → should be dropped
        "subscribe to my channel for more reviews",       # spam phrase → should be dropped
    ]


@pytest.fixture
def fake_embedder():
    """
    A deterministic, dependency-free 'embedder' that matches the SentenceTransformer
    surface area used by our code (.encode() returns normalized numpy vectors).

    The vectors are based on a simple bag-of-words hash so semantically related
    strings cluster in the same direction. Good enough for unit tests.
    """
    import numpy as np

    class _FakeEmbedder:
        def __init__(self, dim: int = 64) -> None:
            self.dim = dim

        def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True, **kwargs):
            if isinstance(texts, str):
                texts = [texts]
            vecs = []
            for t in texts:
                vec = np.zeros(self.dim, dtype=np.float32)
                for tok in str(t).lower().split():
                    h = hash(tok) % self.dim
                    vec[h] += 1.0
                # Normalize
                norm = np.linalg.norm(vec) or 1.0
                vec /= norm
                vecs.append(vec)
            return np.array(vecs)

    return _FakeEmbedder()
