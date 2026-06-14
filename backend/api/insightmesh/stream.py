# backend/api/insightmesh/stream.py
"""
Server-Sent Events endpoint for real-time pipeline progress.

Why SSE and not WebSocket?
  - One-way (server → client) is all we need.
  - SSE is plain HTTP, works through proxies, no extra deps.

Endpoint:
  POST /api/insightmesh/run_pipeline/stream

Same request body as /run_pipeline. The response is an SSE stream where each
event is JSON-encoded:

  event: progress
  data: {"stage": "scrape", "platform": "youtube", "kept": 23}

  event: complete
  data: {"final_report": {...}}

  event: error
  data: {"message": "..."}

We achieve "real-time" progress by running the actual pipeline in a background
task and the SSE handler polling a shared per-job event queue.

This intentionally does NOT modify the original /run_pipeline endpoint — the
two share zero code paths so changes here can't break the standard request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.api.insightmesh.run_pipeline import (
    RunInput,
    _build_ctx,
    _determine_mode,
    REPORT_SCHEMA_VERSION,
)
from backend.api.insightmesh.plugins import PLUGINS
from backend.api.insightmesh.scraper_config import get_scraper_config, apply_depth_preset
from backend.api.endpoints.analyze_reviews import analyze_core

# Deep classification layer — extracts multi-intent, conditions, experience stage,
# switching signals, and claims vs opinions from every review.
try:
    from backend.insight.deep_classify.classifier import deep_classify_reviews, aggregate_deep_signals
except Exception:
    deep_classify_reviews = None
    aggregate_deep_signals = None

# Product Intelligence — teaches the system what the product IS before analysis.
try:
    from backend.insight.product_intelligence.generator import generate_product_intelligence
except Exception:
    generate_product_intelligence = None
from backend.utils.cache import make_cache_key, pipeline_cache
from backend.utils.db import save_run

# Re-use helpers from run_pipeline that don't need to be re-implemented
from backend.api.insightmesh.run_pipeline import (
    _collect_texts_from_payload,
    _collect_items_from_payload,
    _META_FIELDS,
    _is_too_short_or_nonalphabetic,
    _has_enough_words,
    _post_with_fallback,
    _safe_launch,
    _bail_if_disconnected,
    compact_comment,
    LONG_BLOCK_CHARS,
    USE_REDDIT_COMPACTOR,
    ANALYZE_BATCH_MAX,
)

log = logging.getLogger("insightmesh.stream")

router = APIRouter()


def _sse(event: str, data: Dict[str, Any]) -> str:
    """Format one SSE message. Each event ends with a blank line."""
    try:
        payload = json.dumps(data, default=str)
    except Exception:
        payload = json.dumps({"_serialization_error": True})
    return f"event: {event}\ndata: {payload}\n\n"


# Relevance tiers the deep classifier produces that are NOT about the product.
# Reviews tagged with these are dropped before analysis (see section 5b in the
# pipeline). "direct" and "ecosystem" are kept — ecosystem comments still carry
# product intelligence (e.g. "GTA runs at 60fps on PS5").
_OFF_PRODUCT_TIERS = {"off_topic", "tangential"}

# Tiers that count as genuinely USEFUL product signal — these drive the adaptive
# scraping loop (Task 1). "direct" = about the product itself; "ecosystem" = about
# something related that still reveals product intelligence.
_USEFUL_TIERS = {"direct", "ecosystem"}

# ---- Adaptive scraping loop config (env-tunable) ----
# If fewer than this many useful (direct/ecosystem) reviews survive classification,
# we automatically expand the search with extra YouTube queries and re-classify.
ADAPTIVE_USEFUL_TARGET = int(os.getenv("ADAPTIVE_USEFUL_TARGET", "15"))
# Maximum number of EXPANSION rounds (on top of the initial classification) so the
# loop can never spin forever.
ADAPTIVE_MAX_ROUNDS = int(os.getenv("ADAPTIVE_MAX_ROUNDS", "3"))
# Hard ceiling on how many comments we'll ever hand to analyze_core, so expansion
# can grow the useful set without blowing up analyzer cost.
ADAPTIVE_ANALYZE_CEILING = max(ANALYZE_BATCH_MAX, int(os.getenv("ADAPTIVE_ANALYZE_CEILING", "60")))

# Strictness handed to analyze_core's relevance PREFILTER. Default "off" DISABLES it:
# that prefilter gated comments on similarity to the short query string and cut ~40
# comments down to ~14 (even "low" kept only ~6/20 in testing) because real feedback
# ("the display is sharp") rarely echoes the product name. Product-relevance is instead
# decided upstream by deep_classify's tiers (direct/ecosystem kept; off_topic/tangential
# dropped) — see the §5b gate below. Override with ANALYZE_STRICTNESS.
ANALYZE_STRICTNESS = os.getenv("ANALYZE_STRICTNESS", "off")

# Fast mode: skip deep classification entirely for quick dashboard rendering.
# Set FAST_MODE=1 to get a dashboard in ~2min instead of ~17min.
# Deep signals (relevance tiers, switching, expectation gaps) will be missing.
FAST_MODE = os.getenv("FAST_MODE", "").strip().lower() in ("1", "true", "yes")


def _generate_expansion_queries(product: str, round_idx: int, year: int) -> List[str]:
    """Generate 2-3 fresh YouTube search queries to broaden the hunt for real
    reviews when the first pass came up thin.

    Deterministic (no LLM) so it's fast, free, and unit-testable. Each round uses
    a different angle so we don't re-fetch the same videos. `product` is the bare
    product name (any trailing "review" is stripped so we don't double it up).

    Examples (product="Tesla Model Y", year=2026, round 1):
      ["Tesla Model Y owner review", "Tesla Model Y problems 2026",
       "Tesla Model Y long term review"]
    """
    p = re.sub(r"\s+reviews?\s*$", "", (product or "").strip(), flags=re.I).strip()
    if not p:
        return []
    rounds = [
        ["{p} owner review", "{p} problems {y}", "{p} long term review"],
        ["{p} review {y}", "{p} pros and cons", "{p} is it worth it"],
        ["{p} common issues", "{p} after 6 months", "{p} honest review"],
    ]
    idx = max(1, min(int(round_idx), len(rounds))) - 1
    return [t.format(p=p, y=year) for t in rounds[idx]]


def _diversified_queries(product: str, product_intel: Any = None, max_queries: int = 5) -> List[str]:
    """Build several aspect-diverse search queries for one product (Task 2).

    A single query ("Tesla Model Y") makes Reddit surface one hot thread and pull all
    its comments — 10 reviews about the same topic. Searching several angles instead
    ("... review", "... problems", "... charging", "... build quality") spreads the
    sample across threads/aspects. We combine fixed review-intent angles with the
    product's OWN learned `direct_aspects` (range, charging, build quality, …) when
    product intelligence is available, so it's domain-aware without hard-coding.
    Caller dedupes scraped comments across queries.
    """
    p = re.sub(r"\s+reviews?\s*$", "", (product or "").strip(), flags=re.I).strip()
    if not p:
        return []
    queries = [f"{p} review", f"{p} problems"]
    aspects: List[str] = []
    if product_intel is not None:
        try:
            aspects = [str(a).strip() for a in (getattr(product_intel, "direct_aspects", None) or []) if str(a).strip()]
        except Exception:
            aspects = []
    queries += [f"{p} {a}" for a in aspects]
    # Generic angles so we still diversify when no product intelligence is available.
    queries += [f"{p} reliability", f"{p} long term review", f"{p} worth it"]
    seen, out = set(), []
    for q in queries:
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
        if len(out) >= max_queries:
            break
    return out


def _interleave_by_platform(items: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Round-robin a flat item list across (platform, query) buckets, up to `cap`.

    Two fairness problems solved at once:
      1. The analysis batch is capped — YouTube (chattier, returns first) used to fill
         every slot via a plain `items[:cap]`, truncating Reddit / App Store entirely
         out (they then showed 0 contribution).
      2. With multi-query diversification (Task 2), one query's hot thread could still
         dominate a platform. Bucketing by (platform, `_query`) too means the batch
         draws round-robin across every source AND every search angle.
    Order within a bucket is preserved; buckets are visited in first-seen order.
    """
    if cap is None or cap <= 0:
        return list(items)
    if len(items) <= cap:
        return list(items)
    from collections import OrderedDict
    buckets: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
    for it in items:
        buckets.setdefault((it.get("platform") or "unknown", it.get("_query")), []).append(it)
    lists = list(buckets.values())
    out: List[Dict[str, Any]] = []
    i = 0
    while len(out) < cap:
        progressed = False
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
                progressed = True
                if len(out) >= cap:
                    break
        if not progressed:
            break
        i += 1
    return out


async def _progress_pipeline(payload: RunInput, request: Optional[Any] = None) -> AsyncGenerator[str, None]:
    """
    The streaming version of run_pipeline. Yields SSE-formatted strings.

    Stages emitted (each as `event: progress`):
      pipeline_started, mode_determined, cache_check,
      scrape_started, scrape_done (per platform), clean_dedupe,
      analyze_started, analyze_done, pipeline_complete

    Cancellation: when the client aborts the fetch (Phase A.2), we detect the
    disconnect at each stage boundary and stop work cleanly. SSE has natural
    backpressure too — yields to a closed connection raise — but explicit
    checks let us skip the costly analyzer/LLM stages before they start.
    """
    t0 = perf_counter()
    job_id = uuid.uuid4().hex[:12]

    async def _maybe_bail(stage: str) -> bool:
        """Return True if the client has gone away (caller should `return`)."""
        if request is None:
            return False
        try:
            if await request.is_disconnected():
                log.info("[stream] client disconnected at stage=%s job=%s", stage, job_id)
                return True
        except Exception:
            pass
        return False

    yield _sse("progress", {"stage": "pipeline_started", "job_id": job_id, "ts_ms": 0})

    if await _maybe_bail("start"):
        return

    # ---- 1. Determine mode + query/tags ----
    try:
        user_mode, query, tags, inferred_time = await _determine_mode(payload)
    except HTTPException as e:
        yield _sse("error", {"message": str(e.detail), "status": e.status_code})
        return
    except Exception as e:
        yield _sse("error", {"message": f"mode resolution failed: {e}"})
        return

    ctx = _build_ctx(user_mode, query, tags, payload, inferred_time)
    yield _sse("progress", {
        "stage": "mode_determined",
        "user_mode": user_mode,
        "query": query,
        "tags": tags,
        "platforms": payload.platforms,
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    if await _maybe_bail("after_mode_determined"):
        return

    # ---- 1b. Cache check ----
    # NOTE: analysis_depth MUST be part of the key. Quick/Balanced/Deep produce
    # materially different reports (sample size, deep-classify gate, useful target)
    # for the SAME product, so without it a depth switch silently returns the prior
    # depth's cached result and the "mode switch doesn't re-run" bug appears.
    # REPORT_SCHEMA_VERSION is part of the key so that when the report SHAPE
    # changes (new overview sections, etc.) old cached reports never match — the
    # stream path otherwise serves any cached report verbatim with no version
    # gate (unlike run_pipeline), which would silently render a stale-shaped
    # dashboard after a feature ships.
    cache_key = make_cache_key(
        "pipeline", REPORT_SCHEMA_VERSION, user_mode, query,
        sorted(payload.platforms or []),
        ctx.get("time_from"), ctx.get("time_to"),
        ctx.get("strictness"),
        getattr(payload, "analysis_depth", None) or "balanced",
        payload.platform_settings or {},
    )
    if not payload.debug:
        cached = pipeline_cache().get(cache_key)
        if cached is not None:
            yield _sse("progress", {"stage": "cache_hit", "key": cache_key[:8], "ts_ms": int((perf_counter() - t0) * 1000)})
            cached_copy = dict(cached)
            meta = dict(cached_copy.get("meta") or {})
            meta["from_cache"] = True
            cached_copy["meta"] = meta
            yield _sse("complete", {"final_report": cached_copy})
            return

    yield _sse("progress", {"stage": "cache_miss", "ts_ms": int((perf_counter() - t0) * 1000)})

    # ---- 2. Active platforms + product intelligence (needed UP FRONT so we can
    #         build aspect-diverse search queries and steer the analysis). ----
    active_platforms = [p for p in (payload.platforms or []) if p in PLUGINS]

    product_intel = None
    if generate_product_intelligence is not None:
        try:
            from backend.utils import llm as llm_client
            product_intel = await asyncio.to_thread(generate_product_intelligence, query, llm_client)
            if product_intel and product_intel.category:
                yield _sse("progress", {
                    "stage": "product_intel_done",
                    "category": product_intel.category,
                    "segment": product_intel.segment,
                    "ts_ms": int((perf_counter() - t0) * 1000),
                })
        except Exception as e:
            log.warning("[stream] product intelligence failed (continuing): %s", e)

    # ---- 3. Scrape each platform — diverse queries (Task 2) + tuneable SCRAPER_CONFIG ----
    # SCRAPER_CONFIG drives volume (max_videos/comments/threads), reply-fetching, reddit
    # sort_modes, appstore countries, query_variants, and the raw-fetch hard cap. One
    # query makes Reddit surface a single hot thread → we fan out across angles so the
    # sample spans threads/aspects; §4 dedupes across them and the (platform, query)-aware
    # interleave keeps the analyzed batch balanced across sources AND angles.
    scfg = get_scraper_config()
    # Apply analysis depth preset (quick/balanced/deep) from frontend
    depth = getattr(payload, "analysis_depth", None) or "balanced"
    scfg = apply_depth_preset(scfg, depth)
    log.info("[stream] analysis_depth=%s", depth)
    targets_cfg = scfg.get("targets", {}) or {}
    max_raw_fetch = int(targets_cfg.get("max_raw_fetch") or 500)
    n_variants = max(
        int(scfg.get("youtube", {}).get("query_variants", 3) or 1),
        int(scfg.get("reddit", {}).get("query_variants", 3) or 1),
    )
    diversified = _diversified_queries(query, product_intel, max_queries=n_variants) or [query]
    reddit_sorts = scfg.get("reddit", {}).get("sort_modes") or ["relevance"]

    # Build (platform, query, sort) specs. Reddit cycles sort_modes across its query
    # variants (a mix of sort orders); App Store resolves an app by NAME, so it uses the
    # bare product query and relies on multi-country for its diversity.
    specs: List = []
    for p in active_platforms:
        if p == "reddit":
            for i, q in enumerate(diversified):
                specs.append((p, q, reddit_sorts[i % len(reddit_sorts)]))
        elif p == "appstore":
            specs.append((p, query, None))
        else:
            for q in diversified:
                specs.append((p, q, None))

    yield _sse("progress", {
        "stage": "scrape_started",
        "platforms": active_platforms,
        "queries": diversified,
        "reddit_sorts": reddit_sorts,
        "max_raw_fetch": max_raw_fetch,
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    platform_items: Dict[str, List[Dict[str, Any]]] = {p: [] for p in active_platforms}

    async def fetch_one(platform: str, q: str, sort: Optional[str]):
        ctx_variant = dict(ctx)
        ctx_variant["query"] = q
        ctx_variant["tags"] = []
        if sort:
            ctx_variant["reddit_sort"] = sort
        suffix, body = _safe_launch(platform, ctx_variant)
        timeout = 12.0 if platform == "youtube" else (30.0 if platform == "appstore" else 15.0)
        return await _post_with_fallback(suffix, body, timeout)

    # Fire every spec in parallel; emit progress as each lands. `raw_fetched_total`
    # tracks the global raw-comment count and is enforced against max_raw_fetch (it
    # keeps accumulating into the deferred adaptive loop too).
    scrape_specs = [(p, q, s, asyncio.create_task(fetch_one(p, q, s))) for (p, q, s) in specs]
    raw_fetched_total = 0
    # Per-source pagination cursors (next_page_token / next_after) for the keep-going
    # loop to fetch FRESH pages later — keyed by (platform, query, sort).
    source_cursors: Dict[tuple, Optional[str]] = {}
    for platform, q, sort, task in scrape_specs:
        if await _maybe_bail(f"before_scrape_{platform}"):
            for _, _, _, t in scrape_specs:
                if not t.done():
                    t.cancel()
            return
        if raw_fetched_total >= max_raw_fetch:
            if not task.done():
                task.cancel()
            # Suppress any exception so it doesn't leak as "Task exception was never retrieved"
            if task.done() and not task.cancelled():
                try:
                    task.result()
                except Exception:
                    pass
            continue
        try:
            data = await task
            items = _collect_items_from_payload(data, default_platform=platform)
            # Save this source's next-page cursor for the keep-going loop.
            source_cursors[(platform, q, sort)] = (data or {}).get("next_page_token") or (data or {}).get("next_after")
            room = max(0, max_raw_fetch - raw_fetched_total)
            if len(items) > room:
                items = items[:room]
            for it in items:
                it["_query"] = q
                if sort:
                    it["_sort"] = sort
            platform_items[platform].extend(items)
            raw_fetched_total += len(items)
            yield _sse("progress", {
                "stage": "scrape_platform_done",
                "platform": platform, "query": q, "sort": sort,
                "fetched": len(items), "raw_total": raw_fetched_total,
                "ts_ms": int((perf_counter() - t0) * 1000),
            })
        except Exception as e:
            yield _sse("progress", {
                "stage": "scrape_platform_error",
                "platform": platform, "query": q, "sort": sort,
                "error": str(e), "ts_ms": int((perf_counter() - t0) * 1000),
            })

    log.info("[stream] scrape: fetched %d raw comments across %d specs (cap %d)",
             raw_fetched_total, len(scrape_specs), max_raw_fetch)
    # Track platforms that returned nothing so the dashboard can show it
    scrape_failures: Dict[str, str] = {}
    for p in active_platforms:
        if not platform_items[p]:
            scrape_failures[p] = "no results"
    if scrape_failures:
        log.warning("[stream] platforms with 0 results: %s", scrape_failures)
    yield _sse("progress", {
        "stage": "scrape_done",
        "total_fetched": raw_fetched_total,
        "max_raw_fetch": max_raw_fetch,
        "empty_platforms": list(scrape_failures.keys()),
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    if await _maybe_bail("after_scrapers"):
        return

    # ---- 4. Clean + dedupe (preserve metadata) ----
    import re
    norm = lambda s: re.sub(r"\W+", " ", s).strip().lower()
    seen = set()
    seen_ids = set()  # stable comment IDs (PART B.4) — dedup across ALL pagination rounds
    items_after_dedupe: List[Dict[str, Any]] = []
    drops = {"short_nonalpha": 0, "low_signal": 0, "duplicate": 0}

    for p, items in platform_items.items():
        for item in items:
            tt = (item.get("text") or "").strip()
            if not tt or _is_too_short_or_nonalphabetic(tt):
                drops["short_nonalpha"] += 1
                continue
            if not _has_enough_words(tt):
                drops["low_signal"] += 1
                continue
            # Dedup by stable comment ID first (a comment seen in any round is never
            # re-counted/re-classified), then by normalized text as a backstop.
            cid = item.get("comment_id") or item.get("post_id")
            if cid is not None and cid in seen_ids:
                drops["duplicate"] += 1
                continue
            key = norm(tt)
            if key in seen:
                drops["duplicate"] += 1
                continue
            if cid is not None:
                seen_ids.add(cid)
            seen.add(key)
            cleaned = dict(item)
            cleaned["text"] = tt
            cleaned["platform"] = p
            items_after_dedupe.append(cleaned)

    # Compact long blocks while preserving metadata
    items_after_compact: List[Dict[str, Any]] = []
    for it in items_after_dedupe:
        if USE_REDDIT_COMPACTOR and len(it["text"]) > LONG_BLOCK_CHARS:
            for chunk in compact_comment(it["text"], max_lines=3, max_chars_per_line=240):
                chunked = dict(it)
                chunked["text"] = chunk
                chunked["compacted"] = True
                items_after_compact.append(chunked)
        else:
            items_after_compact.append(it)

    # ---- Platform-aware noise pre-filter (additive; runs BEFORE any transformer/LLM) ----
    # Cheaply drops zero-signal comments (video reactions, emoji-only, device lists,
    # one-word reactions) per-platform. Comments that survive still get deep-classified
    # in Balanced/Deep. Fail-safe: never filter to empty.
    try:
        from backend.insight.intelligence.comment_filter import filter_batch
        _kept, _filter_stats = filter_batch(items_after_compact)
        if _kept:
            log.info("[stream] pre-filter: %s (dropped %d/%d)",
                     _filter_stats, len(items_after_compact) - len(_kept), len(items_after_compact))
            items_after_compact = _kept
        elif items_after_compact:
            log.info("[stream] pre-filter matched all %d comments — keeping unfiltered (fail-safe)",
                     len(items_after_compact))
    except Exception as e:
        log.warning("[stream] pre-filter skipped (%s)", e)

    # ---- Align with the downstream analyze-time prefilter BEFORE sampling ----
    # analyze_core applies `_prefilter_low_value` (10-word floor, emoji/reaction/
    # product-list/video-reaction) to its input. Apply the SAME rule here first so the
    # sampler only chooses from comments that will survive analysis — otherwise it could
    # pick `target` comments and then lose a few downstream (Quick 15 → ~12 analyzed).
    # Single source of truth (imported, not duplicated). Fail-safe: never gate to empty.
    try:
        from backend.api.endpoints.analyze_reviews import _prefilter_low_value
        _gated = [it for it in items_after_compact if _prefilter_low_value(it.get("text") or "") is None]
        if _gated:
            if len(_gated) != len(items_after_compact):
                log.info("[stream] low-value gate: kept %d/%d (aligned with analyze prefilter)",
                         len(_gated), len(items_after_compact))
            items_after_compact = _gated
    except Exception as e:
        log.warning("[stream] low-value gate skipped (%s)", e)

    # ---- Top-up scrape if the candidate pool is thin (Balanced consistency fix) ----
    # Heavy upstream filtering (platform noise gate + low-value gate) can shrink the
    # scrape far below the depth target — e.g. Balanced landing ~22 analyzed against a
    # target of 50. Rather than sample a too-small pool, pull MORE with FRESH query
    # variants (new threads/videos, not just forward pages of the same queries) so the
    # sampler has real choice and the analyzed count lands near the target. Runs BEFORE
    # smart_sample. Skipped for Quick (meant to stay fast). Fail-open.
    try:
        _useful_target_pre = int(targets_cfg.get("target_useful_reviews") or 100)
        _pool_need = int(_useful_target_pre * 1.5)
        if (depth != "quick"
                and len(items_after_compact) < _pool_need
                and raw_fetched_total < max_raw_fetch):
            _extra_qs = [q for q in _diversified_queries(query, product_intel, max_queries=n_variants + 5)
                         if q not in diversified][:4]
            if _extra_qs:
                async def _topup_scrape(extra_queries: List[str], room: int):
                    """Fan out FRESH query variants across the live platforms; clean +
                    dedup against the run-wide seen sets; compact. Returns (items, count)."""
                    new_specs = []
                    for p in active_platforms:
                        if p == "appstore":
                            continue  # resolves an app by NAME; multi-country already covers it
                        for i, q in enumerate(extra_queries):
                            srt = reddit_sorts[i % len(reddit_sorts)] if p == "reddit" else None
                            new_specs.append((p, q, srt))
                    tasks = [(p, q, s, asyncio.create_task(fetch_one(p, q, s))) for (p, q, s) in new_specs]
                    fresh: List[Dict[str, Any]] = []
                    got = 0
                    for p, q, s, task in tasks:
                        if got >= room:
                            if not task.done():
                                task.cancel()
                            continue
                        try:
                            data = await task
                        except Exception:
                            continue
                        source_cursors[(p, q, s)] = (data or {}).get("next_page_token") or (data or {}).get("next_after")
                        for it in _collect_items_from_payload(data, default_platform=p):
                            if got >= room:
                                break
                            tt = (it.get("text") or "").strip()
                            if not tt or _is_too_short_or_nonalphabetic(tt) or not _has_enough_words(tt):
                                continue
                            cid = it.get("comment_id") or it.get("post_id")
                            if (cid is not None and cid in seen_ids) or norm(tt) in seen:
                                continue
                            if cid is not None:
                                seen_ids.add(cid)
                            seen.add(norm(tt))
                            base = dict(it); base["text"] = tt; base["platform"] = p; base["_query"] = q
                            if s:
                                base["_sort"] = s
                            if USE_REDDIT_COMPACTOR and len(tt) > LONG_BLOCK_CHARS:
                                for ch in compact_comment(tt, max_lines=3, max_chars_per_line=240):
                                    cc = dict(base); cc["text"] = ch; cc["compacted"] = True
                                    fresh.append(cc); got += 1
                            else:
                                fresh.append(base); got += 1
                    return fresh, got

                _room = max(0, max_raw_fetch - raw_fetched_total)
                _topped, _got = await _topup_scrape(_extra_qs, _room)
                raw_fetched_total += _got
                # Align the new items with the SAME gates the main pool already passed.
                try:
                    from backend.insight.intelligence.comment_filter import filter_batch as _fb
                    _kept2, _ = _fb(_topped)
                    if _kept2:
                        _topped = _kept2
                except Exception:
                    pass
                try:
                    from backend.api.endpoints.analyze_reviews import _prefilter_low_value as _plv
                    _topped = [it for it in _topped if _plv(it.get("text") or "") is None]
                except Exception:
                    pass
                if _topped:
                    items_after_compact.extend(_topped)
                log.info("[stream] top-up scrape: +%d candidates via %s (pool=%d raw=%d)",
                         len(_topped), _extra_qs, len(items_after_compact), raw_fetched_total)
                yield _sse("progress", {
                    "stage": "topup_scrape", "extra_queries": _extra_qs,
                    "added": len(_topped), "pool": len(items_after_compact),
                    "raw_fetched": raw_fetched_total,
                    "ts_ms": int((perf_counter() - t0) * 1000),
                })
    except Exception as e:
        log.warning("[stream] top-up scrape skipped (%s)", e)

    # ---- Smart review selection (pure heuristics; pick best N for max insight coverage) ----
    # Replaces "first N that survive dedup" with an information-value + topic-diversity
    # greedy selection. Target is the depth preset's count (Quick 15 / Balanced 40 / Deep 80).
    # No-op when there are fewer candidates than the target.
    try:
        from backend.insight.intelligence.smart_sampler import smart_sample
        _sample_target = int(targets_cfg.get("target_useful_reviews") or 100)
        items_after_compact = smart_sample(items_after_compact, target=_sample_target)
    except Exception as e:
        log.warning("[stream] smart_sample skipped (%s)", e)

    all_comments = [it["text"] for it in items_after_compact]
    yield _sse("progress", {
        "stage": "clean_dedupe",
        "kept": len(all_comments),
        "dropped": drops,
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    if not all_comments:
        yield _sse("error", {"message": "No usable comments fetched from any platform."})
        return

    if await _maybe_bail("before_analyzer"):
        return

    # ---- 5b. PAGINATING KEEP-GOING GATHER (PART A funnel + PART B pagination) ----
    # Classify in tiers (keep direct/ecosystem, drop off_topic/tangential — this REPLACES
    # analyze_core's query-similarity prefilter that gutted ~40→14), and KEEP GOING until
    # `target_useful` useful reviews: (1) classify the rest of what we ALREADY fetched
    # (free, no API), then (2) PAGINATE FORWARD from each source's saved cursor for FRESH
    # comments only (dedup by comment ID via seen_ids). Stop at the target, the
    # max_raw_fetch safety cap, an analyze ceiling, or source exhaustion. The kept set
    # feeds analyze_core directly so the dashboard reflects the whole investigation.
    # Slow on Ollama (deep_classify is many sequential calls); fast on gpt-4o-mini.
    deep_by_text: Dict[str, Any] = {}
    relevance_warning: Optional[str] = None
    adaptive_info: Optional[Dict[str, Any]] = None
    useful_target = int(targets_cfg.get("target_useful_reviews") or 100)
    # Cap the batch and ceiling to the depth's own target.
    # Quick (target=25): effective_batch_max=25, analyze_ceiling=50
    # Balanced (target=50): effective_batch_max=50, analyze_ceiling=100
    # Deep (target=100): effective_batch_max=100, analyze_ceiling=150
    effective_batch_max = min(ANALYZE_BATCH_MAX, useful_target)
    PAGE_CHUNK = max(1, effective_batch_max)
    analyze_ceiling = min(ANALYZE_BATCH_MAX, useful_target * 2)

    def _classify_keep(items: List[Dict[str, Any]]):
        """Classify items (reusing the deep_by_text cache so no comment is classified
        twice), keep direct/ecosystem, drop off_topic/tangential.
        Returns (kept, n_useful, n_off, n_newly_classified)."""
        to_do = [it for it in items if it["text"] not in deep_by_text]
        if to_do and deep_classify_reviews is not None:
            from backend.utils import llm as _llm
            raw = [{"original": it["text"], "translated_text": it["text"]} for it in to_do]
            deep_classify_reviews(raw, _llm, product_intel)
            for it, r in zip(to_do, raw):
                if r.get("deep") is not None:
                    deep_by_text[it["text"]] = r["deep"]
        kept, n_useful, n_off = [], 0, 0
        for it in items:
            tier = (deep_by_text.get(it["text"]) or {}).get("relevance_tier")
            if tier in _OFF_PRODUCT_TIERS:
                n_off += 1
                continue
            kept.append(it)
            if tier in _USEFUL_TIERS:
                n_useful += 1
        return kept, n_useful, n_off, len(to_do)

    async def _paginate(spec_key):
        """Fetch the NEXT page for one source via its saved cursor; clean + dedup by
        comment ID (seen_ids) so we NEVER re-fetch the same comment; compact long blocks.
        Returns (fresh_items, new_cursor)."""
        platform, q, sort = spec_key
        cursor = source_cursors.get(spec_key)
        if not cursor:
            return [], None
        ctx_v = dict(ctx); ctx_v["query"] = q; ctx_v["tags"] = []
        if sort:
            ctx_v["reddit_sort"] = sort
        if platform == "youtube":
            ctx_v["yt_page_token"] = cursor
        elif platform == "reddit":
            ctx_v["reddit_after"] = cursor
        try:
            suffix, body = _safe_launch(platform, ctx_v)
            data = await _post_with_fallback(suffix, body, 12.0 if platform == "youtube" else 10.0)
        except Exception as e:
            log.warning("[stream] paginate %s failed: %s", spec_key, e)
            return [], None
        new_cursor = (data or {}).get("next_page_token") or (data or {}).get("next_after")
        fresh: List[Dict[str, Any]] = []
        for it in _collect_items_from_payload(data, default_platform=platform):
            tt = (it.get("text") or "").strip()
            if not tt or _is_too_short_or_nonalphabetic(tt) or not _has_enough_words(tt):
                continue
            cid = it.get("comment_id") or it.get("post_id")
            if (cid is not None and cid in seen_ids) or norm(tt) in seen:
                continue
            if cid is not None:
                seen_ids.add(cid)
            seen.add(norm(tt))
            base = dict(it); base["text"] = tt; base["platform"] = platform; base["_query"] = q
            if USE_REDDIT_COMPACTOR and len(tt) > LONG_BLOCK_CHARS:
                for ch in compact_comment(tt, max_lines=3, max_chars_per_line=240):
                    cc = dict(base); cc["text"] = ch; cc["compacted"] = True; fresh.append(cc)
            else:
                fresh.append(base)
        return fresh, new_cursor

    # --- Round 1: classify the interleaved initial batch ---
    # Quick depth mode implicitly skips deep classification (like FAST_MODE) —
    # Ollama is too slow to deep-classify per-comment in Quick. Everything else
    # (analyze_core, clustering, evidence engine) runs identically to Balanced/Deep.
    is_fast = FAST_MODE or depth == "quick"
    if is_fast:
        log.info("[stream] fast path (FAST_MODE=%s depth=%s) — skipping deep classification", FAST_MODE, depth)
    yield _sse("progress", {"stage": "classify_gate_start",
                            "n": min(len(items_after_compact), ANALYZE_BATCH_MAX),
                            "ts_ms": int((perf_counter() - t0) * 1000)})
    batch0 = _interleave_by_platform(items_after_compact, effective_batch_max)
    batch0_texts = {it["text"] for it in batch0}
    accumulated_kept: List[Dict[str, Any]] = []
    total_useful = total_off = total_classified = 0
    page_rounds = 1
    if deep_classify_reviews is not None and batch0 and not is_fast:
        k0, u0, o0, c0 = await asyncio.to_thread(_classify_keep, batch0)
        accumulated_kept = list(k0); total_useful += u0; total_off += o0; total_classified += c0
    else:
        accumulated_kept = list(batch0)
    yield _sse("progress", {"stage": "paginate_round", "round": page_rounds, "source": "initial",
                            "classified_total": total_classified, "useful_total": total_useful,
                            "off_topic": total_off, "raw_fetched": raw_fetched_total,
                            "ts_ms": int((perf_counter() - t0) * 1000)})

    # --- Keep going: classify already-fetched leftovers FIRST (free), then paginate
    #     FORWARD for fresh comments, until target / cap / exhaustion. ---
    pending = [it for it in items_after_compact if it["text"] not in batch0_texts]
    pi_idx = 0
    live_specs = [k for k, v in source_cursors.items() if v]
    # NOTE: max_raw_fetch is NOT a loop guard here. Classifying already-fetched
    # leftovers (`pending`) is FREE (no scrape), so it must run even when the scrape
    # already hit max_raw_fetch (e.g. Balanced fetches 300 == cap, then 249 leftovers
    # would otherwise never be classified). Only the pagination branch — which DOES
    # fetch — respects the cap.
    MAX_PAGINATE_ROUNDS = 3
    while (deep_classify_reviews is not None
           and not is_fast
           and total_useful < useful_target
           and len(accumulated_kept) < analyze_ceiling):
        # Hard cap: never classify more than MAX_PAGINATE_ROUNDS rounds, regardless
        # of useful count. Past this the marginal yield is tiny and the cost (LLM
        # classify calls + scrape) balloons (a 6-round run classified 299 reviews).
        if page_rounds >= MAX_PAGINATE_ROUNDS:
            break
        if await _maybe_bail(f"paginate_round_{page_rounds}"):
            break
        if pi_idx < len(pending):
            chunk = pending[pi_idx: pi_idx + PAGE_CHUNK]; pi_idx += len(chunk); source = "already_fetched"
        elif live_specs and raw_fetched_total < max_raw_fetch:
            spec_key = live_specs.pop(0)
            fresh, new_cursor = await _paginate(spec_key)
            room = max(0, max_raw_fetch - raw_fetched_total)
            fresh = fresh[:room]
            raw_fetched_total += len(fresh)
            source_cursors[spec_key] = new_cursor
            if new_cursor and fresh:
                live_specs.append(spec_key)  # source still has pages
            chunk = fresh; source = f"page:{spec_key[0]}"
        else:
            break  # leftovers done AND (no live cursors OR fetch cap reached) → stop
        if not chunk:
            if pi_idx >= len(pending) and not live_specs:
                break
            continue
        kN, uN, oN, cN = await asyncio.to_thread(_classify_keep, chunk)
        accumulated_kept.extend(kN); total_useful += uN; total_off += oN; total_classified += cN
        page_rounds += 1
        yield _sse("progress", {"stage": "paginate_round", "round": page_rounds, "source": source,
                                "classified_this_round": cN, "useful_this_round": uN,
                                "useful_total": total_useful, "raw_fetched": raw_fetched_total,
                                "ts_ms": int((perf_counter() - t0) * 1000)})

    # --- Settle the analyzed set (fail-open to the raw batch if classify unavailable) ---
    if accumulated_kept:
        reviews_batch_items = accumulated_kept[:analyze_ceiling]
    else:
        reviews_batch_items = _interleave_by_platform(items_after_compact, effective_batch_max)
    reviews_batch = [it["text"] for it in reviews_batch_items]
    reviews_batch_meta = [
        {k: it.get(k) for k in _META_FIELDS + ("platform",) if it.get(k) is not None}
        for it in reviews_batch_items
    ]
    # Order matches the loop's actual exit precedence: target first, then the
    # analyze ceiling, then the fetch cap (only reachable via the pagination branch),
    # else sources exhausted. (max_raw_fetch is no longer a loop guard — see above.)
    stop_reason = ("target_met" if total_useful >= useful_target
                   else "analyze_ceiling" if len(accumulated_kept) >= analyze_ceiling
                   else "round_cap" if page_rounds >= MAX_PAGINATE_ROUNDS
                   else "max_raw_fetch" if raw_fetched_total >= max_raw_fetch
                   else "sources_exhausted")
    log.info("[stream] funnel: raw_fetched=%d deduped=%d classified=%d off_topic_dropped=%d "
             "useful(direct+eco)=%d kept_for_dashboard=%d pagination_rounds=%d stop=%s target=%d",
             raw_fetched_total, len(items_after_compact), total_classified, total_off,
             total_useful, len(reviews_batch_items), page_rounds, stop_reason, useful_target)
    yield _sse("progress", {
        "stage": "classify_gate_done",
        "raw_fetched": raw_fetched_total, "deduped": len(items_after_compact),
        "classified": total_classified, "off_topic_dropped": total_off,
        "useful_direct_ecosystem": total_useful, "kept_for_dashboard": len(reviews_batch_items),
        "pagination_rounds": page_rounds, "target_useful": useful_target, "stop_reason": stop_reason,
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    # ---- 5c. Analyze the on-topic comments (single pass) ----
    yield _sse("progress", {
        "stage": "analyze_started",
        "n": len(reviews_batch),
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    try:
        # analyze_core is sync and CPU-heavy — run in thread to keep loop responsive.
        # Pass product_intel so the ABSA domain, AI summary, and roadmap remediations
        # are all anchored to what the product IS (e.g. headphones, not a generic device).
        analysis = await asyncio.to_thread(
            analyze_core, reviews_batch, query=query, strictness=ANALYZE_STRICTNESS,
            meta=reviews_batch_meta, product_intelligence=product_intel,
            quick_mode=is_fast,
        )
    except Exception as e:
        yield _sse("error", {"message": f"analyzer failed: {e}"})
        return

    yield _sse("progress", {
        "stage": "analyze_done",
        "n_clusters": len((analysis or {}).get("overview", {}).get("canonical_clusters", []) or []),
        "ts_ms": int((perf_counter() - t0) * 1000),
    })

    # ---- 5d. Attach product intelligence to the overview. (Deep signals + adaptive
    #          scrape are added later by the deferred enrichment phase, after the
    #          dashboard has already rendered.) ----
    per_review_out = (analysis or {}).get("per_review", []) if isinstance(analysis, dict) else []
    overview = analysis.get("overview") if isinstance(analysis, dict) else None
    if isinstance(overview, dict):
        if product_intel is not None and getattr(product_intel, "category", ""):
            try:
                overview["product_intelligence"] = product_intel.to_dict()
            except Exception:
                pass
        # Surface platforms that returned nothing (Bug 5 visibility)
        if scrape_failures:
            overview["empty_platforms"] = list(scrape_failures.keys())

    per_platform_used = {p: 0 for p in (payload.platforms or [])}
    per_platform_sentiment_sum = {p: 0.0 for p in (payload.platforms or [])}
    per_platform_sentiment_n = {p: 0 for p in (payload.platforms or [])}
    per_platform_stars = {p: {"1 star": 0, "2 stars": 0, "3 stars": 0, "4 stars": 0, "5 stars": 0} for p in (payload.platforms or [])}
    per_platform_reasons: Dict[str, Dict[str, int]] = {p: {} for p in (payload.platforms or [])}

    # Key off each review's own `platform` (attached from meta in analyze_core).
    # Positional alignment to reviews_batch_items is no longer valid here because
    # the relevance filter may have dropped/re-derived per_review.
    per_review_out = (analysis or {}).get("per_review", []) if isinstance(analysis, dict) else []
    for r in per_review_out:
        p = r.get("platform") or "unknown"
        if p not in per_platform_used:
            continue
        per_platform_used[p] += 1
        lbl = str(r.get("sentiment") or "")
        if lbl in per_platform_stars[p]:
            per_platform_stars[p][lbl] += 1
        try:
            sc = float(r.get("sentiment_score") or 0)
        except Exception:
            sc = 0.0
        per_platform_sentiment_sum[p] += sc
        per_platform_sentiment_n[p] += 1
        reason = (r.get("canonical_reason") or "").strip()
        if reason:
            per_platform_reasons[p][reason] = per_platform_reasons[p].get(reason, 0) + 1

    total_used = max(1, sum(per_platform_used.values()))
    contributions_block: List[Dict[str, Any]] = []
    for p in (payload.platforms or []):
        used = per_platform_used.get(p, 0)
        share_pct = round((used / total_used) * 100.0, 1)
        avg_sent = None
        if per_platform_sentiment_n.get(p, 0) > 0:
            avg_sent = round(per_platform_sentiment_sum[p] / per_platform_sentiment_n[p], 3)
        rs = sorted(per_platform_reasons[p].items(), key=lambda x: (-x[1], x[0]))[:5]
        contributions_block.append({
            "platform": p,
            "share_%": share_pct,
            "used": used,
            "avg_sentiment_score": avg_sent,
            "stars": per_platform_stars[p],
            "top_reasons": [k for k, _ in rs],
        })

    # ---- 6. Build final report (minimal — no per-platform contributions in stream mode) ----
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
            "from_cache": False,
            "cache_key": cache_key,
            "stream_mode": True,
        },
        "platforms": {
            p: {
                "counts": {
                    "fetched_raw": len(platform_items.get(p, [])),
                    "text_extracted": len(platform_items.get(p, [])),
                    "deduped": per_platform_used.get(p, 0),
                },
                "drop_stats": {},
            } for p in (payload.platforms or [])
        },
        "contributions": {"per_platform": contributions_block},
        "analysis": analysis,
    }

    # Persist the FAST report and emit it so the dashboard renders immediately.
    def _persist(report: Dict[str, Any]) -> None:
        try:
            pipeline_cache().set(cache_key, report)
        except Exception as e:
            log.warning("[stream] cache set failed: %s", e)

    _persist(final)
    try:
        ov_for_save = overview or {}
        save_run(
            user_mode=user_mode, query=query, filepath=payload.filepath,
            platforms=payload.platforms or [], strictness=ctx.get("strictness"),
            time_from=ctx.get("time_from"), time_to=ctx.get("time_to"),
            elapsed_ms=elapsed_ms, n_kept=len(all_comments), n_analyzed=len(reviews_batch),
            mood_index=ov_for_save.get("mood_index"),
            avg_sentiment=ov_for_save.get("average_sentiment"),
            report=final,
        )
    except Exception as e:
        log.warning("[stream] history save failed: %s", e)

    # The client renders the dashboard on THIS event (~60-90s). The stream stays
    # open; deep signals stream in afterward as `enriched`.
    yield _sse("complete", {"final_report": final})

    # ---- 5e. DEFERRED deep classification + adaptive scrape (background enrichment) ----
    # Now the dashboard is on screen, do the slow, sequential-Ollama work: classify
    # the analyzed comments, adaptively scrape more if useful signal is thin, then
    # aggregate the deep signals and stream an `enriched` report the client merges in.
    if deep_classify_reviews is None or not reviews_batch or not isinstance(overview, dict) or is_fast:
        return
    if await _maybe_bail("before_enrich"):
        return

    try:
        from backend.utils import llm as llm_client
        from backend.insight.coverage.coverage_map import build_coverage_map

        # Targets + coverage config from SCRAPER_CONFIG.
        useful_target = int(targets_cfg.get("target_useful_reviews") or ADAPTIVE_USEFUL_TARGET)
        min_useful = int(targets_cfg.get("min_useful_reviews") or 0)
        cov_cfg = scfg.get("coverage", {}) or {}
        coverage_on = bool(cov_cfg.get("enabled", True))
        max_rounds = int(cov_cfg.get("max_rounds", ADAPTIVE_MAX_ROUNDS) or ADAPTIVE_MAX_ROUNDS)

        # Embedder powers semantic category-matching + sub-problem clustering. Reuse the
        # analyzer's sentence-transformer; coverage degrades to token-overlap if absent.
        cov_embedder = None
        if coverage_on:
            try:
                from backend.api.endpoints.analyze_reviews import embedder as cov_embedder
            except Exception:
                cov_embedder = None
        cmap = build_coverage_map(product_intel, embedder=cov_embedder, config=cov_cfg) if coverage_on else None

        def _classify_and_ingest(items: List[Dict[str, Any]]):
            """Deep-classify items (REUSING any already in deep_by_text — e.g. the §5b
            gate batch — so we never re-call the LLM on the same comment), record signals,
            harvest product_insights. Returns (kept_on_topic, n_useful, product_insights)."""
            to_classify = [it for it in items if it["text"] not in deep_by_text]
            if to_classify:
                raw_new = [{"original": it["text"], "translated_text": it["text"]} for it in to_classify]
                deep_classify_reviews(raw_new, llm_client, product_intel)
                for it, r in zip(to_classify, raw_new):
                    if r.get("deep") is not None:
                        deep_by_text[it["text"]] = r["deep"]
            kept: List[Dict[str, Any]] = []
            n_useful = 0
            insights: List[Dict[str, Any]] = []
            for it in items:
                deep = deep_by_text.get(it["text"])
                tier = (deep or {}).get("relevance_tier")
                if tier not in _OFF_PRODUCT_TIERS:
                    kept.append(it)
                if tier in _USEFUL_TIERS:
                    n_useful += 1
                pi = (deep or {}).get("product_insight")
                if isinstance(pi, dict) and pi.get("insight"):
                    insights.append(pi)
            return kept, n_useful, insights

        # Per-(platform, query) pagination cursors for the coverage gap-search, so that
        # re-searching the SAME gap query in a later round pulls the NEXT page instead of
        # re-fetching page 1. (The §5b keep-going loop paginates the original queries;
        # this paginates the gap-targeted ones.)
        query_cursors: Dict[tuple, Optional[str]] = {}

        async def _scrape_query(q: str, room: int) -> List[Dict[str, Any]]:
            """Scrape one query across the active YouTube/Reddit sources, PAGINATING
            FORWARD via saved cursors (page_token / after) so repeated searches of the
            same query never re-fetch the same comments. Cleans + dedups by comment ID
            (seen_ids) then text (seen); returns up to `room` fresh items."""
            got: List[Dict[str, Any]] = []
            for plat in [p for p in ("youtube", "reddit") if p in active_platforms]:
                if len(got) >= room:
                    break
                ckey = (plat, q)
                ctx_variant = dict(ctx)
                ctx_variant["query"] = q
                ctx_variant["tags"] = []
                cur = query_cursors.get(ckey)
                if cur:
                    if plat == "youtube":
                        ctx_variant["yt_page_token"] = cur
                    else:
                        ctx_variant["reddit_after"] = cur
                try:
                    suffix, body = _safe_launch(plat, ctx_variant)
                    data = await _post_with_fallback(suffix, body, 12.0 if plat == "youtube" else 10.0)
                except Exception as e:
                    log.warning("[stream] coverage scrape failed for %r on %s: %s", q, plat, e)
                    continue
                # Save the next-page cursor so the NEXT search of this query advances.
                query_cursors[ckey] = (data or {}).get("next_page_token") or (data or {}).get("next_after")
                for it in _collect_items_from_payload(data, default_platform=plat):
                    tt = (it.get("text") or "").strip()
                    if not tt or _is_too_short_or_nonalphabetic(tt) or not _has_enough_words(tt):
                        continue
                    cid = it.get("comment_id") or it.get("post_id")
                    if (cid is not None and cid in seen_ids) or norm(tt) in seen:
                        continue
                    if cid is not None:
                        seen_ids.add(cid)
                    seen.add(norm(tt))
                    base = dict(it)
                    base["text"] = tt
                    base["platform"] = plat
                    if USE_REDDIT_COMPACTOR and len(tt) > LONG_BLOCK_CHARS:
                        for chunk in compact_comment(tt, max_lines=3, max_chars_per_line=240):
                            cc = dict(base)
                            cc["text"] = chunk
                            cc["compacted"] = True
                            got.append(cc)
                    else:
                        got.append(base)
                    if len(got) >= room:
                        break
            return got

        round_stats: List[Dict[str, Any]] = []
        total_useful = 0
        total_classified = 0
        extra_items: List[Dict[str, Any]] = []   # adaptively-scraped, on-topic
        can_expand = ("youtube" in active_platforms) or ("reddit" in active_platforms)

        # ---- Round 1: classify the dashboard's comments + SEED the coverage map. ----
        yield _sse("progress", {"stage": "deep_classify_start", "n": len(reviews_batch), "ts_ms": int((perf_counter() - t0) * 1000)})
        if cmap is not None:
            cmap.start_round()
        _, useful0, ins0 = await asyncio.to_thread(_classify_and_ingest, reviews_batch_items)
        total_useful += useful0
        total_classified += len(reviews_batch_items)
        if cmap is not None:
            cmap.ingest(ins0)
            cmap.log_state("initial batch")
        assess0 = cmap.assess() if cmap is not None else {}
        round_stats.append({"round": 1, "source": "analyzed_batch", "classified": len(reviews_batch_items),
                            "insights": len(ins0), "useful": useful0, "coverage": assess0})
        yield _sse("progress", {
            "stage": "coverage_round", "round": 1, "source": "analyzed_batch",
            "classified": len(reviews_batch_items), "insights": len(ins0), "useful_total": total_useful,
            "coverage": assess0, "ts_ms": int((perf_counter() - t0) * 1000),
        })

        # ---- Coverage-driven expansion: hunt the GAP / THIN categories specifically,
        # round after round, until the expected map is reasonably covered and no new
        # categories are emerging — or we hit max_rounds / the raw-fetch cap / run out
        # of sources. Saturated + well-covered categories are deliberately NOT re-pulled. ----
        while (
            cmap is not None
            and can_expand
            and not cmap.is_done()
            and cmap.round < max_rounds
            and raw_fetched_total < max_raw_fetch
        ):
            targets = cmap.expansion_targets()
            if not targets:
                break
            if await _maybe_bail(f"coverage_round_{cmap.round}"):
                return
            cmap.start_round()
            queries = [f"{query} {cat} problems" for cat in targets]
            yield _sse("progress", {
                "stage": "coverage_expand_start", "round": cmap.round,
                "targets": targets, "queries": queries,
                "ts_ms": int((perf_counter() - t0) * 1000),
            })
            new_items: List[Dict[str, Any]] = []
            for q in queries:
                if raw_fetched_total + len(new_items) >= max_raw_fetch:
                    break
                room = max(0, max_raw_fetch - raw_fetched_total - len(new_items))
                new_items.extend(await _scrape_query(q, room))
            raw_fetched_total += len(new_items)

            if not new_items:
                cmap.ingest([])  # advance round bookkeeping; sources exhausted for these
                round_stats.append({"round": cmap.round, "source": "coverage_expansion",
                                    "targets": targets, "classified": 0, "note": "no_new_comments",
                                    "coverage": cmap.assess()})
                yield _sse("progress", {
                    "stage": "coverage_round", "round": cmap.round, "source": "coverage_expansion",
                    "targets": targets, "classified": 0, "note": "no_new_comments",
                    "coverage": cmap.assess(), "ts_ms": int((perf_counter() - t0) * 1000),
                })
                break

            keptN, usefulN, insN = await asyncio.to_thread(_classify_and_ingest, new_items)
            extra_items.extend(keptN)
            total_useful += usefulN
            total_classified += len(new_items)
            cmap.ingest(insN)
            cmap.log_state(f"after targeted round (targets={targets})")
            assessN = cmap.assess()
            round_stats.append({"round": cmap.round, "source": "coverage_expansion", "targets": targets,
                                "classified": len(new_items), "insights": len(insN), "useful": usefulN,
                                "coverage": assessN})
            yield _sse("progress", {
                "stage": "coverage_round", "round": cmap.round, "source": "coverage_expansion",
                "targets": targets, "classified": len(new_items), "insights": len(insN),
                "useful_total": total_useful, "coverage": assessN,
                "ts_ms": int((perf_counter() - t0) * 1000),
            })

        # ---- Coverage report + honest warnings ----
        coverage_report = cmap.report() if cmap is not None else None
        cov_assess = (coverage_report or {}).get("assessment", {}) or {}
        target_met = total_useful >= useful_target
        insufficient = total_useful < min_useful
        if total_useful == 0:
            relevance_warning = "Couldn't confidently identify on-topic comments in the sample."
        elif insufficient:
            relevance_warning = (
                f"Insufficient data: only {total_useful} useful "
                f"review{'s' if total_useful != 1 else ''} about the product "
                f"(need at least {min_useful} for a confident read)."
            )

        rounds_used = cmap.round if cmap is not None else len(round_stats)
        adaptive_info = {
            "mode": "coverage_driven" if cmap is not None else "useful_count",
            "useful_target": useful_target,
            "min_useful": min_useful,
            "useful_found": total_useful,
            "rounds": rounds_used,
            "max_rounds": max_rounds,
            "total_classified": total_classified,
            "raw_fetched": raw_fetched_total,
            "max_raw_fetch": max_raw_fetch,
            "extra_useful_scraped": len(extra_items),
            "target_met": target_met,
            "insufficient": insufficient,
            "coverage": cov_assess,
            "per_round": round_stats,
            "deferred": True,
        }
        # Telemetry: the investigation outcome — rounds, raw fetched, useful (passed
        # relevance), coverage of the expected map, what was discovered, and the gaps.
        log.info(
            "[stream] coverage result: rounds=%d | raw_fetched=%d | useful=%d | "
            "expected_covered=%s/%s | discovered=%s | gaps=%s",
            rounds_used, raw_fetched_total, total_useful,
            cov_assess.get("expected_covered"), cov_assess.get("expected_total"),
            (coverage_report or {}).get("discovered_categories"),
            (coverage_report or {}).get("gaps"),
        )
        if coverage_report:
            log.info("[stream] coverage summary: %s", coverage_report.get("summary"))

        # Attach deep signals onto per_review, then aggregate across the analyzed
        # reviews PLUS any adaptively-found on-topic comments.
        for r in per_review_out:
            key = (r.get("original") or r.get("translated_text") or "").strip()
            if key in deep_by_text:
                r["deep"] = deep_by_text[key]
        agg_input = list(per_review_out) + [
            {"original": it["text"], "translated_text": it["text"], "deep": deep_by_text[it["text"]]}
            for it in extra_items if it["text"] in deep_by_text
        ]
        try:
            if aggregate_deep_signals is not None:
                overview["deep_signals"] = aggregate_deep_signals(agg_input)
        except Exception as e:
            log.warning("[stream] deep_signals aggregation failed (continuing): %s", e)

        # Deeper cross-section intelligence now that deep_signals + per-review `deep`
        # exist: aspect consensus, expectation-vs-reality, first-impression-vs-long-term
        # sentiment. Appended to the cross_insights computed earlier so they render as
        # CrossInsight cards when the `enriched` report merges in. Fail-open.
        try:
            from backend.insight.intelligence.synthesizer import find_deep_cross_insights
            extra_ci = find_deep_cross_insights(overview, per_review_out)
            if extra_ci:
                existing = overview.get("cross_insights") or []
                overview["cross_insights"] = list(existing) + extra_ci
                log.info("[stream] deep cross-insights: +%d (%s)", len(extra_ci),
                         [c.get("type") for c in extra_ci])
        except Exception as e:
            log.warning("[stream] deep cross-insights failed (continuing): %s", e)
        # Causal Chain Aggregation — group the deep classifier's causal_chains by
        # semantic similarity and rank by independent-reviewer confirmations.
        # Only possible here (Balanced/Deep), since Quick skips deep classification.
        try:
            from backend.insight.intelligence.causal_aggregator import aggregate_causal_findings
            from backend.api.endpoints.analyze_reviews import embedder as _causal_embedder
            chains = (overview.get("deep_signals") or {}).get("causal_chains") or []
            overview["causal_findings"] = aggregate_causal_findings(chains, embedder=_causal_embedder)
        except Exception as e:
            log.warning("[stream] causal aggregation failed (continuing): %s", e)
            overview["causal_findings"] = []
        overview["adaptive_scrape"] = adaptive_info
        if coverage_report is not None:
            overview["coverage_report"] = coverage_report
        if relevance_warning:
            overview["relevance_warning"] = relevance_warning

        # Re-persist the enriched report and stream it so the client merges it in.
        _persist(final)
        yield _sse("progress", {
            "stage": "deep_classify_done",
            "raw_fetched": raw_fetched_total,
            "passed_relevance": total_useful,
            "useful": total_useful,
            "target": useful_target,
            "target_reached": target_met,
            "insufficient": insufficient,
            "rounds": rounds_used,
            "coverage": cov_assess,
            "coverage_summary": (coverage_report or {}).get("summary"),
            "ts_ms": int((perf_counter() - t0) * 1000),
        })
        yield _sse("enriched", {"final_report": final})
    except Exception as e:
        log.warning("[stream] deferred enrichment failed (continuing): %s", e)
    return


@router.get("/run_pipeline/stream/_ping", tags=["InsightMesh", "Stream"])
def stream_ping() -> Dict[str, Any]:
    return {"ok": True, "format": "text/event-stream"}


@router.post(
    "/run_pipeline/stream",
    tags=["InsightMesh", "Stream"],
    summary="Run pipeline with real-time progress events (SSE)",
)
async def run_pipeline_stream(payload: RunInput, request: Request) -> StreamingResponse:
    """
    SSE-streamed pipeline run.

    Consume from JS:
        const res = await fetch('/api/insightmesh/run_pipeline/stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
            signal: abortController.signal,  // Phase A.2: aborts kill server work too
        });
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
            const {value, done} = await reader.read();
            if (done) break;
            buf += decoder.decode(value, {stream: true});
            // ... parse events from buf
        }
    """
    return StreamingResponse(
        _progress_pipeline(payload, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",          # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
