# backend/api/insightmesh/plugins.py
# Dynamic, safe plugin launcher with env-tunable limits.
# Supports context-driven launch (query/tags/time window/strictness),
# plus a backward-compatible adapter for callers that pass only tags.

from typing import Dict, List, Any, Optional, Union
import os
import re
from datetime import datetime, timezone

# (Imports kept so your app can mount routers elsewhere if needed)
from backend.api.endpoints.scrape_reviews import router as youtube_router  # noqa: F401
from backend.api.endpoints.reddit_scraper import router as reddit_router  # noqa: F401

# Central tuneable scraper config (volume, replies, countries, query variants, targets).
from backend.api.insightmesh.scraper_config import platform_cfg

# ---- Env-configurable limits (fallbacks match your current defaults) ----
YT_MAX_VIDEOS_ENV   = int(os.getenv("YT_MAX_VIDEOS", "2"))
YT_MAX_COMMENTS_ENV = int(os.getenv("YT_MAX_COMMENTS", "20"))
REDDIT_LIMIT_ENV    = int(os.getenv("REDDIT_LIMIT", "20"))

# Reasonable hard caps (defensive)
YT_MAX_VIDEOS_CAP   = int(os.getenv("YT_MAX_VIDEOS_CAP", "10"))
YT_MAX_COMMENTS_CAP = int(os.getenv("YT_MAX_COMMENTS_CAP", "200"))
REDDIT_LIMIT_CAP    = int(os.getenv("REDDIT_LIMIT_CAP", "200"))

# ======================================================================
# Helpers
# ======================================================================

