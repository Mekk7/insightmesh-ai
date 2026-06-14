# backend/api/endpoints/scrape_reviews.py
# YouTube scraping with time window + strictness-aware filtering + diagnostics.

import os
import re
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from dotenv import load_dotenv, find_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.utils.filtering import filter_comments, filter_and_metrics, filter_items_and_metrics

log = logging.getLogger("insightmesh.scrape_youtube")

# 1) Load environment
load_dotenv(find_dotenv())
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
# Note: We do NOT raise at import time anymore. The backend should boot even if YouTube
# is unconfigured (e.g., user only wants Reddit). The /scrape/youtube endpoint will
# return a clean 503 with a helpful message if the key is missing.

# Env-tunable caps (safe defaults keep quota predictable)
YT_MAX_VIDEOS_HARD   = int(os.getenv("YT_MAX_VIDEOS_HARD", "10"))   # per search call (SCRAPER_CONFIG wants up to 8)
YT_MAX_COMMENTS_HARD = int(os.getenv("YT_MAX_COMMENTS_HARD", "100")) # per video (kept)
YT_COMMENT_PAGES_CAP = int(os.getenv("YT_COMMENT_PAGES_CAP", "3"))   # pages of 100 comments

# Optional search defaults
YT_SEARCH_ORDER_DEFAULT   = os.getenv("YT_SEARCH_ORDER_DEFAULT", "viewCount")  # viewCount|date|relevance|rating
YT_VIDEO_DURATION_DEFAULT = os.getenv("YT_VIDEO_DURATION_DEFAULT", "any")      # any|short|medium|long

router = APIRouter()

# 2) Helpers
def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(n)))

