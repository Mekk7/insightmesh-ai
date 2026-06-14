# backend/api/insightmesh/run_pipeline.py
# Dataset-first (company) OR query-first (consumer) flow with time-window auto-inference.
# Analyzer runs IN-PROCESS by default; set USE_HTTP_ANALYZER=1 to call HTTP analyzer.

import asyncio
from time import perf_counter
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import pandas as pd

# Column detection for time inference
from backend.utils.column_guesser import guess_columns

# Cache + run history (added in Phase 2)
from backend.utils.cache import pipeline_cache, scraper_cache, make_cache_key
from backend.utils.db import save_run

# Pull in your plugin registry
from backend.api.insightmesh.plugins import PLUGINS

# Import analyzer CORE for in-process use
from backend.api.endpoints.analyze_reviews import analyze_core

# --- Report schema version ---
# Bump this whenever the analyzer's output semantics change in a way that makes
# OLD cached/history reports wrong to display. Cached reports stamped with an
# older version are ignored (treated as a cache miss) so a fresh analysis runs.
# 2 = total stars<->category reconciliation in analyze_core (no 5-star complaints).
# 3 = Advanced Intelligence Pack (competitive_intelligence / dealbreakers /
#     purchase_advice added to overview) — invalidates pre-pack cached reports.
# 4 = Balanced preset retune (target 50 / max_raw_fetch 400) + deep cross-insights
#     (consensus / expectation / experience) — invalidates older Balanced reports.
REPORT_SCHEMA_VERSION = 4

# --- Feature toggles ---
USE_REDDIT_COMPACTOR = os.getenv("USE_REDDIT_COMPACTOR", "1") in {"1", "true", "True"}
LONG_BLOCK_CHARS     = int(os.getenv("LONG_BLOCK_CHARS", "400"))
USE_HTTP_ANALYZER    = os.getenv("USE_HTTP_ANALYZER", "0") in {"1", "true", "True"}  # default OFF
ANALYZE_BATCH_MAX    = int(os.getenv("ANALYZE_BATCH_MAX", "150"))  # how many comments reach analyze/classify (was 40 — far too thin; raised so 100+ get classified)

# Smarter pre-filter toggles
MIN_TEXT_LEN         = int(os.getenv("MIN_TEXT_LEN", "6"))
DROP_NONALPHA_RATIO  = float(os.getenv("DROP_NONALPHA_RATIO", "0.95"))
MIN_WORDS            = int(os.getenv("MIN_WORDS", "3"))
DEFAULT_STRICTNESS   = os.getenv("FILTER_STRICTNESS", "normal")  # low | normal | high

URL_RE = re.compile(r"(https?://\S|www\.)\S*", re.IGNORECASE)

def _is_too_short_or_nonalphabetic(s: str) -> bool:
    if len(s) < MIN_TEXT_LEN:
        return True
    alpha = sum(ch.isalpha() for ch in s)
    nonalpha_ratio = 1.0 - (alpha / max(1, len(s)))
    return nonalpha_ratio >= DROP_NONALPHA_RATIO

def _has_enough_words(s: str) -> bool:
    # Remove URLs, then require a few alpha words (>=2 letters)
    s2 = URL_RE.sub(" ", s)
    words = re.findall(r"[A-Za-z]{2,}", s2)
    return len(words) >= MIN_WORDS

# --- Base URL / prefix (fallback tries /api, custom prefix, then root) ---
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
API_PREFIX = os.getenv("API_PREFIX", "/api")

def _candidate_urls(suffix: str) -> List[str]:
    def join(a: str, b: str) -> str:
        if a.endswith("/") and b.startswith("/"):
            return a[:-1] + b
        if not a.endswith("/") and not b.startswith("/"):
            return a + "/" + b
        return a + b

    # The app is mounted at API_PREFIX (default "/api"), so the prefixed URL is the
    # CORRECT one. We deliberately do NOT fall back to the bare-root path when a prefix
    # is configured: that fallback produced misleading "/reviews/scrape/* 404" errors
    # that masked the real failure from the prefixed endpoint (e.g. a reddit 503 / a
    # timeout). With no bogus fallback, _post_with_fallback surfaces the true error.
    candidates: List[str] = []
    prefix = (API_PREFIX or "").strip()
    if prefix:
        candidates.append(join(BASE_URL, join(prefix, suffix)))
    if prefix != "/api":
        candidates.append(join(BASE_URL, join("/api", suffix)))  # conventional mount
    if not prefix:
        candidates.append(join(BASE_URL, suffix))  # only meaningful when truly root-mounted

    # de-dup
    seen, uniq = set(), []
    for u in candidates:
        if u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

# ---------------- Source-aware text extractor ----------------

_LIST_KEYS = {"kept_comments", "comments", "reviews", "results", "items", "children"}
_WRAP_KEYS = {"data", "payload", "response"}