def _join_tags(tags: Optional[List[str]]) -> str:
    if not tags:
        return ""
    seen = set()
    parts: List[str] = []
    for t in tags:
        t = str(t or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(t)
    q = " ".join(parts)
    return q[:120] if len(q) > 120 else q

def _normalize_ctx(ctx_or_tags: Union[Dict[str, Any], List[str], str, None]) -> Dict[str, Any]:
    if isinstance(ctx_or_tags, dict):
        ctx = dict(ctx_or_tags)  # shallow copy
        ctx.setdefault("query", None)
        ctx.setdefault("tags", [])
        ctx.setdefault("platform_settings", {})
        return ctx
    if isinstance(ctx_or_tags, list):
        return {"query": None, "tags": list(ctx_or_tags), "mode": "company", "platform_settings": {}}
    if isinstance(ctx_or_tags, str):
        return {"query": ctx_or_tags.strip(), "tags": [], "mode": "consumer", "platform_settings": {}}
    return {"query": None, "tags": [], "platform_settings": {}}

def _make_query_from_ctx(ctx: Dict[str, Any], extra: str = "") -> str:
    """Build the search query from ctx.

    `extra` lets a specific platform bias the search term (e.g. YouTube appends
    "review" so the algorithm returns review videos instead of random gameplay /
    meme content). It is applied ONLY when a caller asks for it — Reddit and the
    App Store pass no extra, since "<product> review" would over-narrow a
    subreddit search or break an app-name store lookup.
    """
    q = (ctx.get("query") or "").strip()
    if not q:
        q = _join_tags(ctx.get("tags"))
    extra = (extra or "").strip()
    if extra and q and extra.lower() not in q.lower():
        q = f"{q} {extra}"
    return q[:120] if len(q) > 120 else q

def _clean_sub_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", s or "").lower()

# Generic EV / non-Model-Y Tesla fallback (broad, safe).
_EV_DEFAULT = ["electricvehicles", "teslamotors", "teslalounge"]
# Tesla (esp. Model Y) → the on-topic product subreddit FIRST, then the broad EV
# community. r/teslamotors is a mixed all-models sub whose search bleeds Model 3 /
# Cybertruck threads into a Model Y query; r/TeslaModelY keeps the signal on-product.
_TESLA_DEFAULT = ["TeslaModelY", "electricvehicles"]
_PLAYSTATION_DEFAULT = ["PS5", "playstation", "PS4"]

# Keyword → subreddit candidates (broad, safe defaults)
_SUB_MAP: Dict[str, List[str]] = {
    # Autos / EV
    "tesla": _TESLA_DEFAULT,
    "model y": _TESLA_DEFAULT,
    "model 3": _EV_DEFAULT,
    "model s": _EV_DEFAULT,
    "model x": _EV_DEFAULT,
    "cybertruck": _EV_DEFAULT,
    "fsd": _EV_DEFAULT,
    "rivian": ["rivian"],
    "ford": ["ford", "electricvehicles"],
    "mustang mach": ["mache", "electricvehicles"],
    "lightning": ["ford", "electricvehicles"],
    "bmw": ["BMW"],
    "mercedes": ["mercedes_benz"],
    "toyota": ["Toyota"],
    "honda": ["Honda"],
    "audi": ["Audi"],
    "porsche": ["Porsche"],
    "car": ["cars", "whatcarshouldIbuy"],

    # Consumer tech
    "headphone": ["headphones", "audiophile"],
    "headphones": ["headphones", "audiophile"],
    "earbud": ["headphones", "audiophile"],
    "sony": ["headphones", "audiophile", "sony"],
    "wh-1000xm": ["headphones", "audiophile", "sony"],
    "iphone": ["iphone", "apple"],
    "apple": ["apple"],
    "android": ["android"],
    "samsung": ["samsung"],
    "pixel": ["GooglePixel"],
    "laptop": ["laptops", "buildapc"],
    "pc": ["buildapc"],
    "gaming": ["pcgaming"],
    "camera": ["photography", "cameras"],
    "canon": ["photography", "canon"],
    "nikon": ["photography", "nikon"],
    "fuji": ["photography", "fujifilm"],

    # Gaming consoles (keys are matched as substrings against a lowercased query)
    "ps5": _PLAYSTATION_DEFAULT,
    "ps4": _PLAYSTATION_DEFAULT,
    "playstation": _PLAYSTATION_DEFAULT,          # "PlayStation 5", "PlayStation5"
    "play station": _PLAYSTATION_DEFAULT,         # "play station 5" (spaced)
    "dualsense": _PLAYSTATION_DEFAULT,
    "xbox": ["xboxseriesx", "xbox"],
    "series x": ["xboxseriesx", "xbox"],
    "series s": ["xboxseriesx", "xbox"],
    "nintendo switch": ["NintendoSwitch"],
    "switch 2": ["NintendoSwitch"],
    "steam deck": ["SteamDeck"],
    "console": ["consoles", "gaming"],

    # Home & lifestyle
    "skincare": ["SkincareAddiction"],
    "makeup": ["MakeupAddiction"],
    "vacuum": ["VacuumCleaners"],
    "coffee": ["Coffee"],
}

def _choose_subreddits_from_query(ctx: Dict[str, Any]) -> List[str]:
    text = " ".join(ctx.get("tags") or [])
    q = ctx.get("query") or ""
    tag_text = (f"{text} {q}").lower()

    subs: List[str] = []
    for key, candidates in _SUB_MAP.items():
        if key in tag_text:
            subs.extend(candidates)

    if not subs and any(k in tag_text for k in ["ev", "electric", "tesla"]):
        subs.extend(_EV_DEFAULT)

    # Product-specific subreddits FIRST (highest signal), then 'all' as a fallback.
    # The scraper scans in order and stops once it has enough, so leading with r/PS5
    # and r/playstation means a "PS5" search actually pulls from those communities
    # instead of being saturated by generic r/all results.
    out: List[str] = []
    seen = set()
    for s in (subs + ["all"]):
        c = _clean_sub_name(s)
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)

    # Nothing matched the map → broaden beyond r/all with a couple generic subs.
    if out == ["all"]:
        out += ["headphones", "laptops"]
        out = list(dict.fromkeys(out))

    return out[:5]

