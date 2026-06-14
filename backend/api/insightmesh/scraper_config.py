# backend/api/insightmesh/scraper_config.py
"""
Central, tuneable scraper configuration.

ONE place to control how aggressively each platform is scraped and what the
adaptive loop aims for. Every value has an env override (so a deployment can be
tuned without code edits) — see `_ENV_INT` / `_ENV_BOOL` / list overrides below.

Consumers:
  - plugins.py        — turns these into per-platform scrape request bodies
  - stream.py         — query fan-out (query_variants), reddit sort_modes,
                        appstore countries, and the `targets` (useful-review
                        goal, insufficient-data threshold, raw-fetch hard cap)
"""
from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict, List

# The canonical defaults. Tune here, or via the env overrides below.
SCRAPER_CONFIG: Dict[str, Any] = {
    "youtube": {
        "max_videos": 8,               # search results to check (was ~2)
        "max_comments_per_video": 50,  # comments per video (was ~20)
        "fetch_replies": True,         # pull reply chains (was False)
        "query_variants": 3,           # N search queries from the product name
    },
    "reddit": {
        "max_threads": 10,             # threads to pull from (was ~2)
        "max_comments_per_thread": 30, # comments per thread
        "include_replies": True,       # descend reply chains
        "query_variants": 3,           # diverse search queries
        "sort_modes": ["relevance", "top", "new"],  # mix of sort orders
    },
    "appstore": {
        "max_reviews": 50,             # reviews to pull (across stores/countries)
        "countries": ["us", "gb", "in", "jp", "fr"],  # multi-country → language diversity (en/hi/ja/fr)
    },
    "targets": {
        "min_useful_reviews": 30,      # below this → "insufficient data" warning
        "target_useful_reviews": 80,   # Deep target (this default IS the deep preset)
        "max_raw_fetch": 400,          # hard cap on total raw comments fetched
    },
    # Coverage-driven investigation (CoverageMap). All thresholds tuneable.
    "coverage": {
        "enabled": True,
        "max_rounds": 6,                    # hard cap on investigation rounds
        "thin_threshold": 5,                # < this many mentions → "thin"
        "well_covered_threshold": 8,        # >= this → "well covered"
        "saturation_min_mentions": 6,       # need this many before saturation can declare
        "category_match_similarity": 0.45,  # insight↔category sim to join existing category
        "subproblem_similarity": 0.60,      # insight↔sub-problem sim to merge vs. create new
        "coverage_stop_fraction": 0.70,     # stop when this fraction of EXPECTED cats covered
        "max_gap_queries_per_round": 4,     # cap targeted gap searches per round
    },
}

# env var -> (section, key) for integer overrides
_ENV_INT = {
    "SCRAPER_YT_MAX_VIDEOS":        ("youtube", "max_videos"),
    "SCRAPER_YT_MAX_COMMENTS":      ("youtube", "max_comments_per_video"),
    "SCRAPER_YT_QUERY_VARIANTS":    ("youtube", "query_variants"),
    "SCRAPER_REDDIT_MAX_THREADS":   ("reddit", "max_threads"),
    "SCRAPER_REDDIT_MAX_COMMENTS":  ("reddit", "max_comments_per_thread"),
    "SCRAPER_REDDIT_QUERY_VARIANTS":("reddit", "query_variants"),
    "SCRAPER_APPSTORE_MAX_REVIEWS": ("appstore", "max_reviews"),
    "SCRAPER_MIN_USEFUL":           ("targets", "min_useful_reviews"),
    "SCRAPER_TARGET_USEFUL":        ("targets", "target_useful_reviews"),
    "SCRAPER_MAX_RAW_FETCH":        ("targets", "max_raw_fetch"),
    "SCRAPER_COVERAGE_MAX_ROUNDS":  ("coverage", "max_rounds"),
    "SCRAPER_COVERAGE_THIN":        ("coverage", "thin_threshold"),
    "SCRAPER_COVERAGE_WELL":        ("coverage", "well_covered_threshold"),
    "SCRAPER_COVERAGE_SATURATION":  ("coverage", "saturation_min_mentions"),
    "SCRAPER_COVERAGE_MAX_GAP_Q":   ("coverage", "max_gap_queries_per_round"),
}
# env var -> (section, key) for boolean overrides
_ENV_BOOL = {
    "SCRAPER_YT_FETCH_REPLIES":       ("youtube", "fetch_replies"),
    "SCRAPER_REDDIT_INCLUDE_REPLIES": ("reddit", "include_replies"),
    "SCRAPER_COVERAGE_ENABLED":       ("coverage", "enabled"),
}
# env var -> (section, key) for float overrides
_ENV_FLOAT = {
    "SCRAPER_COVERAGE_CAT_SIM":       ("coverage", "category_match_similarity"),
    "SCRAPER_COVERAGE_SUB_SIM":       ("coverage", "subproblem_similarity"),
    "SCRAPER_COVERAGE_STOP_FRAC":     ("coverage", "coverage_stop_fraction"),
}


