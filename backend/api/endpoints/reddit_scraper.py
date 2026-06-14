# backend/api/endpoints/reddit_scraper.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
import os
import time

import praw
from praw.models import MoreComments
from dotenv import load_dotenv, find_dotenv

from backend.utils.filtering import filter_and_metrics, filter_items_and_metrics
from datetime import datetime, timezone

# -------------------- Setup --------------------
load_dotenv(find_dotenv())

# Tunables (override via env as needed)
MAX_POSTS_PER_SUB_DEFAULT = int(os.getenv("REDDIT_MAX_POSTS_PER_SUB", "3"))   # posts to scan per subreddit
MAX_POSTS_PER_SUB_CAP     = int(os.getenv("REDDIT_MAX_POSTS_PER_SUB_CAP", "25"))
BUDGET_SEC                = float(os.getenv("REDDIT_BUDGET_SEC", "5.0"))      # overall per-request budget
TOPLEVEL_PAD              = int(os.getenv("REDDIT_TOPLEVEL_PAD", "2"))        # small overfetch for filtering
DEFAULT_SUBS              = ["teslamotors", "electricvehicles", "teslalounge"]

router = APIRouter()

# Lazy client init so missing creds don't crash app import
_reddit_client: Optional[praw.Reddit] = None
_reddit_status: str = "uninitialized"

def _build_reddit() -> Tuple[Optional[praw.Reddit], str]:
    client_id     = os.getenv("REDDIT_CLIENT_ID") or ""
    client_secret = os.getenv("REDDIT_CLIENT_SECRET") or ""
    username      = os.getenv("REDDIT_USERNAME") or ""
    password      = os.getenv("REDDIT_PASSWORD") or ""
    user_agent    = os.getenv("REDDIT_USER_AGENT", "insightmesh/0.1") or "insightmesh/0.1"

    if not (client_id and client_secret and user_agent):
        return None, "missing_credentials"

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username or None,
            password=password or None,
            user_agent=user_agent,
            check_for_async=False,  # prevents event loop mis-detection
        )
        # Prefer read-only (safer, sufficient for scraping)
        try:
            reddit.read_only = True
        except Exception:
            pass

        # quick no-op access to confirm client works
        _ = reddit.read_only
        return reddit, "ok"
    except Exception as e:
        return None, f"init_error: {e}"

def _get_reddit_or_error() -> praw.Reddit:
    global _reddit_client, _reddit_status
    if _reddit_client is None:
        _reddit_client, _reddit_status = _build_reddit()
    if _reddit_client is None:
        # 503 makes it clear the service isn't configured, not a client error
        raise HTTPException(
            status_code=503,
            detail={
                "source": "reddit",
                "error": "Reddit client not available",
                "status": _reddit_status,
                "hint": "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT (and optionally REDDIT_USERNAME/REDDIT_PASSWORD)"
            }
        )
    return _reddit_client

# -------------------- Models --------------------
class RedditScrapeInput(BaseModel):
    subreddit: Optional[str] = None           # legacy single
    subreddits: Optional[List[str]] = None    # preferred multi
    query: Optional[str] = None
    limit: int = 20                           # desired kept items (comments/text)
    # NEW (optional, backwards-compatible):
    time_filter: Optional[str] = None         # hour|day|week|month|year|all
    strictness: Optional[str] = None          # low|normal|high
    comments_mode: Optional[str] = None       # "top" | "all" | "0" (no comments; use title/selftext)
    max_posts: Optional[int] = None           # posts to scan per subreddit (overrides env default)
    sort: Optional[str] = None                # search sort: relevance|top|new|hot|comments
    max_comments_per_post: Optional[int] = None  # cap kept comments per thread (diversity)
    after: Optional[str] = None               # pagination cursor (submission fullname, e.g. t3_xxx);
                                              # pass back `next_after` to fetch the NEXT page of threads

# -------------------- Helpers --------------------
def _clean_sub(s: str) -> str:
    # keep only letters/digits/_- ; lowercase; default to 'all' if empty
    s = "".join(ch for ch in (s or "") if ch.isalnum() or ch in "_-").lower()
    return s or "all"

def _choose_subs(inp: RedditScrapeInput) -> List[str]:
    if inp.subreddits and len(inp.subreddits) > 0:
        subs = inp.subreddits
    elif inp.subreddit:
        subs = [inp.subreddit]
    else:
        subs = DEFAULT_SUBS[:]
    # normalize, de-dupe, always include 'all'
    subs = [_clean_sub(s) for s in subs]
    if "all" not in subs:
        subs.insert(0, "all")
    # de-dupe preserving order
    seen, out = set(), []
    for s in subs:
        if s not in seen:
            seen.add(s); out.append(s)
    # keep small and tight
    return out[:5]