def _maybe_text_from_obj(o: Any) -> Optional[str]:
    if not o:
        return None
    if isinstance(o, str):
        t = o.strip()
        return t if t else None
    if isinstance(o, dict):
        for k in ("text", "body", "comment", "content", "selftext", "title"):
            v = o.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None

def _collect_texts_from_payload(obj: Any) -> List[str]:
    """
    Source-aware text collector for a single platform payload.
    Prefer 'kept_comments' over 'comments'; still walk other containers.
    Returns a flat list of strings.
    """
    out: List[str] = []

    def _walk(o: Any):
        if o is None:
            return
        if isinstance(o, str):
            t = o.strip()
            if t:
                out.append(t)
            return
        if isinstance(o, list):
            for x in o:
                _walk(x)
            return
        if isinstance(o, dict):
            # Prefer kept_comments, then comments (legacy)
            if "kept_comments" in o:
                _walk(o["kept_comments"])
            elif "comments" in o:
                _walk(o["comments"])

            t = _maybe_text_from_obj(o)
            if t:
                out.append(t)

            # Walk other known list/wrapper keys (excluding comments handled above)
            for k in _LIST_KEYS - {"kept_comments", "comments"}:
                if k in o:
                    _walk(o[k])
            for k in _WRAP_KEYS:
                if k in o:
                    _walk(o[k])
            for v in o.values():
                if isinstance(v, (dict, list)):
                    _walk(v)
            return

    _walk(obj)
    return out


_ITEM_KEYS_PREFER = ("kept_items",)
_META_FIELDS = ("published_at", "author", "score", "like_count", "video_id", "post_id", "subreddit", "language_detected", "stars_hint", "comment_id", "source_title")

