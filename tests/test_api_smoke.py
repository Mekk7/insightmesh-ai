# tests/test_api_smoke.py
"""
Smoke tests against the FastAPI app via TestClient.

IMPORTANT: importing `backend.main` transitively imports analyze_reviews, which
loads transformers, sentence-transformers, KeyBERT, spaCy and zero-shot model.
First import can be SLOW (several seconds, downloads models on first run).

These tests are marked `slow` so they're opt-in:
    pytest                          # runs everything except slow
    pytest -m slow                  # only slow
    pytest -m ""                    # run all
"""
from __future__ import annotations

import os

import pytest


# Mark the entire module as slow because of the import cost
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A TestClient that uses a temp DB so we don't pollute the user's data."""
    tmpdir = tmp_path_factory.mktemp("api_smoke")
    os.environ["INSIGHTMESH_DB_PATH"] = str(tmpdir / "test_smoke.db")

    # Skip heavy LLM/transformer calls during smoke tests
    os.environ.setdefault("SKIP_PHRASE_EXTRACTION", "1")
    os.environ.setdefault("SKIP_ACTION_ITEMS", "1")

    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:
        yield c


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "online"
    assert "version" in body
    assert "api_prefix" in body


def test_ping(client):
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "online"


def test_api_ping(client):
    r = client.get("/api/_ping")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_route_list(client):
    r = client.get("/__routes__")
    assert r.status_code == 200
    paths = [route["path"] for route in r.json()]
    # Sanity-check some critical routes exist
    assert "/api/insightmesh/run_pipeline" in paths
    assert any("/history" in p for p in paths)


def test_history_ping(client):
    r = client.get("/api/insightmesh/history/_ping")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


def test_history_list_empty(client):
    r = client.get("/api/insightmesh/history?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_history_stats(client):
    r = client.get("/api/insightmesh/history/stats")
    assert r.status_code == 200
    assert "total_runs" in r.json()


def test_cache_stats(client):
    r = client.get("/api/insightmesh/history/cache/stats")
    assert r.status_code == 200
    body = r.json()
    assert "scraper" in body
    assert "pipeline" in body


def test_cache_clear(client):
    r = client.post("/api/insightmesh/history/cache/clear")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_history_get_404(client):
    r = client.get("/api/insightmesh/history/999999")
    assert r.status_code == 404


def test_history_delete_404(client):
    r = client.delete("/api/insightmesh/history/999999")
    assert r.status_code == 404


def test_history_search(client):
    r = client.get("/api/insightmesh/history/search?q=nothing")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "needle" in body


def test_run_pipeline_validation_error_missing_input(client):
    """Pipeline should reject when neither query_override nor filepath provided."""
    r = client.post("/api/insightmesh/run_pipeline", json={})
    assert r.status_code in (400, 422)
