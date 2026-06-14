# tests/test_db.py
"""Tests for backend/utils/db.py — the SQLite run-history layer."""
from __future__ import annotations

import json

import pytest


def _sample_report(query="tesla", n=50):
    return {
        "meta": {"query_used": query, "elapsed_ms": 1234},
        "analysis": {
            "overview": {"mood_index": 0.42, "average_sentiment": 3.7},
        },
        "n": n,
    }


class TestSaveAndGet:
    def test_save_returns_id(self, tmp_db_path):
        from backend.utils.db import save_run
        rid = save_run(
            user_mode="consumer",
            query="tesla model y",
            filepath=None,
            platforms=["youtube", "reddit"],
            strictness="normal",
            time_from=None,
            time_to=None,
            elapsed_ms=1500,
            n_kept=42,
            n_analyzed=40,
            mood_index=0.2,
            avg_sentiment=3.5,
            report=_sample_report(),
        )
        assert isinstance(rid, int) and rid > 0

    def test_save_then_get_roundtrip(self, tmp_db_path):
        from backend.utils.db import save_run, get_run
        rid = save_run(
            user_mode="company",
            query="ev",
            filepath="/data/sales.csv",
            platforms=["reddit"],
            strictness="high",
            time_from="2024-01-01",
            time_to="2024-12-31",
            elapsed_ms=2222,
            n_kept=88,
            n_analyzed=40,
            mood_index=-0.1,
            avg_sentiment=2.9,
            report=_sample_report("ev", 88),
        )
        row = get_run(rid)
        assert row is not None
        assert row["user_mode"] == "company"
        assert row["query"] == "ev"
        assert row["platforms"] == ["reddit"]   # JSON-decoded back to list
        assert row["mood_index"] == pytest.approx(-0.1)
        assert row["report"]["meta"]["query_used"] == "ev"

    def test_get_nonexistent_returns_none(self, tmp_db_path):
        from backend.utils.db import get_run
        assert get_run(999_999) is None


class TestList:
    def _seed(self, n=5):
        from backend.utils.db import save_run
        ids = []
        for i in range(n):
            ids.append(save_run(
                user_mode="consumer" if i % 2 == 0 else "company",
                query=f"q{i}",
                filepath=None,
                platforms=["youtube"],
                strictness="normal",
                time_from=None, time_to=None,
                elapsed_ms=100 * i,
                n_kept=i, n_analyzed=i,
                mood_index=None, avg_sentiment=None,
                report={"i": i},
                error=("boom" if i == 3 else None),
            ))
        return ids

    def test_list_newest_first(self, tmp_db_path):
        ids = self._seed(n=3)
        from backend.utils.db import list_runs
        items = list_runs(limit=10)
        assert len(items) == 3
        # newest first — last-inserted id should be index 0
        assert items[0]["id"] == ids[-1]

    def test_list_respects_limit(self, tmp_db_path):
        self._seed(n=5)
        from backend.utils.db import list_runs
        items = list_runs(limit=2)
        assert len(items) == 2

    def test_list_offset(self, tmp_db_path):
        self._seed(n=5)
        from backend.utils.db import list_runs
        page1 = list_runs(limit=2, offset=0)
        page2 = list_runs(limit=2, offset=2)
        assert page1[0]["id"] != page2[0]["id"]
        assert {r["id"] for r in page1} & {r["id"] for r in page2} == set()

    def test_filter_by_user_mode(self, tmp_db_path):
        self._seed(n=5)
        from backend.utils.db import list_runs
        consumer = list_runs(user_mode="consumer")
        assert all(r["user_mode"] == "consumer" for r in consumer)

    def test_only_successful(self, tmp_db_path):
        self._seed(n=5)
        from backend.utils.db import list_runs
        ok = list_runs(only_successful=True)
        assert all(r["error"] is None for r in ok)
        # We seeded one error (i==3) → 5 total - 1 error = 4 ok
        assert len(ok) == 4