def _collect_items_from_payload(obj: Any, default_platform: str = "unknown") -> List[Dict[str, Any]]:
    """
    Walk a scraper payload and return enriched items: [{text, published_at?, author?, score?, ...}].
    Prefers `kept_items` (the new scraper schema) but gracefully falls back to walking
    `kept_comments` and other text-only containers, wrapping each string as a bare item.
    """
    out: List[Dict[str, Any]] = []

    def _wrap(text: str, source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        item = {"text": text}
        if source:
            for k in _META_FIELDS:
                if k in source and source[k] is not None:
                    item[k] = source[k]
            if "platform" in source and source["platform"]:
                item["platform"] = source["platform"]
        return item

    def _walk(o: Any):
        if o is None:
            return
        if isinstance(o, str):
            t = o.strip()
            if t:
                out.append({"text": t})
            return
        if isinstance(o, list):
            for x in o:
                _walk(x)
            return
        if isinstance(o, dict):
            # Preferred: enriched kept_items
            if "kept_items" in o and isinstance(o["kept_items"], list):
                for child in o["kept_items"]:
                    if isinstance(child, dict) and isinstance(child.get("text"), str):
                        out.append(_wrap(child["text"], child))
                    elif isinstance(child, str) and child.strip():
                        out.append({"text": child.strip()})
                return  # consumed this payload

            # Fallback: kept_comments / comments as strings
            if "kept_comments" in o and isinstance(o["kept_comments"], list):
                for s in o["kept_comments"]:
                    if isinstance(s, str) and s.strip():
                        out.append({"text": s.strip()})
                return
            if "comments" in o and isinstance(o["comments"], list):
                for s in o["comments"]:
                    if isinstance(s, str) and s.strip():
                        out.append({"text": s.strip()})
                return

            # Dict that *is* an item
            if isinstance(o.get("text"), str) and o.get("text").strip():
                out.append(_wrap(o["text"], o))
                return

            # Otherwise walk known container/wrapper keys
            for k in _LIST_KEYS - {"kept_comments", "comments"}:
                if k in o:
                    _walk(o[k])
            for k in _WRAP_KEYS:
                if k in o:
                    _walk(o[k])
            return

    _walk(obj)
    # Stamp default_platform on items that didn't bring one
    for it in out:
        it.setdefault("platform", default_platform)
    return out

# ---------------- Lightweight Paragraph Compactor ----------------
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9“"(\[])|[\n\r]+')
def _split_sents(txt: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(txt) if s and len(s.strip()) > 1][:50]

def compact_comment(text: str, max_lines: int = 3, max_chars_per_line: int = 240) -> List[str]:
    if not text:
        return []
    t = text.strip()
    if len(t) < 10:
        return [t]
    sents = _split_sents(t) or [t]
    ranked = sorted(sents, key=lambda s: len(s.split()), reverse=True)[:max_lines]
    ranked.sort(key=lambda s: sents.index(s))
    def _shorten(s: str) -> str:
        return s if len(s) <= max_chars_per_line else s[: max_chars_per_line - 3].rstrip() + "..."
    return [_shorten(s) for s in ranked]

# ---------------- Time inference from CSV ----------------

def _infer_time_granularity(sorted_series: pd.Series) -> str:
    """Return 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly' (best-effort)."""
    try:
        diffs = sorted_series.diff().dropna().dt.days
        if diffs.empty:
            return "unknown"
        med = float(diffs.median())
        if med <= 2:
            return "daily"
        if med <= 10:
            return "weekly"
        if med <= 45:
            return "monthly"
        if med <= 120:
            return "quarterly"
        return "yearly"
    except Exception:
        return "unknown"

def _span_to_reddit_time_filter(span_days: int) -> str:
    """Map span to PRAW time_filter ('day'|'week'|'month'|'year'|'all')."""
    if span_days <= 1:
        return "day"
    if span_days <= 7:
        return "week"
    if span_days <= 31:
        return "month"
    if span_days <= 366:
        return "year"
    return "all"

def _infer_time_from_csv(path: str) -> Optional[Dict[str, Any]]:
    """Memory-friendly scan to find global min/max date and granularity."""
    try:
        # 1) Peek a small sample to guess columns
        head = pd.read_csv(path, nrows=5000, low_memory=True)
        roles = guess_columns(head)
        date_col = roles.get("date")
        if not date_col or date_col not in head.columns:
            return None

        # 2) Stream only the date column to compute min/max efficiently
        global_min, global_max = None, None
        # Also keep a small sample to detect granularity
        sample_dates: List[pd.Timestamp] = []

        for chunk in pd.read_csv(
            path,
            usecols=[date_col],
            parse_dates=[date_col],
            chunksize=200_000
        ):
            s = pd.to_datetime(chunk[date_col], errors="coerce")
            s = s.dropna()
            if s.empty:
                continue
            cmin, cmax = s.min(), s.max()
            global_min = cmin if global_min is None else min(global_min, cmin)
            global_max = cmax if global_max is None else max(global_max, cmax)
            # keep tiny sample for granularity
            sample_dates.extend(s.head(200).tolist())
            if len(sample_dates) > 2000:
                sample_dates = sample_dates[:2000]

        if not global_min or not global_max:
            return None

        span_days = max(1, (global_max - global_min).days or 1)
        granularity = "unknown"
        if sample_dates:
            ser = pd.Series(sorted(sample_dates))
            granularity = _infer_time_granularity(ser)

        return {
            "time_from": global_min.to_pydatetime().isoformat(),
            "time_to": global_max.to_pydatetime().isoformat(),
            "span_days": span_days,
            "granularity": granularity,
            "reddit_time_filter": _span_to_reddit_time_filter(span_days),
            "date_col": date_col
        }
    except Exception as e:
        logging.warning(f"[time_infer] Failed to infer time window: {e}")
        return None

# ---------------- Request / Response Models ----------------

class RunInput(BaseModel):
    # NEW (optional, backwards-compatible): explicit mode switch
    # - "consumer": search by product name (query_override required; filepath must be null)
    # - "company":  upload CSV (filepath required; query_override must be null)
    # If omitted, we infer from provided fields for backward compatibility.
    input_mode: Optional[str] = Field(default=None)  # "consumer" | "company"

    # Company mode: provide a CSV filepath (server-side path) → we infer tags/query and time
    filepath: Optional[str] = Field(None)

    # Consumer/company: which platforms to hit
    platforms: List[str] = Field(default=["youtube", "reddit"])

    # Legacy perf flag (unchanged; keep for compatibility)
    mode: str = Field(default="fast")

    # Consumer mode: explicit query (e.g., "sony wh-1000xm6")
    query_override: Optional[str] = Field(default=None)

    # Optional timebox (ISO strings, e.g., "2024-01-01"); overrides inference if provided
    time_from: Optional[str] = Field(default=None)
    time_to: Optional[str] = Field(default=None)

    # Comment filtering strictness (pipeline): low | normal | high | ultra
    strictness: Optional[str] = Field(default=None)

    # NEW: optional platform-specific settings; forwarded to plugins via ctx
    # Example:
    # {
    #   "youtube": {"max_videos": 6, "max_comments_per_video": 120},
    #   "reddit":  {"subreddits": ["TeslaMotors"], "time_filter": "month", "max_posts": 80}
    # }
    platform_settings: Optional[Dict[str, Any]] = Field(default=None)

    # Analysis depth: "quick" (~25 comments, fast), "balanced" (~50), "deep" (100+, thorough)
    analysis_depth: Optional[str] = Field(default="balanced")

    # Return debug blocks
    debug: bool = Field(default=True)

class RunOutput(BaseModel):
    final_report: Dict[str, Any]

router = APIRouter()

# ---------------- Client-disconnect helper (Phase A.2) ----------------
#
# When the frontend aborts a request (user types a new search, clicks Stop, switches
# pages, etc.), the TCP connection drops. FastAPI lets us detect that via
# `await request.is_disconnected()`. We check at every meaningful stage boundary
# so we don't waste OpenAI tokens / YouTube quota / Reddit calls / CPU on work
# the user no longer cares about.
#
# We raise a clean HTTPException(499) per nginx's convention ("client closed request").
# The frontend already ignores aborted responses, so this is purely server-side
# resource discipline.
async def _bail_if_disconnected(request: Optional[Request], stage: str) -> None:
    if request is None:
        return
    try:
        if await request.is_disconnected():
            logging.info("[run_pipeline] client disconnected at stage=%s — aborting", stage)
            # 499 = client closed request (nginx convention). The frontend won't
            # see this body since it already aborted, but logs are clean.
            raise HTTPException(status_code=499, detail=f"client_closed_request_at_{stage}")
    except HTTPException:
        raise
    except Exception as e:
        # Defensive: never let the disconnect check ITSELF break the pipeline
        logging.debug("[run_pipeline] disconnect check error at %s: %s", stage, e)

# ---------------- Internal Calls ----------------

async def _post_with_fallback(suffix: str, json_body: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    last_err: Optional[str] = None
    for url in _candidate_urls(suffix):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec)) as client:
                resp = await client.post(url, json=json_body)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    data.setdefault("_debug_hit_url", url)
                return data
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue
    raise HTTPException(502, f"All URL candidates failed for {suffix}. Last error: {last_err}")

