# backend/utils/cache.py
"""
Tiny in-process TTL cache.

Why not Redis? Because most teams running this for the first time want zero
extra infra. This module gives 80% of the benefit (skip repeat scrapes within
a session) with zero new dependencies. For multi-worker production, swap the
backing store for Redis behind the same `get/set/delete` interface.

Usage:
    from backend.utils.cache import scraper_cache, make_cache_key

    c = scraper_cache()
    key = make_cache_key("youtube", "tesla model y", time_from, time_to, strictness)
    hit = c.get(key)
    if hit is not None:
        return hit
    result = expensive_call()
    c.set(key, result)
    return result

Tunables (env):
    SCRAPER_CACHE_TTL_SEC   default 1800 (30 min)
    SCRAPER_CACHE_SIZE      default 256
    PIPELINE_CACHE_TTL_SEC  default 900  (15 min)
    PIPELINE_CACHE_SIZE     default 64
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """Thread-safe in-process TTL cache with size cap (drops oldest on overflow)."""

    def __init__(self, ttl_seconds: int = 600, max_size: int = 256) -> None:
        self.ttl: float = float(ttl_seconds)
        self.max_size: int = int(max_size)
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                # Expired — evict
                del self._store[key]
                self._evictions += 1
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            # Capacity check — drop oldest by insertion timestamp
            if len(self._store) >= self.max_size and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
                self._evictions += 1
            self._store[key] = (time.time(), value)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            valid = sum(1 for ts, _ in self._store.values() if now - ts <= self.ttl)
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total else 0.0
            return {
                "size": len(self._store),
                "valid": valid,
                "max_size": self.max_size,
                "ttl_seconds": self.ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": round(hit_rate, 3),
            }


def make_cache_key(*parts: Any) -> str:
    """
    Stable, short hash for cache keys from arbitrary JSON-serialisable args.
    Lists are NOT sorted automatically — sort yourself if order shouldn't matter.
    """
    try:
        payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        payload = repr(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# -------------------- Module-level singletons --------------------

_scraper_cache: Optional[TTLCache] = None
_pipeline_cache: Optional[TTLCache] = None
_lock = threading.Lock()


def scraper_cache() -> TTLCache:
    """Cache for individual scraper API responses (YouTube/Reddit)."""
    global _scraper_cache
    if _scraper_cache is None:
        with _lock:
            if _scraper_cache is None:
                ttl = int(os.getenv("SCRAPER_CACHE_TTL_SEC", "1800"))
                size = int(os.getenv("SCRAPER_CACHE_SIZE", "256"))
                _scraper_cache = TTLCache(ttl_seconds=ttl, max_size=size)
    return _scraper_cache


def pipeline_cache() -> TTLCache:
    """Cache for full pipeline final_reports (whole-flow short-circuit)."""
    global _pipeline_cache
    if _pipeline_cache is None:
        with _lock:
            if _pipeline_cache is None:
                ttl = int(os.getenv("PIPELINE_CACHE_TTL_SEC", "900"))
                size = int(os.getenv("PIPELINE_CACHE_SIZE", "64"))
                _pipeline_cache = TTLCache(ttl_seconds=ttl, max_size=size)
    return _pipeline_cache


def all_caches_stats() -> Dict[str, Dict[str, Any]]:
    return {
        "scraper": scraper_cache().stats(),
        "pipeline": pipeline_cache().stats(),
    }


def clear_all_caches() -> Dict[str, int]:
    return {
        "scraper": scraper_cache().clear(),
        "pipeline": pipeline_cache().clear(),
    }