def _parse_iso_when(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    txt = s.strip().rstrip("Z")
    try:
        return datetime.fromisoformat(txt).replace(tzinfo=timezone.utc) \
            if "T" in txt else datetime.fromisoformat(txt).replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _map_time_filter(time_from: Optional[str], time_to: Optional[str]) -> Optional[str]:
    start = _parse_iso_when(time_from)
    end = _parse_iso_when(time_to)
    if not start and not end:
        return None
    now = datetime.now(timezone.utc)
    if start and end:
        delta = (end - start)
    elif start and not end:
        delta = (now - start)
    elif end and not start:
        delta = (end - (end.replace(hour=0, minute=0, second=0) if end else now))
    else:
        return None

    seconds = max(delta.total_seconds(), 0.0)
    if seconds <= 3600:
        return "hour"
    if seconds <= 86400:
        return "day"
    if seconds <= 7 * 86400:
        return "week"
    if seconds <= 30 * 86400:
        return "month"
    if seconds <= 365 * 86400:
        return "year"
    return "all"

def _int_in_bounds(val: Any, default_val: int, lo: int, hi: int) -> int:
    try:
        v = int(val)
    except Exception:
        v = default_val
    return max(lo, min(hi, v))

def _list_from_maybe_csv(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        items = list(x)
    else:
        items = str(x).split(",")
    cleaned: List[str] = []
    seen = set()
    for s in items:
        k = _clean_sub_name(str(s))
        if not k or k in seen:
            continue
        seen.add(k)
        cleaned.append(k)
    return cleaned

def _map_strictness_for_scraper(s: Optional[str]) -> Optional[str]:
    """
    Analyzer may use 'ultra', while scrapers accept only low|normal|high.
    Map 'ultra' → 'high' so we don't silently drop to 'normal'.
    """
    if not s:
        return None
    s = str(s).lower().strip()
    if s == "ultra":
        return "high"
    return s if s in {"low", "normal", "high"} else "normal"

# ======================================================================
# Launchers
# ======================================================================

def _launch_youtube(ctx_or_tags: Union[Dict[str, Any], List[str], str, None]):
    ctx = _normalize_ctx(ctx_or_tags)
    # Append "review" so YouTube surfaces review videos, not random gameplay/memes.
    query = _make_query_from_ctx(ctx, extra="review")
    ps = (ctx.get("platform_settings") or {}).get("youtube", {}) or {}
    cfg = platform_cfg("youtube")  # SCRAPER_CONFIG defaults (max_videos=8, max_comments=50, ...)

    # Defaults from SCRAPER_CONFIG (env-fallback) with safe caps; platform_settings still wins.
    max_videos = _int_in_bounds(
        ps.get("max_videos"),
        cfg.get("max_videos", YT_MAX_VIDEOS_ENV),
        1,
        YT_MAX_VIDEOS_CAP,
    )
    # support both names
    max_comments = ps.get("max_comments_per_video", ps.get("max_comments"))
    max_comments = _int_in_bounds(
        max_comments,
        cfg.get("max_comments_per_video", YT_MAX_COMMENTS_ENV),
        1,
        YT_MAX_COMMENTS_CAP,
    )

    # Prefer explicit platform_settings timebox; else ctx time window
    published_after  = ps.get("published_after")  or ctx.get("yt_published_after") or ctx.get("time_from")
    published_before = ps.get("published_before") or ctx.get("yt_published_before") or ctx.get("time_to")

    body: Dict[str, Any] = {
        "query": query,
        "max_videos": max_videos,
        "max_comments": max_comments,
        "fetch_replies": bool(ps.get("fetch_replies", cfg.get("fetch_replies", False))),
    }
    # Pagination cursor: the keep-going loop passes the prior response's next_page_token
    # back via ctx["yt_page_token"] to fetch the NEXT page of videos (never repeats).
    page_token = ctx.get("yt_page_token") or ps.get("page_token")
    if page_token:
        body["page_token"] = page_token
    if published_after:
        body["published_after"] = published_after
    if published_before:
        body["published_before"] = published_before

    # Scraper strictness mapping (ultra → high)
    scr_strict = _map_strictness_for_scraper(ctx.get("strictness"))
    if scr_strict:
        body["strictness"] = scr_strict

    return "/reviews/scrape/youtube", body

def _launch_reddit(ctx_or_tags: Union[Dict[str, Any], List[str], str, None]):
    ctx = _normalize_ctx(ctx_or_tags)
    query = _make_query_from_ctx(ctx)
    ps = (ctx.get("platform_settings") or {}).get("reddit", {}) or {}
    cfg = platform_cfg("reddit")  # max_threads, max_comments_per_thread, include_replies, sort_modes

    # Subreddits: platform_settings override → heuristic from query/tags
    override_subs = _list_from_maybe_csv(ps.get("subreddits"))
    subreddits = override_subs if override_subs else _choose_subreddits_from_query(ctx)

    max_threads = _int_in_bounds(ps.get("max_threads", ps.get("max_posts")), cfg.get("max_threads", 3), 1, 25)
    per_thread  = _int_in_bounds(ps.get("max_comments_per_thread"), cfg.get("max_comments_per_thread", 30), 1, REDDIT_LIMIT_CAP)
    # Global kept budget for the call = threads × per-thread, capped.
    limit = _int_in_bounds(ps.get("limit"), max_threads * per_thread, 1, REDDIT_LIMIT_CAP)
    include_replies = bool(ps.get("include_replies", cfg.get("include_replies", False)))

    # Time filter: platform_settings override → ctx-window mapping
    time_filter = ps.get("time_filter") or ctx.get("reddit_time_filter") or _map_time_filter(ctx.get("time_from"), ctx.get("time_to"))

    body: Dict[str, Any] = {
        "subreddits": subreddits,
        "query": query,
        "limit": limit,
        "max_posts": max_threads,
        "max_comments_per_post": per_thread,
        # "all" descends reply chains (replace_more); "top" = top-level only.
        "comments_mode": "all" if include_replies else "top",
    }
    # Search sort: per-spec override from the stream fan-out (ctx["reddit_sort"]),
    # else platform_settings, else first configured sort mode.
    sort = ctx.get("reddit_sort") or ps.get("sort") or (cfg.get("sort_modes") or [None])[0]
    if sort:
        body["sort"] = sort
    # Pagination cursor: keep-going loop passes the prior next_after back via ctx.
    after = ctx.get("reddit_after") or ps.get("after")
    if after:
        body["after"] = after
    if time_filter:
        body["time_filter"] = time_filter

    # Scraper strictness mapping (ultra → high)
    scr_strict = _map_strictness_for_scraper(ctx.get("strictness"))
    if scr_strict:
        body["strictness"] = scr_strict

    return "/reviews/scrape/reddit", body

def _launch_appstore(ctx_or_tags: Union[Dict[str, Any], List[str], str, None]):
    """App Store + Google Play review source. Real star-rated reviews for any
    app-based product, via public endpoints (no key, no fragile scraping)."""
    ctx = _normalize_ctx(ctx_or_tags)
    query = _make_query_from_ctx(ctx)
    ps = (ctx.get("platform_settings") or {}).get("appstore", {}) or {}
    cfg = platform_cfg("appstore")  # max_reviews, countries

    stores = ps.get("stores") or ["appstore", "googleplay"]
    # Multi-country (US/GB/IN/…) → each storefront returns reviews in its language.
    countries = _list_from_maybe_csv(ps.get("countries")) or list(cfg.get("countries") or ["us"])
    lang = ps.get("lang") or "en"
    max_reviews = _int_in_bounds(ps.get("max_reviews"), cfg.get("max_reviews", 80), 5, 200)

    body: Dict[str, Any] = {
        "query": query,
        "stores": stores,
        "country": countries[0],   # back-compat single field
        "countries": countries,
        "lang": lang,
        "max_reviews": max_reviews,
    }
    scr_strict = _map_strictness_for_scraper(ctx.get("strictness"))
    if scr_strict:
        body["strictness"] = scr_strict
    return "/reviews/scrape/appstore", body

# ======================================================================
# Plugin registry
# ======================================================================

PLUGINS: Dict[str, Dict[str, Any]] = {
    "youtube": {
        "router": "/reviews/scrape/youtube",
        "launch": _launch_youtube,
    },
    "reddit": {
        "router": "/reviews/scrape/reddit",
        "launch": _launch_reddit,
    },
    "appstore": {
        "router": "/reviews/scrape/appstore",
        "launch": _launch_appstore,
    },
}