async def try_categorize(filepath: Optional[str]) -> List[str]:
    if not filepath:
        return []
    try:
        # Was 1.0s — way too short for any non-trivial CSV. Categorize loads pandas,
        # column-guesses, and (when configured) calls an embedder. 30s is the realistic ceiling.
        data = await _post_with_fallback("/insightmesh/categorize", {"filepath": filepath}, 30.0)
        return data.get("top_products") or data.get("search_tags") or []
    except Exception as e:
        logging.warning("[try_categorize] failed for %s: %s", filepath, e)
        return []

def _summarize_payload(obj: Any) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"type": type(obj).__name__}
    try:
        if isinstance(obj, dict):
            keys = list(obj.keys())
            summary["keys"] = keys[:30]
            counts = {}
            for k in ("kept_comments", "comments", "reviews", "results", "items", "children", "data"):
                v = obj.get(k)
                if isinstance(v, list): counts[k] = len(v)
                elif isinstance(v, dict): counts[k] = len(v.keys())
            summary["counts"] = counts
            if "_debug_hit_url" in obj: summary["hit_url"] = obj["_debug_hit_url"]
        elif isinstance(obj, list):
            summary["len"] = len(obj)
            if obj and isinstance(obj[0], dict):
                summary["first_item_keys"] = list(obj[0].keys())[:30]
    except Exception as e:
        summary["summ_err"] = str(e)
    return summary

def _map_strictness_for_analyzer(s: Optional[str]) -> Optional[str]:
    """
    Map pipeline strictness (low|normal|high|ultra) → analyzer strictness (low|normal|ultra).
    """
    if not s:
        return None
    m = s.lower()
    return {"low": "low", "normal": "normal", "high": "ultra", "ultra": "ultra"}.get(m, m)

