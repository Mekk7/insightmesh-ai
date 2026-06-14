# backend/api/endpoints/appstore_scraper.py
# App Store (Apple) + Google Play review sourcing.
#
# WHY THIS SOURCE
# ---------------
# YouTube/Reddit give *discussion*, not *reviews*. For any app-based product,
# the App Store and Google Play hold thousands of REAL star-rated reviews — and
# both expose them through endpoints that don't require scraping HTML or paying
# for a provider:
#   - Apple:   iTunes "customer reviews" RSS feed (public JSON, no key).
#   - Google:  the Play Store reviews endpoint via the `google-play-scraper`
#              package when installed; otherwise we degrade gracefully.
#
# This is the honest, reliable Step-3 lane: real reviews, real stars, no CAPTCHA
# fragility, no paid key. It returns the SAME schema as the YouTube scraper
# (`kept_items` with text + metadata) so the pipeline ingests it unchanged.

import re
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.utils.filtering import filter_items_and_metrics, filter_and_metrics

log = logging.getLogger("insightmesh.appstore")
router = APIRouter()

# Apple iTunes reviews RSS. country + app id + page → JSON.
_ITUNES_SEARCH = "https://itunes.apple.com/search"
_ITUNES_REVIEWS = "https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"

_STAR_WORD = {1: "1 star", 2: "2 stars", 3: "3 stars", 4: "4 stars", 5: "5 stars"}


def _clamp(n: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(n)))
    except Exception:
        return lo


# ----------------------------- Apple App Store -----------------------------

def _itunes_find_app(query: str, country: str) -> Optional[Dict[str, Any]]:
    """Resolve a free-text product name to an App Store app (id + name)."""
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(_ITUNES_SEARCH, params={
                "term": query, "country": country, "entity": "software", "limit": 1,
            })
            r.raise_for_status()
            results = (r.json() or {}).get("results") or []
            if not results:
                return None
            app = results[0]
            return {"app_id": app.get("trackId"), "name": app.get("trackName")}
    except Exception as e:
        log.info("[appstore] itunes search failed: %s", e)
        return None


