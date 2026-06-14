# backend/api/insightmesh/discover.py
"""
Discovery surface — what to show users when they first land on the app.

A real app needs to feel populated. This module reads from the existing
pipeline_runs SQLite store and emits three social-proof slices:

  - trending: queries most-analyzed in the recent window (with avg sentiment)
  - recent:   most-recently analyzed products (with timestamp + mood)
  - top_rated / most_discussed: simple ranked slices

When the database is sparse (early development), the endpoint blends in
the three demo products so the discover surface never looks empty.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from backend.utils.db import _ensure_init, get_conn, DEFAULT_DB_PATH  # type: ignore
from backend.api.insightmesh.demo import _PROFILE as DEMO_PROFILES

router = APIRouter()


def _safe_query_key(q: Optional[str]) -> str:
    """Normalize a query for grouping. Trim, casefold, collapse whitespace."""
    if not q:
        return ""
    return " ".join(q.strip().lower().split())


def _aggregate_runs(window_days: int = 30) -> Dict[str, Any]:
    """Read pipeline_runs and aggregate trending/recent/etc.

    Returns:
      {
        "trending":      [{query, count, avg_sentiment, last_seen}],
        "recent":        [{query, mood_index, avg_sentiment, created_at, n_kept}],
        "popular_categories": [{label, count}],
      }
    """
    _ensure_init()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    out_trending: List[Dict[str, Any]] = []
    out_recent: List[Dict[str, Any]] = []

    try:
        with get_conn() as conn:
            # Group by normalized query, only successful runs in window
            agg_rows = conn.execute(
                """
                SELECT query,
                       COUNT(*)                  AS c,
                       AVG(avg_sentiment)        AS avg_s,
                       AVG(mood_index)           AS avg_m,
                       MAX(created_at)           AS last_seen,
                       SUM(n_kept)               AS total_comments
                FROM pipeline_runs
                WHERE error IS NULL AND query IS NOT NULL AND TRIM(query) != ''
                  AND created_at >= ?
                GROUP BY LOWER(TRIM(query))
                ORDER BY c DESC, last_seen DESC
                LIMIT 12
                """,
                (cutoff,),
            ).fetchall()

            for r in agg_rows:
                out_trending.append({
                    "query": r["query"],
                    "count": int(r["c"] or 0),
                    "avg_sentiment": round(float(r["avg_s"]), 2) if r["avg_s"] is not None else None,
                    "mood_index": round(float(r["avg_m"]), 3) if r["avg_m"] is not None else None,
                    "last_seen": r["last_seen"],
                    "total_comments": int(r["total_comments"] or 0),
                })

            # Most recent successful runs, deduped by query (latest wins)
            recent_rows = conn.execute(
                """
                SELECT id, query, mood_index, avg_sentiment, created_at, n_kept, platforms
                FROM pipeline_runs
                WHERE error IS NULL AND query IS NOT NULL AND TRIM(query) != ''
                ORDER BY created_at DESC
                LIMIT 30
                """
            ).fetchall()

            seen_queries: set = set()
            for r in recent_rows:
                k = _safe_query_key(r["query"])
                if k in seen_queries:
                    continue
                seen_queries.add(k)
                out_recent.append({
                    "id": int(r["id"]),
                    "query": r["query"],
                    "mood_index": float(r["mood_index"]) if r["mood_index"] is not None else None,
                    "avg_sentiment": float(r["avg_sentiment"]) if r["avg_sentiment"] is not None else None,
                    "created_at": r["created_at"],
                    "n_kept": int(r["n_kept"] or 0),
                })
                if len(out_recent) >= 8:
                    break
    except sqlite3.Error:
        # Bare DB unavailable — return empties, caller still blends in demos
        pass

    return {
        "trending": out_trending,
        "recent": out_recent,
    }


def _blend_with_demos(real: Dict[str, Any]) -> Dict[str, Any]:
    """When real data is sparse, blend in the curated demo products so the
    discover surface always has something to show. Demo entries are clearly
    marked with `source: "demo"` so the frontend can label them."""
    demo_entries: List[Dict[str, Any]] = []
    for key, prof in DEMO_PROFILES.items():
        demo_entries.append({
            "query": prof["query"],
            "demo_key": key,
            "avg_sentiment": prof["avg_sent"],
            "mood_index": prof["mood_index"],
            "n_kept": prof["n_kept"],
            "source": "demo",
        })

    trending = list(real.get("trending") or [])
    recent = list(real.get("recent") or [])

    # If we have less than 3 real trending entries, pad with demos
    if len(trending) < 3:
        seen = {_safe_query_key(t.get("query")) for t in trending}
        for d in demo_entries:
            if _safe_query_key(d["query"]) in seen:
                continue
            trending.append({**d, "count": 1, "last_seen": None})
            if len(trending) >= 6:
                break

    if len(recent) < 3:
        seen_r = {_safe_query_key(r.get("query")) for r in recent}
        for d in demo_entries:
            if _safe_query_key(d["query"]) in seen_r:
                continue
            recent.append({**d, "created_at": None})
            if len(recent) >= 6:
                break

    return {"trending": trending, "recent": recent}


@router.get("/discover/feed", summary="Trending + recent products across the platform")
def discover_feed(window_days: int = 30) -> Dict[str, Any]:
    """The primary feed for the empty/welcome state.

    Returns:
      {
        "trending":   [{query, count, avg_sentiment, mood_index, last_seen, total_comments, [source]}],
        "recent":     [{query, mood_index, avg_sentiment, created_at, n_kept, [source]}],
        "stats":      {total_real_runs, demo_blended},
      }
    """
    real = _aggregate_runs(window_days=max(1, min(int(window_days), 365)))
    blended = _blend_with_demos(real)
    return {
        **blended,
        "stats": {
            "total_real_trending": len(real.get("trending") or []),
            "total_real_recent": len(real.get("recent") or []),
            "demo_blended": len(blended["trending"]) > len(real.get("trending") or []),
        },
    }


@router.get("/discover/related", summary="Find related products to a given query")
def discover_related(query: str = "") -> Dict[str, Any]:
    """Light related-products endpoint: returns analyzed queries that share
    tokens with the input query, ranked by token overlap.

    This is a free, dependency-less version of 'people who searched X also searched Y'.
    """
    _ensure_init()
    q_tokens = set(_safe_query_key(query).split())
    if not q_tokens:
        return {"related": []}

    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT query, AVG(avg_sentiment) AS s, COUNT(*) AS c, MAX(created_at) AS last_seen
                FROM pipeline_runs
                WHERE error IS NULL AND query IS NOT NULL
                GROUP BY LOWER(TRIM(query))
                ORDER BY last_seen DESC
                LIMIT 100
                """
            ).fetchall()
    except sqlite3.Error:
        rows = []

    scored: List[Dict[str, Any]] = []
    for r in rows:
        candidate = _safe_query_key(r["query"])
        if not candidate or candidate == _safe_query_key(query):
            continue
        c_tokens = set(candidate.split())
        overlap = len(q_tokens & c_tokens)
        if overlap == 0:
            continue
        union = len(q_tokens | c_tokens) or 1
        jaccard = overlap / union
        scored.append({
            "query": r["query"],
            "avg_sentiment": round(float(r["s"]), 2) if r["s"] is not None else None,
            "count": int(r["c"] or 0),
            "similarity": round(jaccard, 3),
            "last_seen": r["last_seen"],
        })

    scored.sort(key=lambda x: (-x["similarity"], -x["count"]))

    # Pad from demos if nothing in real data matched
    if not scored:
        for key, prof in DEMO_PROFILES.items():
            if _safe_query_key(prof["query"]) == _safe_query_key(query):
                continue
            scored.append({
                "query": prof["query"],
                "demo_key": key,
                "avg_sentiment": prof["avg_sent"],
                "count": 1,
                "similarity": 0.0,
                "source": "demo",
            })

    return {"related": scored[:6]}