def _clamp_time_filter(tf: Optional[str]) -> str:
    allowed = {"hour", "day", "week", "month", "year", "all"}
    if not tf:
        return "month"
    tf = str(tf).lower().strip()
    return tf if tf in allowed else "month"

def _clamp_strictness(st: Optional[str]) -> str:
    allowed = {"low", "normal", "high"}
    if not st:
        return "normal"
    st = str(st).lower().strip()
    return st if st in allowed else "normal"

def _clamp_comments_mode(cm: Optional[str]) -> str:
    if not cm:
        return "top"
    cm = str(cm).lower().strip()
    return cm if cm in {"top", "all", "0"} else "top"

def _clamp_sort(s: Optional[str]) -> str:
    allowed = {"relevance", "top", "new", "hot", "comments"}
    if not s:
        return "relevance"
    s = str(s).lower().strip()
    return s if s in allowed else "relevance"

def _clamp_positive(n: Optional[int], default_val: int, lo: int, hi: int) -> int:
    try:
        v = int(n) if n is not None else int(default_val)
    except Exception:
        v = int(default_val)
    return max(lo, min(hi, v))

# -------------------- Routes --------------------
@router.get("/scrape/reddit/_ping")
def reddit_ping():
    # Light ping to see if creds are wired
    try:
        _get_reddit_or_error()
        return {"ok": True, "status": "ready"}
    except HTTPException as e:
        return {"ok": False, "status": "not_ready", "detail": e.detail}

