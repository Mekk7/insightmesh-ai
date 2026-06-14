# InsightMesh AI — Tests

Pytest test suite for the backend. Designed to be **fast by default** and
**runnable without API keys or internet**.

## Quick start

```bash
# Install dev deps once
pip install pytest pytest-asyncio pytest-cov

# Run the default (fast) suite
pytest

# Verbose
pytest -v

# Only the slow / smoke tests (loads transformers)
pytest -m slow

# Run everything
pytest -m ""

# Coverage report
pytest --cov=backend --cov-report=term-missing
```

## File map

| File | What it tests | Speed |
|------|---------------|-------|
| `test_cache.py` | `backend/utils/cache.py` — TTL cache, eviction, thread-safety, stats | Fast |
| `test_db.py` | `backend/utils/db.py` — SQLite history layer, all CRUD ops | Fast (uses temp DB) |
| `test_filtering.py` | `backend/utils/filtering.py` — comment validation, drop reasons | Fast |
| `test_column_guesser.py` | `backend/utils/column_guesser.py` — role detection (date/sales/product) | Fast |
| `test_relevance.py` | `backend/insight/filters/relevance.py` — universal relevance filter | Fast (uses fake embedder) |
| `test_cluster.py` | `backend/insight/reasons/cluster.py` — canonical clustering | Fast (uses fake embedder) |
| `test_api_smoke.py` | FastAPI endpoints (TestClient) | **Slow** — loads transformers |

## Conventions

- **No network**: tests must NOT call external APIs (YouTube, Reddit, OpenAI). Use mocks/fakes.
- **No real DB writes**: tests using `db.py` MUST use the `tmp_db_path` fixture from `conftest.py`, which redirects writes to a temp file.
- **No ML downloads in fast suite**: tests that would trigger HuggingFace/transformers model downloads must be marked `@pytest.mark.slow`.
- **Embedder mock**: use the `fake_embedder` fixture for any test that needs a `.encode()`-compatible object.

## Useful fixtures (`conftest.py`)

- `tmp_db_path` → temp SQLite file, auto-cleaned
- `fresh_caches` → resets the module-level cache singletons before/after the test
- `fake_embedder` → deterministic, dependency-free embedder for cluster/relevance tests
- `sample_reviews` → small mixed-quality review set for filtering tests

## Adding new tests

1. Pick the right file (one per module under test).
2. If the test needs DB → take `tmp_db_path` fixture.
3. If it needs caches → take `fresh_caches`.
4. If it would load transformers → mark `@pytest.mark.slow` at function or module level.
5. Prefer many small, focused tests over one big one.

## CI integration

Suggested GitHub Actions matrix (Python 3.10, 3.11, 3.12):

```yaml
- run: pip install -r requirements.txt pytest pytest-asyncio pytest-cov
- run: python -m spacy download en_core_web_sm
- run: pytest -q --cov=backend
```

The `-m 'not slow'` filter is the default in `pyproject.toml`, so CI runs the fast suite automatically. Add a separate job for `pytest -m slow` if you want to validate transformer integrations on a schedule.
