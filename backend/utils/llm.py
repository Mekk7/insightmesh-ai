# backend/utils/llm.py
"""
Unified LLM client with graceful free-first fallback.

Resolution order (first one that responds wins):
  1. Ollama  — free, local. Set OLLAMA_URL (default http://localhost:11434) and OLLAMA_MODEL (default llama3.2:3b).
  2. OpenAI  — paid. Used only if OPENAI_API_KEY is set AND Ollama isn't available.
  3. None    — caller falls back to heuristics.

Capabilities advertised via .available() so callers can adapt prompts/expectations.

Design notes:
- Probing Ollama is best-effort and cached for 60s. We never block startup.
- Both backends are wrapped in a tight try/except — never raise from .chat();
  return None instead so the analyzer's heuristic path can kick in.
- JSON mode is normalized: callers ask for `json_mode=True` and we use the
  right backend-specific way to request it (Ollama: `format="json"`; OpenAI:
  `response_format`). When neither supports it cleanly we just inject
  "Respond ONLY with raw JSON" in the system prompt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("insightmesh.llm")

OLLAMA_URL          = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL        = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT      = float(os.getenv("OLLAMA_TIMEOUT_SEC", "30"))
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "").strip()
# LLM_MODEL (generic) takes precedence over OPENAI_MODEL when set.
OPENAI_MODEL        = (os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")).strip()

# Explicit provider preference (optional). "" = legacy free-first behaviour
# (Ollama if reachable, else OpenAI). "openai" = prefer OpenAI whenever a key is
# set (Ollama becomes the fallback). "ollama" = force Ollama-first.
LLM_PROVIDER        = os.getenv("LLM_PROVIDER", "").strip().lower()

# --- Permanent LLM cache (SQLite-backed) -----------------------------------
# LLM calls are expensive (latency for Ollama, dollars for OpenAI). Identical
# (prompt, model, temperature, json_mode) tuples should never run twice. This
# pays back massively when the user analyzes the same product twice, or when
# multiple products share common sub-themes.
LLM_CACHE_PATH = os.getenv("LLM_CACHE_PATH", os.path.join("backend", "data", "llm_cache.db"))
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "1") in ("1", "true", "yes")
_llm_cache_lock = threading.Lock()
_llm_cache_conn: Optional[sqlite3.Connection] = None


def _llm_cache() -> Optional[sqlite3.Connection]:
    global _llm_cache_conn
    if not LLM_CACHE_ENABLED:
        return None
    if _llm_cache_conn is not None:
        return _llm_cache_conn
    with _llm_cache_lock:
        if _llm_cache_conn is not None:
            return _llm_cache_conn
        try:
            os.makedirs(os.path.dirname(LLM_CACHE_PATH), exist_ok=True)
            conn = sqlite3.connect(LLM_CACHE_PATH, check_same_thread=False)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS llm_cache (
                    key TEXT PRIMARY KEY,
                    backend TEXT,
                    model TEXT,
                    response TEXT,
                    created_at REAL
                )"""
            )
            conn.commit()
            _llm_cache_conn = conn
            return conn
        except Exception as e:
            log.warning("LLM cache init failed: %s", e)
            return None


def _cache_key(messages: List[Dict[str, str]], temperature: float, json_mode: bool, max_tokens: int, model: str) -> str:
    payload = json.dumps({
        "messages": messages,
        "temperature": round(float(temperature), 3),
        "json_mode": bool(json_mode),
        "max_tokens": int(max_tokens),
        "model": model,
    }, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    conn = _llm_cache()
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _cache_put(key: str, backend: str, model: str, response: str) -> None:
    conn = _llm_cache()
    if conn is None or not response:
        return
    try:
        with _llm_cache_lock:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, backend, model, response, created_at) VALUES (?, ?, ?, ?, ?)",
                (key, backend, model, response, time.time()),
            )
            conn.commit()
    except Exception as e:
        log.debug("LLM cache write failed: %s", e)

# Probe cache so we don't hammer Ollama on every call
_OLLAMA_PROBE_TTL_SEC = 60
_ollama_state = {"ok": None, "checked_at": 0.0, "error": None}


def _probe_ollama() -> bool:
    """Cached check: is Ollama reachable and does it have at least one model?"""
    now = time.time()
    if _ollama_state["ok"] is not None and (now - _ollama_state["checked_at"]) < _OLLAMA_PROBE_TTL_SEC:
        return _ollama_state["ok"]
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            models = r.json().get("models", []) or []
            ok = len(models) > 0
            _ollama_state.update({"ok": ok, "checked_at": now, "error": None if ok else "no models"})
            return ok
    except Exception as e:
        _ollama_state.update({"ok": False, "checked_at": now, "error": str(e)})
        return False