@router.post(
    "/scrape/reddit",
    response_model=Dict[str, Any],
    summary="Scrape Reddit Comments (FAST, thread-safe, time-filter aware)",
)
def scrape_reddit(input: RedditScrapeInput) -> Dict[str, Any]:
    t0 = time.perf_counter()
    reddit = _get_reddit_or_error()

    subs = _choose_subs(input)

    # clamp requested "limit" (desired kept items)
    limit = _clamp_positive(input.limit, 20, 1, 200)

    # clamp per-sub posts scan override
    posts_per_sub = _clamp_positive(
        input.max_posts,
        MAX_POSTS_PER_SUB_DEFAULT,
        1,
        MAX_POSTS_PER_SUB_CAP,
    )

    time_filter   = _clamp_time_filter(input.time_filter)
    strictness    = _clamp_strictness(input.strictness)
    comments_mode = _clamp_comments_mode(input.comments_mode)
    sort_mode     = _clamp_sort(input.sort)
    # Per-thread cap so one hot thread can't supply the whole sample (diversity).
    per_post_cap  = _clamp_positive(input.max_comments_per_post, limit, 1, 200) if input.max_comments_per_post else limit

    kept: List[str] = []
    kept_items: List[Dict[str, Any]] = []
    dropped_by_reason: Dict[str, int] = {}
    lang_hist: Dict[str, int] = {}
    budget_hit = False

    # Diagnostics
    posts_considered = 0
    comments_scanned = 0
    per_sub_counts: Dict[str, Dict[str, int]] = {}
    last_fullname: Optional[str] = None  # pagination cursor returned as next_after

    try:
        for s in subs:
            if len(kept) >= limit:
                break
            if (time.perf_counter() - t0) > BUDGET_SEC:
                budget_hit = True
                break

            sub = reddit.subreddit(s)
            per_sub_counts[s] = {"posts": 0, "comments": 0}

            # choose posts generator (respect time_filter). `after` paginates the listing
            # forward (PRAW listing param) so a later round pulls the NEXT page of threads.
            # NOTE: PRAW chokes on params=None, so only pass it when we actually have a cursor.
            _extra = {"params": {"after": input.after}} if input.after else {}
            if input.query:
                posts_iter = sub.search(
                    query=input.query, sort=sort_mode, time_filter=time_filter,
                    limit=posts_per_sub, **_extra,
                )
            else:
                posts_iter = sub.top(time_filter=time_filter, limit=posts_per_sub, **_extra)

            for submission in posts_iter:
                posts_considered += 1
                per_sub_counts[s]["posts"] += 1
                last_fullname = getattr(submission, "fullname", None) or last_fullname

                if len(kept) >= limit:
                    break
                if (time.perf_counter() - t0) > BUDGET_SEC:
                    budget_hit = True
                    break

                raw_batch: List[Dict[str, Any]] = []

                if comments_mode == "0":
                    # No comments — use post title/selftext as content
                    title = getattr(submission, "title", None)
                    selftext = getattr(submission, "selftext", None)
                    sub_created = getattr(submission, "created_utc", None)
                    sub_published_at = (
                        datetime.fromtimestamp(float(sub_created), tz=timezone.utc).isoformat()
                        if sub_created else None
                    )
                    sub_score = int(getattr(submission, "score", 0) or 0)
                    sub_id = getattr(submission, "id", None)
                    sub_author = getattr(getattr(submission, "author", None), "name", None)
                    if title and isinstance(title, str) and title.strip():
                        raw_batch.append({
                            "text": title,
                            "published_at": sub_published_at,
                            "author": sub_author,
                            "score": sub_score,
                            "subreddit": s,
                            "post_id": sub_id,
                            "comment_id": f"{sub_id}_title" if sub_id else None,
                            "is_submission_title": True,
                            "platform": "reddit",
                        })
                    if selftext and isinstance(selftext, str) and selftext.strip():
                        raw_batch.append({
                            "text": selftext,
                            "published_at": sub_published_at,
                            "author": sub_author,
                            "score": sub_score,
                            "subreddit": s,
                            "post_id": sub_id,
                            "comment_id": f"{sub_id}_body" if sub_id else None,
                            "is_submission_body": True,
                            "platform": "reddit",
                        })
                else:
                    # Comments path
                    try:
                        if comments_mode == "top":
                            submission.comments.replace_more(limit=0)
                            to_iter = submission.comments
                        else:
                            submission.comments.replace_more(limit=None)
                            to_iter = submission.comments.list()
                    except Exception:
                        to_iter = []

                    sub_id = getattr(submission, "id", None)
                    for c in to_iter:
                        if isinstance(c, MoreComments):
                            continue
                        body = getattr(c, "body", None)
                        if not body:
                            continue
                        c_created = getattr(c, "created_utc", None)
                        c_published_at = (
                            datetime.fromtimestamp(float(c_created), tz=timezone.utc).isoformat()
                            if c_created else None
                        )
                        raw_batch.append({
                            "text": body,
                            "published_at": c_published_at,
                            "author": getattr(getattr(c, "author", None), "name", None),
                            "score": int(getattr(c, "score", 0) or 0),
                            "subreddit": s,
                            "post_id": sub_id,
                            "comment_id": getattr(c, "id", None),  # stable ID for cross-round dedup
                            "platform": "reddit",
                        })
                        # Stop this thread at the per-thread cap OR the remaining
                        # global budget (whichever is smaller) so one thread can't
                        # dominate and we still spread across `max_posts` threads.
                        if len(raw_batch) >= min(per_post_cap, (limit - len(kept)) + TOPLEVEL_PAD):
                            break

                if not raw_batch:
                    continue

                comments_scanned += len(raw_batch)
                per_sub_counts[s]["comments"] += len(raw_batch)

                # Filter items, preserving metadata
                try:
                    page_kept_items, page_dropped, page_langs = filter_items_and_metrics(
                        raw_batch,
                        desired_count=max(0, limit - len(kept)),
                        strictness=strictness,
                    )
                except Exception:
                    # Fallback to text-only filtering
                    page_texts = [it["text"] for it in raw_batch]
                    page_kept, page_dropped, page_langs = filter_and_metrics(
                        page_texts, desired_count=max(0, limit - len(kept)), strictness=strictness
                    )
                    page_kept_items = [{"text": t, "platform": "reddit", "subreddit": s} for t in page_kept]

                kept_items.extend(page_kept_items)
                kept.extend([it["text"] for it in page_kept_items])
                for k, v in page_dropped.items():
                    dropped_by_reason[k] = dropped_by_reason.get(k, 0) + v
                for k, v in page_langs.items():
                    lang_hist[k] = lang_hist.get(k, 0) + v

                if len(kept) >= limit:
                    break

    except Exception as e:
        # PRAW can occasionally raise various network/auth exceptions — surface clearly
        raise HTTPException(status_code=400, detail={"source": "reddit", "error": str(e)})

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "source": "reddit",
        "subreddits": subs,
        "query": input.query,

        # Preferred schema used by pipeline
        "kept_count": len(kept),
        "kept_comments": kept,
        "kept_items": kept_items,  # NEW: per-comment metadata
        "dropped_by_reason": dropped_by_reason,
        "lang_hist": lang_hist,

        # Debug hints (non-breaking)
        "elapsed_ms": elapsed_ms,
        "budget_hit": budget_hit,
        "time_filter": time_filter,
        "strictness": strictness,
        "comments_mode": comments_mode,
        "sort": sort_mode,
        "max_comments_per_post": per_post_cap,
        "max_posts_per_sub": posts_per_sub,
        "next_after": last_fullname,  # pass back as `after` to fetch the next page of threads
        "diagnostics": {
            "posts_considered": posts_considered,
            "comments_scanned": comments_scanned,
            "per_sub": per_sub_counts,
        },

        # Legacy schema (for backward compatibility)
        "count": len(kept),
        "comments": kept,
    }
