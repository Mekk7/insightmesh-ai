# backend/utils/db.py
"""
SQLite-backed pipeline run history.

Zero new dependencies (stdlib `sqlite3` only). Designed to be:
  - File-based (default at backend/data/insightmesh.db)
  - Thread-safe (each call opens its own connection; small write volume)
  - Easy to swap for Postgres later (queries use plain SQL, no ORM lock-in)

Schema is auto-created on first call to `init_db()` or any save/list/get.

Public API:
    init_db()                      -> None
    save_run(...)                  -> int  (returns new row id)
    list_runs(limit=20, ...)       -> list[dict]   (summary fields only)
    get_run(run_id)                -> dict | None  (full row with report JSON)
    delete_run(run_id)             -> bool
    clear_history()                -> int  (rows deleted)
    history_stats()                -> dict
    search_runs(needle, limit=20)  -> list[dict]
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("insightmesh.db")

DEFAULT_DB_PATH = os.getenv(
    "INSIGHTMESH_DB_PATH",
    os.path.join("backend", "data", "insightmesh.db"),
)

_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_mode       TEXT NOT NULL,                  -- 'consumer' | 'company'
    query           TEXT,
    filepath        TEXT,
    platforms       TEXT NOT NULL,                  -- JSON list
    strictness      TEXT,
    time_from       TEXT,
    time_to         TEXT,
    elapsed_ms      INTEGER,
    n_kept          INTEGER,
    n_analyzed      INTEGER,
    mood_index      REAL,
    avg_sentiment   REAL,
    error           TEXT,                           -- non-null if run failed
    report_json     TEXT NOT NULL                   -- full final_report
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON pipeline_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_query      ON pipeline_runs(query);
CREATE INDEX IF NOT EXISTS idx_runs_user_mode  ON pipeline_runs(user_mode);
"""


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Decent defaults for a small write-light workload
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def get_conn(db_path: Optional[str] = None):
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Idempotent. Safe to call at every startup."""
    global _initialized
    with _init_lock:
        if _initialized and not db_path:
            return
        with get_conn(db_path) as conn:
            conn.executescript(SCHEMA)
        _initialized = True
        log.info("[db] schema ensured at %s", db_path or DEFAULT_DB_PATH)


def _ensure_init() -> None:
    if not _initialized:
        init_db()


# -------------------- Row helpers --------------------

_SUMMARY_COLS = (
    "id, created_at, user_mode, query, filepath, platforms, strictness, "
    "time_from, time_to, elapsed_ms, n_kept, n_analyzed, mood_index, "
    "avg_sentiment, error"
)


def _row_to_summary(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    # Decode JSON list
    try:
        d["platforms"] = json.loads(d.get("platforms") or "[]")
    except Exception:
        d["platforms"] = []
    return d


def _row_to_full(row: sqlite3.Row) -> Dict[str, Any]:
    d = _row_to_summary(row)
    try:
        d["report"] = json.loads(row["report_json"]) if row["report_json"] else None
    except Exception:
        d["report"] = None
    return d


# -------------------- Public API --------------------

def save_run(
    *,
    user_mode: str,
    query: Optional[str],
    filepath: Optional[str],
    platforms: Iterable[str],
    strictness: Optional[str],
    time_from: Optional[str],
    time_to: Optional[str],
    elapsed_ms: Optional[int],
    n_kept: Optional[int],
    n_analyzed: Optional[int],
    mood_index: Optional[float],
    avg_sentiment: Optional[float],
    report: Dict[str, Any],
    error: Optional[str] = None,
) -> int:
    _ensure_init()
    try:
        report_json = json.dumps(report, default=str)
    except Exception as e:
        report_json = json.dumps({"_serialization_error": str(e)})
    plats_json = json.dumps(list(platforms or []))

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs
                (user_mode, query, filepath, platforms, strictness,
                 time_from, time_to, elapsed_ms, n_kept, n_analyzed,
                 mood_index, avg_sentiment, error, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_mode, query, filepath, plats_json, strictness,
             time_from, time_to, elapsed_ms, n_kept, n_analyzed,
             mood_index, avg_sentiment, error, report_json),
        )
        return int(cur.lastrowid)


def list_runs(
    *,
    limit: int = 20,
    offset: int = 0,
    user_mode: Optional[str] = None,
    only_successful: bool = False,
) -> List[Dict[str, Any]]:
    _ensure_init()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where_parts: List[str] = []
    params: List[Any] = []
    if user_mode:
        where_parts.append("user_mode = ?")
        params.append(user_mode)
    if only_successful:
        where_parts.append("error IS NULL")
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = f"""
        SELECT {_SUMMARY_COLS}
        FROM pipeline_runs
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_summary(r) for r in rows]


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    _ensure_init()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?",
            (int(run_id),),
        ).fetchone()
    return _row_to_full(row) if row else None


def delete_run(run_id: int) -> bool:
    _ensure_init()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM pipeline_runs WHERE id = ?", (int(run_id),))
        return cur.rowcount > 0


def clear_history() -> int:
    _ensure_init()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM pipeline_runs")
        return cur.rowcount


def search_runs(needle: str, limit: int = 20) -> List[Dict[str, Any]]:
    _ensure_init()
    limit = max(1, min(int(limit), 200))
    pat = f"%{needle.strip()}%" if needle else "%"
    sql = f"""
        SELECT {_SUMMARY_COLS}
        FROM pipeline_runs
        WHERE query LIKE ? OR filepath LIKE ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (pat, pat, limit)).fetchall()
    return [_row_to_summary(r) for r in rows]


def history_stats() -> Dict[str, Any]:
    _ensure_init()
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM pipeline_runs").fetchone()["c"]
        ok = conn.execute("SELECT COUNT(*) AS c FROM pipeline_runs WHERE error IS NULL").fetchone()["c"]
        avg = conn.execute(
            "SELECT AVG(mood_index) AS m, AVG(avg_sentiment) AS s, AVG(elapsed_ms) AS e "
            "FROM pipeline_runs WHERE error IS NULL"
        ).fetchone()
        by_mode_rows = conn.execute(
            "SELECT user_mode, COUNT(*) AS c FROM pipeline_runs GROUP BY user_mode"
        ).fetchall()
        recent = conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM pipeline_runs ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
    return {
        "total_runs": int(total or 0),
        "successful": int(ok or 0),
        "failed": int((total or 0) - (ok or 0)),
        "avg_mood_index": float(avg["m"]) if avg and avg["m"] is not None else None,
        "avg_sentiment": float(avg["s"]) if avg and avg["s"] is not None else None,
        "avg_elapsed_ms": float(avg["e"]) if avg and avg["e"] is not None else None,
        "by_user_mode": {r["user_mode"]: int(r["c"]) for r in by_mode_rows},
        "recent": [_row_to_summary(r) for r in recent],
    }