def available_backend() -> str:
    """Return 'ollama' | 'openai' | 'none' — which backend will actually be used.

    Honors LLM_PROVIDER when set; otherwise falls back to legacy free-first order.
    """
    if LLM_PROVIDER == "openai":
        if OPENAI_API_KEY:
            return "openai"
        return "ollama" if _probe_ollama() else "none"
    if LLM_PROVIDER == "ollama":
        if _probe_ollama():
            return "ollama"
        return "openai" if OPENAI_API_KEY else "none"
    # legacy free-first
    if _probe_ollama():
        return "ollama"
    if OPENAI_API_KEY:
        return "openai"
    return "none"


def _ollama_chat(messages: List[Dict[str, str]], *, temperature: float, json_mode: bool, max_tokens: int) -> Optional[str]:
    body: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": float(temperature),
            "num_predict": int(max_tokens),
        },
    }
    if json_mode:
        body["format"] = "json"
    try:
        with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
            r = client.post(f"{OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
            msg = (data.get("message") or {}).get("content")
            return msg if isinstance(msg, str) and msg.strip() else None
    except Exception as e:
        log.debug("ollama chat failed: %s", e)
        return None


def _openai_chat(messages: List[Dict[str, str]], *, temperature: float, json_mode: bool, max_tokens: int) -> Optional[str]:
    try:
        from openai import OpenAI
    except Exception:
        return None
    if not OPENAI_API_KEY:
        return None
    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            max_retries=1,          # 1 retry max — fail fast on dead keys
            timeout=15.0,           # 15s hard cap per attempt
        )
        kwargs: Dict[str, Any] = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message.content if resp.choices else None
        return msg if isinstance(msg, str) and msg.strip() else None
    except Exception as e:
        log.debug("openai chat failed: %s", e)
        return None


def chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    json_mode: bool = False,
    max_tokens: int = 512,
    prefer: Optional[str] = None,
) -> Optional[str]:
    """
    Generic chat completion. Returns the assistant text or None if no backend
    is available / the call failed. Never raises.

    If `json_mode=True`, the caller still has to `json.loads()` the result
    (this fn returns raw text). When neither backend natively supports json,
    a 'Respond only with raw JSON' instruction is prepended.

    `prefer` can force a specific backend: 'ollama' | 'openai'. If the preferred
    backend isn't available we still fall through.
    """
    if json_mode:
        messages = [{"role": "system", "content": "Respond ONLY with raw JSON. No prose, no markdown fences."}] + messages

    # ---- Permanent cache check (saves $ and latency on repeats) ----
    # Namespace the cache by the model that will actually serve the request, so
    # switching providers (Ollama <-> OpenAI) never returns the other backend's
    # cached answer for an identical prompt.
    _ns_model = OPENAI_MODEL if available_backend() == "openai" else OLLAMA_MODEL
    cache_key = _cache_key(messages, temperature, json_mode, max_tokens, _ns_model)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    order = []
    if prefer == "ollama":
        order = ["ollama", "openai"]
    elif prefer == "openai":
        order = ["openai", "ollama"]
    elif LLM_PROVIDER == "openai":
        order = ["openai", "ollama"]
    elif LLM_PROVIDER == "ollama":
        order = ["ollama", "openai"]
    else:
        order = ["ollama", "openai"] if _probe_ollama() else (["openai"] if OPENAI_API_KEY else [])

    for backend in order:
        if backend == "ollama":
            if not _probe_ollama():
                continue
            out = _ollama_chat(messages, temperature=temperature, json_mode=json_mode, max_tokens=max_tokens)
            if out is not None:
                _cache_put(cache_key, "ollama", OLLAMA_MODEL, out)
                return out
        elif backend == "openai":
            if not OPENAI_API_KEY:
                continue
            out = _openai_chat(messages, temperature=temperature, json_mode=json_mode, max_tokens=max_tokens)
            if out is not None:
                _cache_put(cache_key, "openai", OPENAI_MODEL, out)
                return out
    return None


def chat_json(messages: List[Dict[str, str]], **kwargs) -> Optional[Any]:
    """Convenience: chat + json.loads with safe fallbacks (strips code fences)."""
    raw = chat(messages, json_mode=True, **kwargs)
    if not raw:
        return None
    raw = raw.strip()
    # Strip markdown code fences if a backend ignored the json_mode hint
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    try:
        return json.loads(raw)
    except Exception:
        # Salvage: find the first {...} or [...]
        for open_c, close_c in (("{", "}"), ("[", "]")):
            i = raw.find(open_c)
            j = raw.rfind(close_c)
            if 0 <= i < j:
                try:
                    return json.loads(raw[i:j + 1])
                except Exception:
                    continue
        return None


def status() -> Dict[str, Any]:
    """Diagnostic snapshot for /health-style endpoints."""
    return {
        "backend": available_backend(),
        "ollama": {
            "url": OLLAMA_URL,
            "model": OLLAMA_MODEL,
            "reachable": _probe_ollama(),
            "error": _ollama_state.get("error"),
        },
        "openai": {
            "configured": bool(OPENAI_API_KEY),
            "model": OPENAI_MODEL,
        },
    }
