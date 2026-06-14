# tests/test_cache.py
"""Tests for backend/utils/cache.py — the TTL cache."""
from __future__ import annotations

import threading
import time

import pytest

from backend.utils.cache import (
    TTLCache,
    all_caches_stats,
    clear_all_caches,
    make_cache_key,
    pipeline_cache,
    scraper_cache,
)


class TestTTLCache:
    def test_set_and_get_roundtrip(self):
        c = TTLCache(ttl_seconds=60, max_size=10)
        c.set("a", {"value": 1})
        assert c.get("a") == {"value": 1}

    def test_missing_key_returns_none(self):
        c = TTLCache(ttl_seconds=60, max_size=10)
        assert c.get("missing") is None

    def test_expiry(self):
        c = TTLCache(ttl_seconds=0.05, max_size=10)
        c.set("a", "v")
        assert c.get("a") == "v"
        time.sleep(0.1)
        assert c.get("a") is None, "Expired entry should be gone"

    def test_capacity_eviction(self):
        c = TTLCache(ttl_seconds=60, max_size=3)
        c.set("a", 1)
        time.sleep(0.001)  # ensure distinct timestamps
        c.set("b", 2)
        time.sleep(0.001)
        c.set("c", 3)
        time.sleep(0.001)
        c.set("d", 4)  # should evict "a" (oldest)
        assert c.get("a") is None
        assert c.get("d") == 4

    def test_overwrite_does_not_evict(self):
        c = TTLCache(ttl_seconds=60, max_size=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("a", "updated")  # overwrite, NOT a new entry
        assert c.get("a") == "updated"
        assert c.get("b") == 2

    def test_delete(self):
        c = TTLCache(ttl_seconds=60, max_size=10)
        c.set("a", 1)
        assert c.delete("a") is True
        assert c.delete("a") is False
        assert c.get("a") is None

    def test_clear_returns_count(self):
        c = TTLCache(ttl_seconds=60, max_size=10)
        c.set("a", 1)
        c.set("b", 2)
        assert c.clear() == 2
        assert c.get("a") is None

    def test_stats_tracks_hits_and_misses(self):
        c = TTLCache(ttl_seconds=60, max_size=10)
        c.set("a", 1)
        c.get("a")           # hit
        c.get("a")           # hit
        c.get("missing")     # miss
        s = c.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert s["hit_rate"] == round(2 / 3, 3)
        assert s["size"] == 1
        assert s["max_size"] == 10

    def test_stats_counts_evictions(self):
        c = TTLCache(ttl_seconds=60, max_size=1)
        c.set("a", 1)
        c.set("b", 2)  # evicts a
        assert c.stats()["evictions"] == 1

    def test_thread_safety(self):
        """Hammer the cache from multiple threads — no exceptions, no data races."""
        c = TTLCache(ttl_seconds=60, max_size=100)
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    c.set(f"k{n}_{i}", i)
                    c.get(f"k{n}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Cache should be thread-safe; got: {errors}"


class TestMakeCacheKey:
    def test_same_args_produce_same_key(self):
        assert make_cache_key("a", 1, [1, 2]) == make_cache_key("a", 1, [1, 2])

    def test_different_args_produce_different_keys(self):
        assert make_cache_key("a", 1) != make_cache_key("a", 2)

    def test_order_matters_for_positional_args(self):
        assert make_cache_key("a", "b") != make_cache_key("b", "a")

    def test_dict_order_does_not_matter(self):
        # JSON serialization uses sort_keys=True
        assert make_cache_key({"x": 1, "y": 2}) == make_cache_key({"y": 2, "x": 1})

    def test_key_is_short_hex(self):
        k = make_cache_key("anything")
        assert len(k) == 32
        assert all(c in "0123456789abcdef" for c in k)


class TestCacheSingletons:
    def test_scraper_and_pipeline_caches_are_distinct(self, fresh_caches):
        sc = scraper_cache()
        pc = pipeline_cache()
        assert sc is not pc

    def test_scraper_cache_is_singleton(self, fresh_caches):
        assert scraper_cache() is scraper_cache()

    def test_all_caches_stats(self, fresh_caches):
        stats = all_caches_stats()
        assert "scraper" in stats
        assert "pipeline" in stats
        assert "size" in stats["scraper"]

    def test_clear_all(self, fresh_caches):
        scraper_cache().set("a", 1)
        pipeline_cache().set("b", 2)
        result = clear_all_caches()
        assert result == {"scraper": 1, "pipeline": 1}
        assert scraper_cache().get("a") is None