def _build_ctx(user_mode: str, query: str, tags: List[str], payload: RunInput, inferred_time: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # Default strictness: NORMAL for consumer (was 'high' - too aggressive for short product names);
    # env-default for company so CSV imports can be tuned.
    if payload.strictness:
        strict_raw = payload.strictness.lower()
    else:
        strict_raw = ("normal" if user_mode == "consumer" else DEFAULT_STRICTNESS).lower()
    if strict_raw not in {"low", "normal", "high", "ultra"}:
        strict_raw = "normal"
    strict_for_analyzer = _map_strictness_for_analyzer(strict_raw)

    # Respect explicit overrides; else use inference; else None
    time_from = payload.time_from or (inferred_time.get("time_from") if inferred_time else None)
    time_to   = payload.time_to   or (inferred_time.get("time_to")   if inferred_time else None)

    ctx: Dict[str, Any] = {
        "mode": user_mode,                   # "company" or "consumer"
        "query": query,                      # main search query
        "tags": tags[:8],                    # helper tags
        "time_from": time_from,
        "time_to": time_to,
        "strictness": strict_for_analyzer,   # normalized for analyzer
        "strictness_raw": strict_raw,        # original for debugging/telemetry
        "platform_settings": payload.platform_settings or {},
    }
    if inferred_time:
        ctx["granularity"] = inferred_time.get("granularity")
        ctx["reddit_time_filter"] = inferred_time.get("reddit_time_filter")
        ctx["span_days"] = inferred_time.get("span_days")
        ctx["date_col"] = inferred_time.get("date_col")
        # Convenience for YouTube APIs that want RFC3339-ish timestamps
        ctx["yt_published_after"]  = time_from
        ctx["yt_published_before"] = time_to
    return ctx

def _safe_launch(platform: str, ctx: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Call plugin launch(); support both new signature (ctx) and legacy (tags)."""
    launcher = PLUGINS[platform]["launch"]
    try:
        return launcher(ctx)  # preferred
    except TypeError:
        # Legacy fallback: plugin wants tags list
        return launcher(ctx.get("tags", []))

async def _determine_mode(payload: RunInput) -> Tuple[str, str, List[str], Optional[Dict[str, Any]]]:
    """
    Returns (user_mode, query, tags, inferred_time) with validation.
    user_mode is "consumer" or "company".
    """
    # If explicit input_mode given, enforce exclusivity
    if payload.input_mode:
        m = payload.input_mode.strip().lower()
        if m not in {"consumer", "company"}:
            raise HTTPException(400, "input_mode must be 'consumer' or 'company'.")
        if m == "consumer":
            if not (payload.query_override and payload.query_override.strip()):
                raise HTTPException(400, "When input_mode='consumer', 'query_override' is required and 'filepath' must be null.")
            if payload.filepath:
                raise HTTPException(400, "When input_mode='consumer', do not provide 'filepath'.")
            query = payload.query_override.strip()
            tags = [query]
            return "consumer", query, tags, None
        else:  # company
            if not payload.filepath:
                raise HTTPException(400, "When input_mode='company', 'filepath' is required and 'query_override' must be null.")
            if payload.query_override:
                raise HTTPException(400, "When input_mode='company', do not provide 'query_override'.")
            tags = await try_categorize(payload.filepath)
            if not tags:
                raise HTTPException(400, "Could not infer search terms from the dataset. Provide a better CSV or use consumer mode.")
            query = " ".join(tags)[:120]
            inferred_time = _infer_time_from_csv(payload.filepath)
            return "company", query, tags, inferred_time

    # Backward-compatible behavior (no input_mode): infer by presence of fields
    if payload.query_override and payload.query_override.strip():
        user_mode = "consumer"
        query = payload.query_override.strip()
        tags = [query]
        inferred_time = None
    elif payload.filepath:
        user_mode = "company"
        tags = await try_categorize(payload.filepath)
        if not tags:
            raise HTTPException(
                400,
                "Could not infer search terms from the dataset. "
                "Check that the CSV has a 'product'-like column. "
                "Or send query_override='your product name' instead."
            )
        query = " ".join(tags)[:120]
        inferred_time = _infer_time_from_csv(payload.filepath)
    else:
        raise HTTPException(
            400,
            "Provide either a dataset ('filepath') for company mode or a product name via 'query_override' for consumer mode."
        )
    return user_mode, query, tags, inferred_time

# ---------------- Main Orchestrator ----------------

@router.post(
    "/run_pipeline",
    response_model=RunOutput,
    summary="End-to-End: Dataset or Product Query → Cross-platform Insights (FAST)"
)
async def run_pipeline(payload: RunInput, request: Request):
    t0 = perf_counter()

    # Bail early if the user already cancelled before we even started
    await _bail_if_disconnected(request, "start")

    # 1) Determine mode + query/tags
    user_mode, query, tags, inferred_time = await _determine_mode(payload)
    ctx = _build_ctx(user_mode, query, tags, payload, inferred_time)

    await _bail_if_disconnected(request, "after_mode_determination")

    # 1b) Pipeline-level cache short-circuit (skip when debug=True so devs see fresh runs)
    cache_key = make_cache_key(
        "pipeline",
        user_mode,
        query,
        sorted(payload.platforms or []),
        ctx.get("time_from"),
        ctx.get("time_to"),
        ctx.get("strictness"),
        payload.platform_settings or {},
    )
    if not payload.debug:
        cached = pipeline_cache().get(cache_key)
        # Ignore cached reports built by an older analyzer schema — serving them
        # would re-introduce already-fixed bugs (e.g. 5-star complaints). A stale
        # version is treated as a miss, forcing a fresh, corrected run.
        cached_version = (cached or {}).get("meta", {}).get("schema_version") if isinstance(cached, dict) else None
        if cached is not None and cached_version == REPORT_SCHEMA_VERSION:
            cached_copy = dict(cached)
            cached_meta = dict(cached_copy.get("meta") or {})
            cached_meta["from_cache"] = True
            cached_meta["cache_key"] = cache_key
            cached_copy["meta"] = cached_meta
            logging.info("[run_pipeline] cache HIT (%s)", cache_key[:8])
            return {"final_report": cached_copy}
        elif cached is not None:
            logging.info("[run_pipeline] cache STALE (v%s != v%s) — recomputing (%s)",
                         cached_version, REPORT_SCHEMA_VERSION, cache_key[:8])

    # 2) Build requests (for debug visibility)
    launches: Dict[str, Dict[str, Any]] = {}
    for p in payload.platforms:
        if p not in PLUGINS:
            continue
        suffix, body = _safe_launch(p, ctx)
        # Echo platform_settings used (if any)
        launches[p] = {
            "suffix": suffix,
            "body": body,
            "candidates": _candidate_urls(suffix),
            "settings_echo": (ctx.get("platform_settings") or {}).get(p, {})
        }

    # 3) Fire requests with fallback (measure per-platform fetch timings)
    async def _fetch(platform: str) -> Tuple[str, Any, int]:
        suffix, body = _safe_launch(platform, ctx)
        timeout = 12.0 if platform == "youtube" else 6.0
        t_fetch = perf_counter()
        data = await _post_with_fallback(suffix, body, timeout)
        fetch_ms = int((perf_counter() - t_fetch) * 1000)
        return platform, data, fetch_ms

    tasks = [_fetch(p) for p in payload.platforms if p in launches]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # If the client gave up while we were waiting on scrapers, bail before doing
    # any of the expensive post-processing (LLM phrase extraction, embeddings, etc.)
    await _bail_if_disconnected(request, "after_scrapers")

    # 4) Collect per-platform debug + prepare per-platform item pools
    platform_data: Dict[str, Any] = {}
    platform_items: Dict[str, List[Dict[str, Any]]] = {}  # enriched items per platform
    errors: List[str] = []

    for res in results:
        if isinstance(res, Exception):
            msg = f"scraper_error: {repr(res)}"
            errors.append(msg)
            continue

        platform, data, fetch_ms = res
        if platform not in launches:
            continue

        if isinstance(data, Exception):
            msg = f"{platform}: {repr(data)}"
            errors.append(msg)
            platform_data[platform] = {"error": msg, "request": launches[platform], "ctx": ctx}
            continue

        summary = _summarize_payload(data)

        # Extract enriched items for this platform (source-aware)
        t_extract = perf_counter()
        items = _collect_items_from_payload(data, default_platform=platform)
        extract_ms = int((perf_counter() - t_extract) * 1000)

        platform_items[platform] = items

        # For visibility, keep the old visible_count heuristic too
        visible_count = 0
        if isinstance(data, dict):
            for k in ("kept_comments", "comments", "reviews"):
                v = data.get(k)
                if isinstance(v, list): visible_count += len(v)
            if isinstance(data.get("results"), list):
                for v in data["results"]:
                    if isinstance(v, dict) and isinstance(v.get("kept_comments"), list):
                        visible_count += len(v["kept_comments"])
        elif isinstance(data, list):
            if data and isinstance(data[0], dict):
                for v in data:
                    if isinstance(v, dict) and isinstance(v.get("kept_comments"), list):
                        visible_count += len(v["kept_comments"])
            else:
                visible_count += len(data)

        platform_data[platform] = {
            "request": launches[platform],
            "response_shape": summary,
            "visible_count": visible_count,
            "ctx": ctx,
            "timing_ms": {"fetch": fetch_ms, "extract": extract_ms},
        }

    if errors:
        logging.warning(f"[run_pipeline] Some scrapers failed: {errors}")

    # 5) Clean, dedupe, compact — while preserving per-comment metadata
    norm = lambda s: re.sub(r"\W+", " ", s).strip().lower()
    seen = set()

    per_platform_counts: Dict[str, Dict[str, int]] = {}
    per_platform_drops: Dict[str, Dict[str, int]] = {}

    items_after_dedupe: List[Dict[str, Any]] = []  # full items with metadata

    # Initialize stats dicts
    for p in payload.platforms:
        per_platform_counts[p] = {
            "fetched_raw": 0,
            "text_extracted": 0,
            "cleaned": 0,
            "deduped": 0
        }
        per_platform_drops[p] = {"short_nonalpha": 0, "low_signal": 0, "duplicate": 0}

    # Clean step (pre-dedupe)
    for p in payload.platforms:
        items = platform_items.get(p, []) or []
        per_platform_counts[p]["fetched_raw"] = len(items)
        per_platform_counts[p]["text_extracted"] = len(items)

        cleaned_for_p: List[Dict[str, Any]] = []
        for item in items:
            tt = (item.get("text") or "").strip()
            if not tt or _is_too_short_or_nonalphabetic(tt):
                per_platform_drops[p]["short_nonalpha"] += 1
                continue
            if not _has_enough_words(tt):
                per_platform_drops[p]["low_signal"] += 1
                continue
            # Normalize text and ensure platform tag is set
            cleaned_item = dict(item)
            cleaned_item["text"] = tt
            cleaned_item["platform"] = p
            cleaned_for_p.append(cleaned_item)

        per_platform_counts[p]["cleaned"] = len(cleaned_for_p)

        # Dedupe globally; attribute duplicates to the platform where they appeared
        for it in cleaned_for_p:
            key = norm(it["text"])
            if key in seen:
                per_platform_drops[p]["duplicate"] += 1
                continue
            seen.add(key)
            items_after_dedupe.append(it)
            per_platform_counts[p]["deduped"] += 1

    # Compact long walls of text (e.g., Reddit) — preserve metadata across chunks
    items_after_compact: List[Dict[str, Any]] = []
    for item in items_after_dedupe:
        t = item["text"]
        if USE_REDDIT_COMPACTOR and len(t) > LONG_BLOCK_CHARS:
            for chunk in compact_comment(t, max_lines=3, max_chars_per_line=240):
                chunked = dict(item)
                chunked["text"] = chunk
                chunked["compacted"] = True
                items_after_compact.append(chunked)
        else:
            items_after_compact.append(item)

    all_comments: List[str] = [it["text"] for it in items_after_compact]
    comment_platforms: List[str] = [it["platform"] for it in items_after_compact]

    # Overall merge stats for Signal Funnel (keep legacy keys expected by UI)
    total_extracted = sum(len(platform_items.get(p, []) or []) for p in payload.platforms)
    dedup_cleaned_len = len(items_after_dedupe)
    # Aggregate overall drop_stats
    overall_drop_stats = {"short_nonalpha": 0, "low_signal": 0, "duplicate": 0}
    for p in payload.platforms:
        ds = per_platform_drops.get(p, {})
        for k in overall_drop_stats.keys():
            overall_drop_stats[k] += int(ds.get(k, 0))

    # Build debug block prior to analysis
    debug_block = {
        "ctx": ctx,
        "tags": tags,
        "time_inference": inferred_time,
        "per_platform": platform_data,
        "merge": {
            "merged_items_len": total_extracted,     # (kept for back-compat; equals total extracted)
            "texts_len": total_extracted,            # UI reads this as "Fetched APIs"
            "dedup_cleaned_len": dedup_cleaned_len,  # after dedupe
            "final_comments_len": len(all_comments), # after compaction & quality filters
            "drop_stats": overall_drop_stats,
            "texts_sample": all_comments[:3],
            "final_comments_sample": all_comments[:3],
        },
        "per_platform_counts": per_platform_counts,
        "per_platform_drops": per_platform_drops,
    }

    if not all_comments:
        if payload.debug:
            raise HTTPException(500, detail={"message": "No usable comments fetched from any platform.", "debug": debug_block})
        raise HTTPException(500, "No usable comments fetched from any platform.")

    # Before kicking off the expensive analyzer (embeddings, transformer models,
    # potential LLM calls for phrase extraction + smart summary + aspect taxonomy),
    # check one more time that the user still wants this answer.
    await _bail_if_disconnected(request, "before_analyzer")

    # --- EARLY RETURN TO ISOLATE ANALYZER (debug mode) ---
    if os.getenv("SKIP_ANALYZE", "0") in {"1", "true", "True"}:
        elapsed_ms = int((perf_counter() - t0) * 1000)
        final = {
            "meta": {
                "user_mode": user_mode,
                "mode": payload.mode,
                "query_used": query,
                "time_from": ctx.get("time_from"),
                "time_to": ctx.get("time_to"),
                "strictness": ctx.get("strictness"),
                "elapsed_ms": elapsed_ms,
                "note": "Analyzer was skipped due to SKIP_ANALYZE."
            },
            "platforms": {
                p: {
                    **platform_data.get(p, {}),
                    "counts": per_platform_counts.get(p, {}),
                    "drop_stats": per_platform_drops.get(p, {}),
                } for p in payload.platforms
            },
            "preview": {
                "final_comments_len": len(all_comments),
                "sample_comments": all_comments[:5]
            },
            "debug": debug_block
        }
        return {"final_report": final}

    # 6) Analyze reviews — cap batch, but keep platform back-map and metadata
    reviews_batch_items = items_after_compact[:ANALYZE_BATCH_MAX]
    reviews_batch = [it["text"] for it in reviews_batch_items]
    reviews_batch_platforms = [it["platform"] for it in reviews_batch_items]
    # Build parallel meta list to pass to analyzer (timestamps, scores, authors)
    reviews_batch_meta = [
        {k: it.get(k) for k in _META_FIELDS + ("platform",) if it.get(k) is not None}
        for it in reviews_batch_items
    ]

    # HTTP mode or in-process mode
    t_analyze = perf_counter()
    if USE_HTTP_ANALYZER:
        debug_block["analyze_request"] = {
            "mode": "http",
            "suffix": "/reviews/analyze",
            "candidates": _candidate_urls("/reviews/analyze"),
            "payload_size": len(reviews_batch),
            "query": query,
            "strictness": ctx.get("strictness"),
        }
        analysis = await _post_with_fallback(
            "/reviews/analyze",
            {
                "reviews": reviews_batch,
                "query": query,
                "strictness": ctx.get("strictness"),
            },
            60.0
        )
    else:
        debug_block["analyze_request"] = {
            "mode": "in_process_core",
            "payload_size": len(reviews_batch),
            "query": query,
            "strictness": ctx.get("strictness"),
        }
        try:
            analysis = analyze_core(
                reviews_batch,
                query=query,
                strictness=ctx.get("strictness"),
                meta=reviews_batch_meta,
            )
        except Exception as e:
            if payload.debug:
                raise HTTPException(500, detail={"message": f"In-process analyzer failed: {e}", "debug": debug_block})
            raise HTTPException(500, f"In-process analyzer failed: {e}")
    analyze_ms_total = int((perf_counter() - t_analyze) * 1000)

    # 7) Build per-platform contributions from analysis
    per_platform_used = {p: 0 for p in payload.platforms}
    per_platform_sentiment_sum = {p: 0.0 for p in payload.platforms}
    per_platform_sentiment_n = {p: 0 for p in payload.platforms}
    per_platform_stars = {p: {"1 star": 0, "2 stars": 0, "3 stars": 0, "4 stars": 0, "5 stars": 0} for p in payload.platforms}
    per_platform_reasons: Dict[str, Dict[str, int]] = {p: {} for p in payload.platforms}
    per_platform_keyphrases: Dict[str, Dict[str, int]] = {p: {} for p in payload.platforms}

    per_review = analysis.get("per_review", []) if isinstance(analysis, dict) else []
    for i, r in enumerate(per_review):
        p = reviews_batch_platforms[i] if i < len(reviews_batch_platforms) else "unknown"
        per_platform_used[p] = per_platform_used.get(p, 0) + 1

        # stars & sentiment
        lbl = str(r.get("sentiment") or "")
        if lbl in per_platform_stars.get(p, {}):
            per_platform_stars[p][lbl] += 1
        try:
            sc = float(r.get("sentiment_score") or 0)
        except Exception:
            sc = 0.0
        per_platform_sentiment_sum[p] += sc
        per_platform_sentiment_n[p] += 1

        # reasons
        reason = (r.get("canonical_reason") or "").strip()
        if reason:
            per_platform_reasons[p][reason] = per_platform_reasons[p].get(reason, 0) + 1

        # keyphrases
        for kp in (r.get("keyphrases") or []):
            kps = str(kp).strip()
            if not kps:
                continue
            per_platform_keyphrases[p][kps] = per_platform_keyphrases[p].get(kps, 0) + 1

    total_used = max(1, sum(per_platform_used.values()))
    contributions = {
        "per_platform": []
    }
    for p in payload.platforms:
        used = per_platform_used.get(p, 0)
        share_pct = round((used / total_used) * 100.0, 1)
        avg_sent = None
        if per_platform_sentiment_n.get(p, 0) > 0:
            avg_sent = round(per_platform_sentiment_sum[p] / per_platform_sentiment_n[p], 3)

        # top reasons & keyphrases
        rs = sorted(per_platform_reasons[p].items(), key=lambda x: (-x[1], x[0]))[:5]
        ks = sorted(per_platform_keyphrases[p].items(), key=lambda x: (-x[1], x[0]))[:8]

        # approximate analyze time per platform
        analyze_ms_share = int(analyze_ms_total * (used / total_used)) if total_used else 0
        # attach timings back into platform_data
        if p in platform_data:
            tms = platform_data[p].setdefault("timing_ms", {})
            tms["analyze"] = analyze_ms_share

        contributions["per_platform"].append({
            "platform": p,
            "share_%": share_pct,
            "used": used,
            "avg_sentiment_score": avg_sent,
            "stars": per_platform_stars[p],
            "top_reasons": [k for k, _ in rs],
            "top_keyphrases": [k for k, _ in ks]
        })

    # Add counts/drops to platform_data
    for p in payload.platforms:
        if p in platform_data:
            platform_data[p]["counts"] = per_platform_counts.get(p, {})
            platform_data[p]["drop_stats"] = per_platform_drops.get(p, {})

    elapsed_ms = int((perf_counter() - t0) * 1000)
    final = {
        "meta": {
            "user_mode": user_mode,            # "consumer" | "company"
            "mode": payload.mode,              # legacy perf flag
            "query_used": query,
            "time_from": ctx.get("time_from"),
            "time_to": ctx.get("time_to"),
            "strictness": ctx.get("strictness"),
            "elapsed_ms": elapsed_ms,
            "from_cache": False,
            "cache_key": cache_key,
            "schema_version": REPORT_SCHEMA_VERSION,
        },
        "platforms": platform_data,
        "contributions": contributions,
        "analysis": analysis,
    }
    if payload.debug:
        final["debug"] = debug_block

    # ---- Persist to cache + run history (best-effort, never breaks the response) ----
    try:
        pipeline_cache().set(cache_key, final)
    except Exception as e:
        logging.warning("[run_pipeline] cache set failed: %s", e)

    try:
        overview = (analysis or {}).get("overview") or {}
        save_run(
            user_mode=user_mode,
            query=query,
            filepath=payload.filepath,
            platforms=payload.platforms or [],
            strictness=ctx.get("strictness"),
            time_from=ctx.get("time_from"),
            time_to=ctx.get("time_to"),
            elapsed_ms=elapsed_ms,
            n_kept=len(all_comments),
            n_analyzed=len(reviews_batch),
            mood_index=overview.get("mood_index"),
            avg_sentiment=overview.get("average_sentiment"),
            report=final,
        )
    except Exception as e:
        logging.warning("[run_pipeline] history save failed: %s", e)

    return {"final_report": final}