def _as_bool(s: str) -> bool:
    return str(s).strip().lower() in {"1", "true", "yes", "on"}


def get_scraper_config() -> Dict[str, Any]:
    """Return a fresh copy of the config with env overrides applied."""
    cfg = deepcopy(SCRAPER_CONFIG)
    for env, (sec, key) in _ENV_INT.items():
        v = os.getenv(env)
        if v not in (None, ""):
            try:
                cfg[sec][key] = int(v)
            except (TypeError, ValueError):
                pass
    for env, (sec, key) in _ENV_BOOL.items():
        v = os.getenv(env)
        if v not in (None, ""):
            cfg[sec][key] = _as_bool(v)
    for env, (sec, key) in _ENV_FLOAT.items():
        v = os.getenv(env)
        if v not in (None, ""):
            try:
                cfg[sec][key] = float(v)
            except (TypeError, ValueError):
                pass
    sm = os.getenv("SCRAPER_REDDIT_SORT_MODES")
    if sm:
        cfg["reddit"]["sort_modes"] = [x.strip() for x in sm.split(",") if x.strip()]
    co = os.getenv("SCRAPER_APPSTORE_COUNTRIES")
    if co:
        cfg["appstore"]["countries"] = [x.strip() for x in co.split(",") if x.strip()]
    return cfg


# Analysis depth presets — "Quick" for fast runs, "Balanced" for normal, "Deep" for full investigation.
# Frontend sends `analysis_depth` in the request body; stream.py calls `apply_depth_preset(cfg, depth)`.
DEPTH_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {
        "youtube": {"max_videos": 3, "max_comments_per_video": 20, "fetch_replies": False, "query_variants": 1},
        "reddit":  {"max_threads": 3, "max_comments_per_thread": 15, "include_replies": False, "query_variants": 1, "sort_modes": ["relevance"]},
        "appstore": {"max_reviews": 20, "countries": ["us"]},
        "targets": {"min_useful_reviews": 10, "target_useful_reviews": 15, "max_raw_fetch": 60},
        "coverage": {"enabled": False},
    },
    "balanced": {
        "youtube": {"max_videos": 6, "max_comments_per_video": 40, "fetch_replies": True, "query_variants": 3},
        "reddit":  {"max_threads": 8, "max_comments_per_thread": 25, "include_replies": True, "query_variants": 3, "sort_modes": ["relevance", "top"]},
        "appstore": {"max_reviews": 35, "countries": ["us", "gb"]},
        # target 50 + a 400-raw budget so heavy upstream filtering still leaves
        # 40-50 analyzable reviews (the top-up scrape in stream.py backfills a thin pool).
        "targets": {"min_useful_reviews": 25, "target_useful_reviews": 50, "max_raw_fetch": 400},
        "coverage": {"enabled": True, "max_rounds": 3},
    },
    "deep": {
        # Uses the full SCRAPER_CONFIG defaults (max_videos=8, target=80, max_raw_fetch=400).
        # No overrides needed — this IS the default config.
    },
}


def apply_depth_preset(cfg: Dict[str, Any], depth: str) -> Dict[str, Any]:
    """Apply a depth preset over the given config. Returns the modified config."""
    preset = DEPTH_PRESETS.get(depth)
    if not preset:
        return cfg  # unknown depth → use defaults (= deep)
    for section, overrides in preset.items():
        if section in cfg and isinstance(overrides, dict):
            cfg[section].update(overrides)
    return cfg


def platform_cfg(platform: str) -> Dict[str, Any]:
    """Per-platform config block (with env overrides applied)."""
    return get_scraper_config().get(platform, {}) or {}


def targets() -> Dict[str, Any]:
    """The `targets` block: min_useful_reviews, target_useful_reviews, max_raw_fetch."""
    return get_scraper_config().get("targets", {}) or {}