def _parse_iso_utc(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = ts.strip()
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(s)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _to_rfc3339(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    # YouTube expects RFC3339 like 2024-05-01T00:00:00Z
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _clamp_strictness(s: Optional[str]) -> str:
    # Accept ultra; downstream filter can decide how to use it
    allowed = {"low", "normal", "high", "ultra"}
    if not s:
        return "normal"
    s = str(s).lower().strip()
    return s if s in allowed else "normal"

def _clamp_order(o: Optional[str]) -> str:
    allowed = {"viewcount", "date", "relevance", "rating"}
    if not o:
        return YT_SEARCH_ORDER_DEFAULT
    o = str(o).lower().strip()
    return o if o in allowed else YT_SEARCH_ORDER_DEFAULT

def _clamp_video_duration(v: Optional[str]) -> str:
    allowed = {"any", "short", "medium", "long"}
    if not v:
        return YT_VIDEO_DURATION_DEFAULT
    v = str(v).lower().strip()
    return v if v in allowed else YT_VIDEO_DURATION_DEFAULT

# 3) Request schema
class YouTubeScrapeInput(BaseModel):
    query:        str
    max_videos:   int = 2
    max_comments: int = 20
    # Time window (ISO/RFC3339); we’ll pass after to API and filter before client-side
    published_after:  Optional[str] = None
    published_before: Optional[str] = None
    # Filtering strictness for de-noising comments
    strictness:       Optional[str] = None  # low|normal|high|ultra
    # Optional search tunables
    order:            Optional[str] = None  # viewCount|date|relevance|rating
    video_duration:   Optional[str] = None  # any|short|medium|long
    # Optional per-request override for page cap
    comment_pages_cap: Optional[int] = None
    # Pull reply chains too (inline replies on each top-level comment). Off = top-level only.
    fetch_replies:    bool = False
    # Pagination: opaque YouTube search pageToken. Pass the `next_page_token` from a prior
    # response to fetch the NEXT page of videos (fresh ones), never re-fetching the same.
    page_token:       Optional[str] = None

@router.post(
    "/scrape/youtube",
    response_model=Dict[str, Any],
    summary="Search & Scrape YouTube Comments (time-aware, filtered)",
)
def scrape_youtube(input: YouTubeScrapeInput):
    """Blocking SDK → FastAPI runs in a threadpool."""
    # Clamp to keep quota predictable
    max_videos   = _clamp(input.max_videos,   1, YT_MAX_VIDEOS_HARD)
    max_comments = _clamp(input.max_comments, 1, YT_MAX_COMMENTS_HARD)
    strictness   = _clamp_strictness(input.strictness)
    order        = _clamp_order(input.order)
    video_dur    = _clamp_video_duration(input.video_duration)
    pages_cap    = _clamp(input.comment_pages_cap or YT_COMMENT_PAGES_CAP, 1, 10)

    # Lazy credential check — return 503 instead of crashing at import
    if not YOUTUBE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail={
                "source": "youtube",
                "error": "YouTube client not configured",
                "hint": "Set YOUTUBE_API_KEY in your .env file. Get one at: https://console.cloud.google.com/apis/credentials",
            },
        )

    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    except Exception as e:
        raise HTTPException(
            500,
            detail={"source": "youtube", "error": f"Client init failure: {e}"}
        )

    # Parse time window
    dt_after  = _parse_iso_utc(input.published_after)
    dt_before = _parse_iso_utc(input.published_before)
    if dt_after and dt_before and dt_after > dt_before:
        # Trivial guard: swap to avoid empty results if caller inverts dates
        dt_after, dt_before = dt_before, dt_after
    rfc3339_after = _to_rfc3339(dt_after) if dt_after else None

    # 1) Search videos by query (apply publishedAfter if available)
    try:
        search_params = dict(
            part="snippet",
            q=input.query,
            type="video",
            maxResults=max_videos,
            order=order,
            videoDuration=video_dur,
        )
        if rfc3339_after:
            # Supported by YouTube Data API v3
            search_params["publishedAfter"] = rfc3339_after
        if input.page_token:
            search_params["pageToken"] = input.page_token

        search_resp = youtube.search().list(**search_params).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", 400)
        raise HTTPException(status_code=status, detail={"source": "youtube", "error": str(e)})

    # Cursor for the NEXT page of video results (None when exhausted).
    next_page_token = (search_resp or {}).get("nextPageToken")
    items = (search_resp or {}).get("items", []) or []
    if not items:
        return {
            "source": "youtube",
            "query": input.query,
            "results": [],
            "published_after": input.published_after,
            "published_before": input.published_before,
            "strictness": strictness,
            "order": order,
            "video_duration": video_dur,
            "max_videos": max_videos,
            "max_comments": max_comments,
        }

    # Optionally filter search results by published_before client-side
    filtered_items = []
    for item in items:
        snip = item.get("snippet") or {}
        published_at = snip.get("publishedAt") or ""
        if dt_before:
            try:
                pt = _parse_iso_utc(published_at)
                if pt and pt > dt_before:
                    continue  # skip newer than upper bound
            except Exception:
                pass
        filtered_items.append(item)

    if not filtered_items:
        return {
            "source": "youtube",
            "query": input.query,
            "results": [],
            "published_after": input.published_after,
            "published_before": input.published_before,
            "strictness": strictness,
            "order": order,
            "video_duration": video_dur,
            "max_videos": max_videos,
            "max_comments": max_comments,
        }

    results: List[Dict[str, Any]] = []

    # --- Video-level relevance filter ---
    # Extract the core product name words for matching against video titles.
    # "Apple Vision Pro review" → {"apple", "vision", "pro"} (strip common query suffixes)
    _QUERY_NOISE = {"review", "reviews", "problems", "issues", "worth", "it", "is",
                    "pros", "cons", "and", "the", "a", "an", "vs", "or", "honest",
                    "long", "term", "after", "months", "owner", "reliability", "common"}
    _q_words = [w.lower() for w in re.findall(r'[A-Za-z0-9]+', input.query) if w.lower() not in _QUERY_NOISE]
    # Require at least N of the product name words to appear in the video title.
    # For "Apple Vision Pro" (3 words), require 2+. For single-word products, require 1.
    _match_threshold = max(1, len(_q_words) - 1) if len(_q_words) > 1 else 1
    log.info("[yt] video relevance filter: query_words=%s threshold=%d", _q_words, _match_threshold)

    skipped_videos = 0
    for item in filtered_items:
        id_block = item.get("id") or {}
        snip     = item.get("snippet") or {}
        video_id = (id_block.get("videoId") or "").strip()
        if not video_id:
            continue  # skip odd result rows

        title        = snip.get("title") or ""
        channel      = snip.get("channelTitle") or ""
        published_at = snip.get("publishedAt") or ""
        description  = snip.get("description") or ""

        # Check if the video title (or description first 500 chars) is about the product.
        # A video titled "Tesla Lock Unlock Compilation" should NOT be scraped for
        # Apple Vision Pro reviews even if YouTube search returned it.
        _title_lower = (title + " " + description[:500]).lower()
        _title_match = sum(1 for w in _q_words if w in _title_lower)
        if _title_match < _match_threshold:
            skipped_videos += 1
            log.info("[yt] SKIP video '%s' — only %d/%d product words matched (threshold=%d)",
                     title[:80], _title_match, len(_q_words), _match_threshold)
            continue

        kept: List[str] = []
        kept_items: List[Dict[str, Any]] = []
        dropped_by_reason: Dict[str, int] = {}
        lang_hist: Dict[str, int] = {}

        next_page = None
        pages = 0
        comments_scanned = 0

        # When fetching replies, ask the API to inline them on each thread.
        thread_part = "snippet,replies" if input.fetch_replies else "snippet"

        # Pull up to (<= pages_cap) pages of top-level comments
        while len(kept) < max_comments and pages < pages_cap:
            try:
                threads = youtube.commentThreads().list(
                    part=thread_part,
                    videoId=video_id,
                    textFormat="plainText",
                    maxResults=100,
                    pageToken=next_page,
                ).execute()
            except HttpError:
                break  # comments disabled / quota hiccup → skip politely

            t_items = (threads or {}).get("items", []) or []
            if not t_items:
                break

            # Extract text + per-comment metadata for top-level comments (and,
            # when fetch_replies is on, the reply chain inlined by the API — up to
            # ~5 replies per thread without a second paginated call).
            page_items: List[Dict[str, Any]] = []
            for it in t_items:
                tlc = ((it or {}).get("snippet") or {}).get("topLevelComment", {}) or {}
                sn = tlc.get("snippet", {}) or {}
                text = sn.get("textDisplay")
                if not text:
                    continue
                page_items.append({
                    "text": text,
                    "published_at": sn.get("publishedAt"),
                    "author": sn.get("authorDisplayName"),
                    "like_count": int(sn.get("likeCount") or 0),
                    "video_id": video_id,
                    "comment_id": tlc.get("id") or (it or {}).get("id"),  # stable ID for cross-round dedup
                    "platform": "youtube",
                    "source_title": title,  # video title → lets the analyzer separate product feedback from video reactions
                })
                if input.fetch_replies:
                    for rep in ((it or {}).get("replies") or {}).get("comments", []) or []:
                        rsn = (rep or {}).get("snippet") or {}
                        rtext = rsn.get("textDisplay")
                        if not rtext:
                            continue
                        page_items.append({
                            "text": rtext,
                            "published_at": rsn.get("publishedAt"),
                            "author": rsn.get("authorDisplayName"),
                            "like_count": int(rsn.get("likeCount") or 0),
                            "video_id": video_id,
                            "comment_id": (rep or {}).get("id"),
                            "platform": "youtube",
                            "is_reply": True,
                            "source_title": title,
                        })

            comments_scanned += len(page_items)

            need = max(0, max_comments - len(kept))
            if need and page_items:
                try:
                    page_kept_items, page_dropped, page_langs = filter_items_and_metrics(
                        page_items,
                        desired_count=need,
                        strictness=strictness,
                    )
                except Exception:
                    # Fail-safe: fall back to text-only filtering on this page
                    page_texts = [it["text"] for it in page_items]
                    page_kept, page_dropped, page_langs = filter_and_metrics(page_texts, desired_count=need, strictness=strictness)
                    page_kept_items = [{"text": t, "platform": "youtube", "video_id": video_id} for t in page_kept]

                # accumulate
                kept_items.extend(page_kept_items)
                kept.extend([it["text"] for it in page_kept_items])
                for k, v in page_dropped.items():
                    dropped_by_reason[k] = dropped_by_reason.get(k, 0) + v
                for k, v in page_langs.items():
                    lang_hist[k] = lang_hist.get(k, 0) + v

            next_page = (threads or {}).get("nextPageToken")
            pages += 1
            if not next_page:
                break

        results.append({
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "published": published_at,

            # Preferred schema (pipeline relies on this)
            "kept_count": len(kept),
            "kept_comments": kept,
            "kept_items": kept_items,  # NEW: per-comment metadata

            # Optional diagnostics (non-breaking)
            "dropped_by_reason": dropped_by_reason,
            "lang_hist": lang_hist,
            "comments_scanned": comments_scanned,

            # Legacy schema
            "comments_count": len(kept),
            "comments": kept,
        })

    return {
        "source": "youtube",
        "query": input.query,
        "results": results,
        "published_after": input.published_after,
        "published_before": input.published_before,
        "strictness": strictness,
        "order": order,
        "video_duration": video_dur,
        "max_videos": max_videos,
        "max_comments": max_comments,
        "comment_pages_cap": pages_cap,
        "next_page_token": next_page_token,
        "skipped_irrelevant_videos": skipped_videos,
    }