class TestSearch:
    def test_search_by_query_substring(self, tmp_db_path):
        from backend.utils.db import save_run, search_runs
        save_run(user_mode="consumer", query="tesla model y", filepath=None,
                 platforms=["youtube"], strictness="normal",
                 time_from=None, time_to=None, elapsed_ms=1,
                 n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                 report={})
        save_run(user_mode="consumer", query="sony headphones", filepath=None,
                 platforms=["youtube"], strictness="normal",
                 time_from=None, time_to=None, elapsed_ms=1,
                 n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                 report={})
        results = search_runs("tesla")
        assert len(results) == 1
        assert "tesla" in (results[0]["query"] or "").lower()

    def test_search_no_match(self, tmp_db_path):
        from backend.utils.db import save_run, search_runs
        save_run(user_mode="consumer", query="tesla", filepath=None,
                 platforms=[], strictness="normal",
                 time_from=None, time_to=None, elapsed_ms=1,
                 n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                 report={})
        assert search_runs("nonexistent_product_xyz") == []


class TestDeleteAndClear:
    def test_delete_existing(self, tmp_db_path):
        from backend.utils.db import save_run, delete_run, get_run
        rid = save_run(user_mode="consumer", query="x", filepath=None,
                       platforms=[], strictness="normal",
                       time_from=None, time_to=None, elapsed_ms=1,
                       n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                       report={})
        assert delete_run(rid) is True
        assert get_run(rid) is None

    def test_delete_nonexistent(self, tmp_db_path):
        from backend.utils.db import delete_run
        assert delete_run(999_999) is False

    def test_clear_history(self, tmp_db_path):
        from backend.utils.db import save_run, clear_history, list_runs
        for i in range(3):
            save_run(user_mode="consumer", query=str(i), filepath=None,
                     platforms=[], strictness="normal",
                     time_from=None, time_to=None, elapsed_ms=1,
                     n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                     report={})
        assert clear_history() == 3
        assert list_runs() == []


class TestStats:
    def test_stats_empty(self, tmp_db_path):
        from backend.utils.db import history_stats
        st = history_stats()
        assert st["total_runs"] == 0
        assert st["successful"] == 0

    def test_stats_with_data(self, tmp_db_path):
        from backend.utils.db import save_run, history_stats
        save_run(user_mode="consumer", query="a", filepath=None,
                 platforms=[], strictness="normal",
                 time_from=None, time_to=None, elapsed_ms=100,
                 n_kept=1, n_analyzed=1, mood_index=0.5, avg_sentiment=4.0,
                 report={})
        save_run(user_mode="company", query="b", filepath=None,
                 platforms=[], strictness="normal",
                 time_from=None, time_to=None, elapsed_ms=200,
                 n_kept=1, n_analyzed=1, mood_index=-0.5, avg_sentiment=2.0,
                 report={}, error="boom")
        st = history_stats()
        assert st["total_runs"] == 2
        assert st["successful"] == 1
        assert st["failed"] == 1
        assert st["by_user_mode"] == {"consumer": 1, "company": 1}
        # Only successful runs contribute to averages
        assert st["avg_mood_index"] == pytest.approx(0.5)
        assert st["avg_sentiment"] == pytest.approx(4.0)


class TestSerialization:
    def test_save_handles_non_serializable_gracefully(self, tmp_db_path):
        """Pipeline reports may contain numpy scalars, datetimes, etc.
        The save layer should fall back to repr instead of crashing."""
        from backend.utils.db import save_run, get_run
        from datetime import datetime
        rid = save_run(user_mode="consumer", query="x", filepath=None,
                       platforms=[], strictness="normal",
                       time_from=None, time_to=None, elapsed_ms=1,
                       n_kept=1, n_analyzed=1, mood_index=None, avg_sentiment=None,
                       report={"now": datetime(2024, 1, 1)})  # not directly JSON-able
        row = get_run(rid)
        assert row is not None
        # default=str converts the datetime to a string
        assert "2024" in str(row["report"]["now"])