def _itunes_reviews(app_id: int, country: str, pages: int) -> List[Dict[str, Any]]:
    """Pull customer reviews from the iTunes RSS JSON feed across `pages`."""
    items: List[Dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = _ITUNES_REVIEWS.format(country=country, page=page, app_id=app_id)
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                r = client.get(url)
                r.raise_for_status()
                feed = (r.json() or {}).get("feed") or {}
                entries = feed.get("entry") or []
        except Exception as e:
            log.info("[appstore] reviews page %s failed: %s", page, e)
            break

        # The first entry on page 1 is the app metadata, not a review — skip non-review entries.
        for e in entries:
            if not isinstance(e, dict):
                continue
            content = (e.get("content") or {}).get("label")
            rating = (e.get("im:rating") or {}).get("label")
            if not content:
                continue
            try:
                stars = int(rating) if rating else None
            except Exception:
                stars = None
            title = (e.get("title") or {}).get("label") or ""
            author = ((e.get("author") or {}).get("name") or {}).get("label")
            updated = (e.get("updated") or {}).get("label")
            text = f"{title}. {content}".strip(". ").strip() if title else content
            items.append({
                "text": text,
                "published_at": updated,
                "author": author,
                "stars_hint": stars,                 # provenance: real star rating
                "platform": "appstore",
                "post_id": str(e.get("id", {}).get("label") or "")[:120] or None,
            })
    return items


# ----------------------------- Google Play -----------------------------

def _gplay_reviews(query: str, country: str, lang: str, count: int) -> List[Dict[str, Any]]:
    """
    Pull Google Play reviews via the optional `google-play-scraper` package.
    Returns [] (and logs) if the package isn't installed — never raises.
    """
    try:
        from google_play_scraper import search as gp_search, reviews as gp_reviews, Sort
    except Exception:
        log.info("[appstore] google-play-scraper not installed; skipping Play reviews")
        return []

    try:
        hits = gp_search(query, lang=lang, country=country, n_hits=1)
        if not hits or not isinstance(hits, list):
            return []
        first = hits[0]
        app_id = first.get("appId") if isinstance(first, dict) else None
        if not app_id:
            return []
        res = gp_reviews(
            app_id, lang=lang, country=country, sort=Sort.NEWEST, count=count,
        )
        # The lib normally returns (reviews, continuation_token); be defensive about
        # odd locales that have returned None / a bare list (root cause of the prior
        # "'NoneType' object is not subscriptable").
        if isinstance(res, tuple):
            result = res[0] if res else []
        elif isinstance(res, list):
            result = res
        else:
            result = []
    except Exception as e:
        log.info("[appstore] google play fetch failed (%s): %s", country, e)
        return []

    items: List[Dict[str, Any]] = []
    for r in result or []:
        if not isinstance(r, dict):
            continue
        text = (r.get("content") or "").strip()
        if not text:
            continue
        score = r.get("score")
        items.append({
            "text": text,
            "published_at": (r.get("at").isoformat() if hasattr(r.get("at"), "isoformat") else r.get("at")),
            "author": r.get("userName"),
            "like_count": int(r.get("thumbsUpCount") or 0),
            "stars_hint": int(score) if score else None,
            "platform": "googleplay",
            "post_id": r.get("reviewId"),
        })
    return items


# ----------------------------- Request schema -----------------------------

class AppStoreScrapeInput(BaseModel):
    query: str
    stores: Optional[List[str]] = None       # ["appstore", "googleplay"]; default both
    country: str = "us"                       # single-country (back-compat)
    countries: Optional[List[str]] = None     # multi-country → language diversity (e.g. ["us","gb","in","jp","de"])
    lang: str = "en"
    max_reviews: int = 80                     # total target across stores × countries
    strictness: Optional[str] = None          # low|normal|high|ultra


@router.post(
    "/scrape/appstore",
    response_model=Dict[str, Any],
    summary="Fetch real star-rated reviews from the App Store and Google Play",
)
def scrape_appstore(input: AppStoreScrapeInput) -> Dict[str, Any]:
    stores = [s.lower() for s in (input.stores or ["appstore", "googleplay"])]
    # Multi-country: each storefront returns reviews in that country's language, so
    # scraping several countries widens both coverage AND language diversity for free.
    countries = [c.lower()[:2] for c in (input.countries or [input.country or "us"]) if c]
    if not countries:
        countries = ["us"]
    lang = (input.lang or "en").lower()[:5]
    max_reviews = _clamp(input.max_reviews, 5, 200)
    strictness = (input.strictness or "normal").lower()
    if strictness == "ultra":
        strictness = "high"

    # Split the budget across every (store × country) lane.
    n_lanes = max(1, len(stores) * len(countries))
    per_lane = max(5, max_reviews // n_lanes)
    raw_items: List[Dict[str, Any]] = []
    resolved: Dict[str, Any] = {}

    for country in countries:
        # ---- Apple App Store ---- (each lane isolated: one bad locale/app never
        # aborts the whole scrape — this is what caused appstore to silently die.)
        if "appstore" in stores:
            try:
                app = _itunes_find_app(input.query, country)
                app_id = app.get("app_id") if isinstance(app, dict) else None
                if app_id:
                    resolved[f"appstore:{country}"] = app.get("name")
                    pages = _clamp((per_lane // 50) + 1, 1, 4)  # ~50 reviews/page
                    for it in (_itunes_reviews(int(app_id), country, pages) or [])[:per_lane]:
                        it["country"] = country
                        raw_items.append(it)
            except Exception as e:
                log.info("[appstore] apple lane failed (%s): %s", country, e)

        # ---- Google Play ----
        if "googleplay" in stores:
            try:
                gp = _gplay_reviews(input.query, country, lang, per_lane) or []
                if gp:
                    resolved[f"googleplay:{country}"] = f"{len(gp)} reviews"
                    for it in gp:
                        it["country"] = country
                        raw_items.append(it)
            except Exception as e:
                log.info("[appstore] google play lane failed (%s): %s", country, e)

    if not raw_items:
        return {
            "source": "appstore",
            "query": input.query,
            "resolved": resolved,
            "kept_count": 0,
            "kept_comments": [],
            "kept_items": [],
            "note": (
                "No app-store reviews found. This product may not be an app, or the "
                "store/country/language didn't match. Try a different country code, or "
                "use a different source."
            ),
        }

    # De-noise with the shared filter (same as YouTube/Reddit) so the pipeline
    # gets clean, consistent input. We keep per-item metadata + the real stars.
    try:
        kept_items, dropped_by_reason, lang_hist = filter_items_and_metrics(
            raw_items, desired_count=max_reviews, strictness=strictness,
        )
    except Exception:
        texts = [it["text"] for it in raw_items]
        kept_texts, dropped_by_reason, lang_hist = filter_and_metrics(
            texts, desired_count=max_reviews, strictness=strictness,
        )
        kept_set = set(kept_texts)
        kept_items = [it for it in raw_items if it["text"] in kept_set]

    kept_comments = [it["text"] for it in kept_items]

    return {
        "source": "appstore",
        "query": input.query,
        "resolved": resolved,
        "stores": stores,
        "countries": countries,
        "kept_count": len(kept_items),
        "kept_comments": kept_comments,
        "kept_items": kept_items,
        "dropped_by_reason": dropped_by_reason,
        "lang_hist": lang_hist,
        # legacy
        "comments_count": len(kept_items),
        "comments": kept_comments,
    }


@router.get("/scrape/appstore/_ping")
def appstore_ping():
    return {"ok": True}
