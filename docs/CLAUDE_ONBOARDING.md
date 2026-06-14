# InsightMesh AI — Claude Onboarding Guide
**Updated: 2026-06-10** (after multi-day architecture + intelligence session)

## 1. What is InsightMesh AI?
Product-feedback intelligence platform. Scrapes reviews from YouTube/Reddit/App Store, runs AI analysis with deep classification, and presents insights through a React dashboard. North star: **"honest by default" / "i hate fake"** — never fake confidence, never show self-contradictory data, say less rather than fabricate.

**The vision:** Not a sentiment counter — a system that UNDERSTANDS products. It knows a game bug isn't a console flaw. It debates using real evidence. It tells you what it couldn't determine. Every insight traces to a real review.

## 2. Tech Stack
- **Backend:** FastAPI (Python), Ollama (local LLM), sentence-transformers
- **Frontend:** Vite + React 19 + Tailwind, Recharts
- **LLM:** Ollama primary (qwen2.5:7b, local, free), OpenAI gpt-4o-mini fallback (paid, optional)
- **DB:** SQLite (run history + LLM cache)
- **Root:** `D:\IM_AI_folder`

## 3. Critical Rules
- **§3.1** NEVER full-read `analyze_reviews.py` (~77KB, 2000+ lines). Use targeted grep/sed.
- **§3.2** After ANY `.py` edit, the backend must be restarted (Python loads modules once).
- **§3.3** Before claiming something is "fixed," verify on a LIVE run, not just by reading code.
- **§3.4** The onboarding doc must reflect reality. Update before wrapping any session.
- **§3.5** Delete `backend/data/insightmesh.db` and `backend/data/llm_cache.db` before testing changes — cached results mask fixes.

## 4. LLM Configuration
- **Primary:** Ollama at localhost:11434, model `qwen2.5:7b`
- **Fallback:** OpenAI gpt-4o-mini (key in `.env`, optional)
- **Status:** Backend logs `[startup] LLM brain: OLLAMA (qwen2.5:7b) ✔` on boot
- **Endpoint:** `GET /api/llm/status` returns current backend status
- **Retry fix:** OpenAI max_retries=1, timeout=15s (no retry storms)

## 5. API Keys (.env)
- `YOUTUBE_API_KEY` — free (Google Cloud Console, 10k quota/day)
- `REDDIT_CLIENT_ID/SECRET/PASSWORD` — free (reddit.com/prefs/apps) — WORKING as of 2026-06-10
- `OPENAI_API_KEY` — paid, optional (prepaid credit)
- `.env` is git-ignored. Keys were rotated on 2026-06-07.
- **WARNING:** Keys have been exposed in chat multiple times. Rotate after every session where they appear.

## 6. What Was Built (2026-06-07 to 2026-06-10 sessions)

### 6.1 Star/Category Reconciliation (DONE)
- **File:** `backend/api/endpoints/analyze_reviews.py` (d.2 block)
- Total reconciliation — no 5-star complaints possible.

### 6.2 Cache Version Gate (DONE)
- **File:** `backend/api/insightmesh/run_pipeline.py`
- `REPORT_SCHEMA_VERSION = 2`. Old cached reports auto-recompute.

### 6.3 EvidenceStore — RAG Retrieval for Debate (DONE)
- **File:** `backend/insight/debate/evidence_store.py`
- Numpy cosine index, stance-filtered retrieval, sarcasm penalty.

### 6.4 Multi-Round Debate Engine (DONE)
- **File:** `backend/insight/debate/engine.py`
- Advocate opens → Skeptic rebuts → Advocate answers → Judge with `could_not_determine`.

### 6.5 Upgraded DebatePanel (DONE)
- **File:** `insightmesh-fe/src/components/DebatePanel.jsx`
- Round-by-round transcript, "couldn't determine" block, `featured` mode.

### 6.6 Debate as Dashboard Centerpiece (DONE)
- Customer view: featured debate right after TrustScore.
- Company view: debate stays lower, machinery leads.

### 6.7 Situation Report Engine (DONE, NOT WIRED)
- **File:** `backend/insight/topic_understanding/situation.py` (may need directory creation)
- Per-topic "what's really happening" reports. Tested but not in pipeline.

### 6.8 Product Intelligence Generator (DONE, WIRED)
- **File:** `backend/insight/product_intelligence/generator.py`
- One LLM call at pipeline start: understands product category, direct vs ecosystem aspects, competitors, price tier, expectation anchors.
- Cached per product name. Passed to deep classifier as context.

### 6.9 Deep Classifier — 9+ Signal Smart Classification (DONE, WIRED)
- **File:** `backend/insight/deep_classify/classifier.py`
- Signals: relevance_tier, multi-intent, conditions, experience_stage, switching, claims vs opinions, expectation_gap, version_mentioned, causal_chain, product_insight.
- Batched LLM calls (8 per batch). Uses Product Intelligence context.
- `product_insight` field extracts "what does this comment tell us about the PRODUCT" — the core intelligence signal.

### 6.10 Relevance Filter (DONE, WIRED by Claude Code)
- **File:** `backend/api/insightmesh/stream.py`
- Deep classification runs BEFORE analyze_core. Off-topic/tangential comments dropped before analysis.
- Ecosystem comments kept (they carry product intel). >60% dropped → warning in overview.
- Secondary filter: `product_insight.reveals_product_info == false` also drops.

### 6.11 YouTube "review" Query Suffix (DONE)
- **File:** `backend/api/insightmesh/plugins.py`
- YouTube searches now append "review" to find review videos instead of random content.

### 6.12 Reddit Global Search (DONE by Claude Code)
- Reddit now searches across ALL subreddits, not just pre-mapped ones.
- Subreddit map kept as boost. Confirmed working for Tesla, Sony, PS5.

### 6.13 Adaptive Scraping Loop (DONE by Claude Code)
- If fewer than 15 useful reviews after deep classification, expands search with additional queries.
- Maximum 3 rounds. Logs rounds and filter counts.

### 6.14 Dashboard Evidence Gates (DONE by Claude Code)
- Sections with insufficient evidence hide or show "not enough data."

### 6.15 LLM-Generated Remediation Suggestions (DONE by Claude Code)
- Replaced hardcoded "capture crash dumps" templates with product-aware LLM suggestions.
- Tesla gets "Expand Supercharger network," headphones get "breathable ear cushion materials."

### 6.16 ABSA Domain Detection (PARTIALLY FIXED by Claude Code)
- Sony headphones correctly detected as "audio" domain with audio-specific aspects.
- Tesla still shows as "auto_ev" but only detects battery/charging aspects. Needs more aspects (range, interior, autopilot, build quality).

### 6.17 Frontend Fixes (DONE)
- Default query: empty (not "Tesla Model Y").
- query_used safety: frontend forces correct product name after any run.
- Ask Q&A: upgraded with deep signals context + better suggestions.

### 6.18 Scraper Configuration System (DONE, 2026-06-10, integration-tested)
- **File:** `backend/api/insightmesh/scraper_config.py` (new) — single source of truth `SCRAPER_CONFIG`
  with per-platform knobs + a `targets` block; every value has an env override (`SCRAPER_*`); accessors
  `get_scraper_config()`, `platform_cfg(p)`, `targets()`.
- **Knobs:** youtube `max_videos`(8) / `max_comments_per_video`(50) / `fetch_replies`(True) /
  `query_variants`(3); reddit `max_threads`(10) / `max_comments_per_thread`(30) / `include_replies`(True) /
  `query_variants`(3) / `sort_modes`([relevance,top,new]); appstore `max_reviews`(50) /
  `countries`([us,gb,in]); targets `min_useful_reviews`(30) / `target_useful_reviews`(100) /
  `max_raw_fetch`(500).
- **Wired into the plugins** (`plugins.py`): each launcher reads `platform_cfg(...)` for its defaults
  (platform_settings still override). YouTube body now carries `fetch_replies`; Reddit body carries
  `max_posts`, `max_comments_per_post`, `comments_mode` (all↔include_replies), and `sort`; App Store body
  carries `countries` + `max_reviews`.
- **Scraper support added:**
  - YouTube (`scrape_reviews.py`): `fetch_replies` (inline reply chains via `part="snippet,replies"`),
    video hard-cap raised to 10 so `max_videos=8` passes.
  - Reddit (`reddit_scraper.py`): `sort` (relevance|top|new|hot|comments) + `max_comments_per_post`
    (per-thread cap so one hot thread can't dominate).
  - App Store (`appstore_scraper.py`): `countries` list — loops storefronts, splits the budget across
    every (store × country) lane, tags each item with its country.
- **Wired into stream + adaptive loop** (`stream.py` §3 + enrichment): `query_variants` drives the
  diversified-query fan-out; reddit `sort_modes` are cycled across those queries; App Store is one spec
  (multi-country inside the scraper); `max_raw_fetch` is a hard global cap tracked across the initial
  scrape AND the deferred adaptive rounds; the adaptive loop now aims for `target_useful_reviews` and the
  overview shows an **"insufficient data"** warning when useful reviews `< min_useful_reviews`.
- **Telemetry (logged + streamed):** `log.info("[stream] adaptive result: raw_fetched=… | passed_relevance(useful)=…/target | target_reached=… …")`,
  plus the `deep_classify_done` SSE event and `overview.adaptive_scrape` carry `raw_fetched`,
  `passed_relevance`, `target`, `target_met`, `insufficient`.
- **Verified:** integration test on the real `_progress_pipeline` (stubbed net/LLM): 3 diversified
  queries; reddit hits all 3 sorts; appstore scraped once by name; targets 100/30 applied;
  insufficient-data warning fires at 8 useful; `max_raw_fetch` cap stops at exactly 25; deferral
  (complete→deep_classify) still holds. NOT yet timed on a fully live run.

### 6.19 Coverage-Driven Self-Expanding Investigation (DONE, 2026-06-10, integration-tested)
- **Files:** `backend/insight/coverage/coverage_map.py` (new engine), `product_intelligence/generator.py`
  (expected feedback map), `scraper_config.py` (`coverage` block), `stream.py` (deferred enrich phase).
- **The idea:** the model investigates a product like a researcher — starts from an EXPECTED feedback
  map, hunts across sources to cover it, goes DEEP into sub-problems, and stays open to NEW issues. The
  investigation map grows as it learns; no fixed feature list.
- **1. Expectation:** `ProductIntelligence.expected_feedback_categories` (new field + LLM prompt +
  fallback) — the categories a product like this COULD receive (PS5 → controller, electrical/power,
  build, heating/cooling, storage, disc drive, software/UI, connectivity, fan noise). A STARTING
  expectation, not a checklist.
- **2. Deep categories:** `CoverageMap` holds each category's discovered SUB-PROBLEMS. An insight either
  merges into the nearest sub-problem (embedding cosine ≥ `subproblem_similarity`) or starts a new one,
  so "battery" naturally splits into discharge / heating / degradation / ….
- **3. Open discovery:** an insight matching NO category (similarity < `category_match_similarity`)
  CREATES a new category — surprises are tracked, never discarded.
- **4. Saturation:** a category is "understood" once a round adds mentions but NO new sub-problem
  (`rounds_no_new ≥ 1`) at/above `saturation_min_mentions`. States: gap (0) / thin / developing /
  saturated / well_covered.
- **5. Coverage loop (in the DEFERRED enrich phase of `stream.py`):** round 1 seeds the map from the
  dashboard's comments; then each round calls `cmap.expansion_targets()` (gaps first, then thin — capped
  by `max_gap_queries_per_round`) and scrapes targeted `"<product> <category> problems"` across the active
  YouTube/Reddit sources, dedupes (run-wide `seen`), classifies, and ingests. Saturated/well-covered
  categories are NOT re-pulled. Stops when `is_done()` (coverage_fraction ≥ `coverage_stop_fraction` AND
  no new categories AND no gaps), OR `max_rounds`, OR `max_raw_fetch`, OR sources exhausted.
- **6. Honest coverage report → `overview.coverage_report`:** `{summary, assessment, rounds, categories,
  discovered_categories, gaps}`. Summary reads e.g. "Investigated 8 feedback categories. Well-covered:
  battery (incl. heating + discharge), … Thin: connectivity (3 mentions). Discovered unexpected: disc
  drive grinding (5 owners). Couldn't find enough on: fan noise."
- **Embedding:** uses the analyzer's `all-MiniLM-L6-v2` for semantic category/sub-problem matching;
  degrades to token-overlap if no embedder (keeps it unit-testable).
- **Config (`SCRAPER_CONFIG["coverage"]`, all env-overridable `SCRAPER_COVERAGE_*`):** `enabled`,
  `max_rounds`(6), `thin_threshold`(5), `well_covered_threshold`(8), `saturation_min_mentions`(6),
  `category_match_similarity`(0.45), `subproblem_similarity`(0.60), `coverage_stop_fraction`(0.70),
  `max_gap_queries_per_round`(4).
- **Logging:** `cmap.log_state()` prints the per-round map (`cat:Nm/Ksp[state]`), and the result line
  logs rounds / raw_fetched / useful / expected_covered / discovered / gaps; per-round `coverage_round`
  SSE events stream the assessment so the investigation can be watched unfold.
- **Verified:** unit test (controlled embedder) — battery splits into 3 sub-problems, controller goes
  saturated, "smell" discovered, fan-noise/storage are gaps. Integration test through the real
  `_progress_pipeline` + real embedder — round 1 covered battery/controller + discovered "packaging",
  round 2 targeted ONLY the gaps (`PS5 fan noise problems` …) without re-pulling battery, filled them to
  5/5 coverage, emitted the honest summary. Deferral (complete→deep_classify) preserved. Runs on Ollama
  now (slow, background); will be fast on gpt-4o-mini.
- **LIVE-verified (2026-06-10, real qwen2.5:7b):** "PS5" produced a product-specific expected map
  (`controller, electrical/power, build/physical structure, heating/cooling, storage, disc drive,
  software/UI, connectivity, fan noise, game library, online service quality`); real `deep_classify`
  mapped sample comments to the correct categories (stick drift→controller, jet-engine fan→heating/cooling,
  grinding→disc drive) and the off-topic "GTA 6" comment was tagged ecosystem with no product insight; the
  honest report + gap targets rendered. **Gotcha learned:** any standalone script must `load_dotenv()`
  BEFORE importing `backend.utils.llm` — otherwise `OLLAMA_MODEL` defaults to the unpulled `llama3.2:3b`
  and Ollama's `/api/chat` returns 404 (silently falls back to fallback intel). The real uvicorn startup
  loads `.env` first, so this only bites ad-hoc scripts.
- **App Store countries** widened to `[us, gb, in, jp, fr]` (en/hi/ja/fr) per the Priority 0 spec.

### 6.20 Funnel Fix — 500→14 → 100s reach the dashboard (PART A, 2026-06-10)
- **A.1 — pre-classify cap.** `ANALYZE_BATCH_MAX` (run_pipeline.py) capped the analyzed/classified batch
  to **40**. Raised default to **150** (env `ANALYZE_BATCH_MAX`) so 100+ reach classification.
- **A.2 — over-aggressive relevance.** analyze_core's prefilter (`filters/relevance.py`) gated comments on
  similarity to the short query string — kept ~1/20 at "normal", ~6/20 at "low" (real feedback like "the
  display is sharp" doesn't echo "Apple Vision Pro"). Fix: (1) new **TIER GATE in `stream.py` §5b.2** runs
  `deep_classify` BEFORE analyze and keeps `direct`+`ecosystem`, dropping only `off_topic`/`tangential`
  (fail-open); (2) `analyze_core` accepts `strictness="off"` to SKIP its prefilter; `stream.ANALYZE_STRICTNESS`
  defaults to `"off"`. Verified: 40 of 60 (20 off_topic dropped) reach the dashboard, was ~14. This
  un-defers deep_classify to BEFORE the dashboard (slower on Ollama; "slow OK for testing"; the deferred
  coverage loop now REUSES `deep_by_text` so nothing is classified twice).
- **A.3 — App Store / Google Play crash.** `_gplay_reviews` could throw "'NoneType' object is not
  subscriptable" for some locales and one bad (store×country) lane aborted the whole appstore scrape.
  Fixed with defensive shape handling + per-lane try/except. Verified live (Apple Vision Pro, us/gb/jp/fr):
  no crash, 20 reviews, graceful per-lane degradation.
- **A.4 — `/reviews/scrape/*` 404.** Could NOT reproduce: routes mount at `/api`, rate-limiter expects
  `/api`, `_candidate_urls` tries `/api` first. The bare-path 404 only appears if `/api` fails first (e.g.
  a 429 rate-limit) → root fallback. Need the exact log line; likely a rate-limit artifact, not a prefix bug.
- **Funnel logging:** `[stream] funnel: raw=… deduped=… classified=… off_topic_dropped=… useful(direct+eco)=…
  kept_for_dashboard=…` + `classify_gate_start`/`classify_gate_done` SSE events.

### 6.21 Pagination + keep-going loop (PART B) — SCRAPER LAYER DONE; stream loop pending
- **DONE + LIVE-VERIFIED — scraper pagination cursors:**
  - YouTube (`scrape_reviews.py`): accepts `page_token`, returns `next_page_token` (search-page cursor);
    items now carry `comment_id` (top-level + reply IDs).
  - Reddit (`reddit_scraper.py`): accepts `after`, returns `next_after` (listing cursor via PRAW
    `params={"after":…}`, only passed when set — `params=None` crashes PRAW); items carry `comment_id`.
    **Live test:** page1 (15 comments, next_after=t3_…) vs page2 (9 comments) → **0 overlap** — fresh
    comments, never re-fetched.
  - `comment_id` added to `_META_FIELDS` so it survives the pipeline; App Store dedups via its `post_id`.
- **DONE — comment-ID dedup (B.4):** `stream.py` §4 dedups by `comment_id`/`post_id` (set `seen_ids`)
  across rounds, then normalized text as backstop.
- **DONE — plugin cursor pass-through:** `_launch_youtube` reads `ctx["yt_page_token"]`; `_launch_reddit`
  reads `ctx["reddit_after"]`.
- **DONE — the stream keep-going ORCHESTRATION loop (B.3/B.5):** §3 now captures each spec's
  `next_page_token`/`next_after` into `source_cursors`. §5b is a **paginating keep-going gather**: it
  classifies the interleaved initial batch, then (1) classifies the rest of what was ALREADY fetched
  (free, no API), then (2) PAGINATES FORWARD via `_paginate(spec_key)` (re-scrape with the saved cursor →
  dedup by `seen_ids` → clean → classify), looping until `target_useful_reviews` (100) / `max_raw_fetch` /
  an `analyze_ceiling` (=max(ANALYZE_BATCH_MAX, target*2)) / sources exhausted. The accumulated
  direct+ecosystem set feeds analyze_core directly, so the dashboard reflects the WHOLE investigation
  (not a 40→14 sliver). `_classify_keep` reuses `deep_by_text` so no comment is classified twice.
- **Funnel logging:** `[stream] funnel: raw_fetched=… deduped=… classified=… off_topic_dropped=…
  useful(direct+eco)=… kept_for_dashboard=… pagination_rounds=… stop=…` + per-round `paginate_round` SSE
  events (round / source=initial|already_fetched|page:<platform> / useful_total / raw_fetched).
- **Verified:** (a) Reddit pagination LIVE — page1 vs page2 = 0 overlap (fresh comments). (b) Keep-going
  loop integration test through the real `_progress_pipeline` (stubbed scrape/LLM): classified leftovers
  first (raw flat), then paginated forward (raw climbed), useful rose monotonically 4→9→15→20→25 to the
  target, **50 comments classified all unique (zero re-classify)**, stop=`target_met`, analyze got the 25
  kept. NOT yet timed on a full live Ollama run (slow); env to tune: `SCRAPER_TARGET_USEFUL`,
  `SCRAPER_MAX_RAW_FETCH`, `ANALYZE_BATCH_MAX`, `DEEP_CLASSIFY_BATCH_SIZE`.

### 6.22 Analysis Depth Modes (DONE, 2026-06-10, Claude chat session)
- **Files:** `scraper_config.py` (DEPTH_PRESETS + apply_depth_preset), `run_pipeline.py` (analysis_depth
  field on RunInput), `stream.py` (reads depth, applies preset before scraping), `Insights.jsx` (toggle UI).
- **Three modes:** ⚡ Quick (~25 target, 3 videos, no replies, no coverage, max 150 raw, ~2-3 min),
  ⚖️ Balanced (~50 target, 5 videos, replies on, 3 coverage rounds, max 300 raw, ~5-7 min),
  🔬 Deep (100+ target, 8 videos, full coverage 6 rounds, max 500 raw, ~15 min).
- **Default: Balanced.** Deep is the old full config. Quick skips coverage loop entirely.
- **UI:** Three toggle buttons visible next to "Live progress" checkbox (not hidden in Advanced).
  Persisted in localStorage. Sent as `analysis_depth` in every request body.

### 6.23 Product Intelligence Dashboard Badge (DONE, 2026-06-10, Claude chat session)
- **File:** `Insights.jsx` — inserted after stats cards, before TrustScore.
- Shows: "AI understands" label + category pill (e.g. "augmented reality headset") + segment + price tier
  + key competitors ("vs Meta Quest 3, HoloLens") + tracking aspects ("display · comfort · weight · ...").
- Only renders when `overview.product_intelligence.category` exists. Compact single-row design.

### 6.24 Smart Signals Dashboard Section (DONE, 2026-06-10, Claude chat session)
- **File:** `Insights.jsx` — inserted before "Voice of the customer" section.
- Displays the deep classification aggregate (`overview.deep_signals`) that was always computed but never shown.
- **Six signal cards** (each only renders when it has data):
  1. ✓ Verified Claims — cross-checked facts confirmed by 2+ reviewers (emerald card)
  2. ↔ Competitive Switching — switching FROM/TO which products (blue card)
  3. 👤 Reviewer Experience — first impression / short term / long term / expert counts (amber card)
  4. 📊 Expectation vs Reality — exceeded / met / fell short counts (rose card)
  5. 🔗 Root Cause Chains — cause → effect → consequence linked steps (purple card)
  6. 📱 Versions Mentioned — which product variants reviewers discuss (zinc card)
- All cards gated on having actual data — empty signals don't render.

### 6.25 Intelligence Synthesizer (DONE, 2026-06-12, LIVE-VERIFIED)
- **New file:** `backend/insight/intelligence/synthesizer.py`. Pure post-processing (regex +
  heuristics, no LLM/network/disk), fail-open, runs for ALL depth modes. Three outputs:
  1. **Review Intelligence Scores** — `score_review_intelligence` scores every review on
     specificity / depth / actionability / uniqueness (0-1 each, averaged → composite + HIGH/
     MEDIUM/LOW label). Attached as `_intelligence` on each `per_review` item.
  2. **Cross-Section Insights** — `find_cross_insights` connects sections: cluster↔temporal
     correlation, confidence↔quality mismatch, dominant theme (>40%), polarization (≥2 polarized
     clusters), high-quality minority signal. Attached as `overview["cross_insights"]`
     (`{type, description, sections, severity}`).
  3. **Adaptive Summary Brief** — `build_summary_brief` composes data-shape directives
     (polarized / dominant-theme / low-confidence / temporal / trend / cross-insights / quality
     warning) → `overview["_summary_brief"]`, prepended to the executive-summary LLM prompt so the
     narrative ADAPTS to the data. `synthesize()` is the entry point that runs all three.
- **Wiring (see memory `intelligence-synthesizer-wiring`):** all inside `_enrich_overview`
  (`analyze_reviews.py`), in dependency order right before return — `enrich_with_evidence` →
  `synthesize` → `build_smart_summary(..., summary_brief=...)`. The brief MUST precede the summary
  LLM call (it depends on `_analysis_confidence` + cross_insights + temporal_anomalies). The old
  `enrich_with_evidence` call in `analyze_core` (after `_enrich_overview`) was REMOVED as redundant
  (its `kept_meta` arg was unused). `_summary_brief` is popped before the API response.
  `narrator.build_smart_summary` / `_llm_narrate` gained a `summary_brief` param (prompt prefix).
- **Frontend (`Insights.jsx` + `ReviewsBrowser.jsx`):** `CrossInsight` component (severity-styled
  callouts: ⚠ warning / 💡 insight / ℹ info) rendered as a stack right after the executive summary;
  review cards (Voice of the Customer + ReviewsBrowser) show a `q0.xx` intelligence chip, a green
  left-border + "detailed" tag for HIGH and muted opacity for LOW; "Most useful" sort and the
  Voice-of-Customer representative pick now key off `_intelligence.composite` (fallback `quality`).
- **LIVE-VERIFIED (2026-06-12, Apple Vision Pro Quick, real qwen2.5:7b):** 27/27 reviews scored;
  3 cross-insights fired (confidence_quality_mismatch, dominant_theme 59%, polarization); the LLM
  summary led with the brief's polarization directive verbatim ("This product divides opinion
  sharply.") and referenced the dominant theme + temporal drop — proving the brief shapes the
  narrative. No new lint errors; `_summary_brief` correctly stripped from the response.

### 6.26 Advanced Intelligence Pack (DONE, 2026-06-14, verified)
- **Three features**, all fail-open, wired in `analyze_core` (`analyze_reviews.py`) right
  AFTER `_enrich_overview` returns (i.e. after the self-corrector), using the
  `product_intelligence` param + `query` + `results`. New imports sit next to `self_correct`.
  1. **Competitive Intelligence** — `backend/insight/intelligence/competitive_intel.py`.
     `extract_competitive_intel(per_review, product_name, known_competitors, product_aspects)`.
     Pre-filters reviews that mention a known competitor OR carry comparison cue words, sends
     them in ONE gpt-4o-mini call → flat extraction list `{competitor, dimension, winner, quote}`,
     then aggregates (pure Python) into a matrix (competitor × dimension verdicts), an overall
     `competitive_position` (dominant/competitive/challenged/trailing), and a composed
     `key_finding`. Stored at `overview["competitive_intelligence"]` when `total_comparisons > 0`.
  2. **Deal-Breaker & Switching Detector** — `dealbreaker_detector.py`. PURE REGEX (no LLM):
     RETURN/SWITCH/WARN/REGRET patterns. Pulls the leaving REASON from each review's
     `canonical_reason`/cluster, and `lost_to` competitors from switch matches. Stored at
     `overview["dealbreakers"]` when `total_dealbreakers > 0`.
  3. **Purchase Decision Engine** — `purchase_advisor.py`.
     `generate_purchase_advice(overview, per_review, product_name, product_intel_dict)`. ONE
     gpt-4o-mini call consuming aspects + clusters + competitive position + deal-breakers +
     confidence → `{verdict BUY/WAIT/SKIP, verdict_confidence, one_line, personas[], alternatives[],
     wait_for[]}`. Stored at `overview["purchase_advice"]`; ONLY the Customer view reads it.
  - **Field mapping gotcha:** the CLAUDE.md wiring snippet used `product_intelligence.competitors`
    / `.tracking_aspects`, but `ProductIntelligence.to_dict()` actually exposes `key_competitors`
    and `direct_aspects`/`ecosystem_aspects`/`expected_feedback_categories` — the wiring uses the
    real names.
- **Frontend (`Insights.jsx`):** consts `competitiveIntel`/`dealbreakers`/`purchaseAdvice` near
  the other `overview.*` reads. Competitive-matrix table + position badge renders in BOTH views
  (🟢 we win / 🔴 they win / 🟡 tie, hover cell = quote). Deal-breaker red card (top reasons,
  "Lost to", quotes) renders in COMPANY view. Customer view leads with the BUY/WAIT/SKIP verdict
  card + persona grid + "If you're waiting" + "Consider instead", placed just above the featured
  DebatePanel.
- **Cost:** 2 LLM calls (~5-7s on gpt-4o-mini); deal-breaker is free.
- **Verified (2026-06-14):** py_compile clean; eslint parsed Insights.jsx with no NEW errors;
  deal-breaker unit test (4/5 flagged, lost_to=Nintendo Switch); competitive + advisor produced
  valid structured output on live gpt-4o-mini; **full `analyze_core` integration** (Steam-Deck
  mock set) populated all three keys: competitive (6 comparisons, position=challenged),
  dealbreakers (4, lost_to Switch×2), purchase_advice (WAIT, personas Gamer/Professional/Casual).
  NOT yet exercised through the browser dashboard — run Apple Vision Pro Quick + Steam Deck Quick.

### 6.27 Reviewer Credibility Intelligence (DONE, verified)
- **New file:** `backend/insight/intelligence/credibility_scorer.py`. Pure computation (no
  LLM), fail-open, runs for ALL depth modes. `score_credibility(review, all)` → 5 factors
  (ownership 0-.25 / specificity 0-.25 / depth 0-.20 / calibration 0-.15 / platform 0-.15) →
  composite 0-.95 + HIGH(≥.60)/MEDIUM(≥.35)/LOW label, attached as `_credibility` on each
  per_review item. `compute_weighted_metrics(per_review)` → `credibility_intelligence`
  (raw_sentiment / weighted_sentiment / credible_avg / casual_avg / sentiment_gap / counts /
  distribution / insight).
- **Wiring (`analyze_reviews.py`):** top-level fail-open import next to the Advanced
  Intelligence Pack; scoring loop + `compute_weighted_metrics` run right AFTER the per-review
  loop (and the sarcasm/buyer-intent advanced layers), BEFORE clustering; result spread into
  the `_enrich_overview({...})` dict as `credibility_intelligence` (only when non-empty).
- **Data fixes vs the literal CLAUDE.md spec (kept):** (1) per_review `sentiment_score` is
  0..1, NOT stars — the metrics convert to the 1..5 star scale (1+4·score, label-first) so the
  card matches `overview.average_sentiment`. (2) `emotion` is stored as a LABEL string with the
  confidence in `emotion_score` — the scorer reads `emotion_score`. (3) deep-classifier nested
  signals (`deep`/`understanding`) are absent at this stage (and always in Quick), so calibration
  has a TEXT-based praise/complaint fallback; ownership/specificity already fall back to regex.
- **`synthesizer.find_cross_insights`:** emits `credibility_gap_positive` (insight) /
  `credibility_gap_negative` (warning) when `|sentiment_gap| ≥ 0.8`.
- **Frontend:** `Insights.jsx` — `credibility` const + "Reviewer credibility" Card (raw vs
  credibility-weighted vs credible-only, HIGH/MEDIUM/LOW distribution bar, gap insight box,
  null-safe); VoC ranking gains a secondary sort by credibility; VoC + `ReviewsBrowser.jsx`
  review cards show a 🛡 "verified depth" badge for HIGH credibility and dim LOW.
- **Verified:** unit test (HIGH 0.77 owner-review / LOW 0.14 "trash lol"; star-scale metrics;
  gap+insight); gap cross-insight fires at ±0.8 (not at 0.4); eslint no new errors. LIVE Apple
  Vision Pro Quick: all 15 reviews scored, `credibility_intelligence` present (raw 2.73 vs
  weighted 2.66, dist {0 high / 2 med / 13 low}). **Gotcha (by design):** Quick pulls ~15 SHORT
  YouTube comments (0.05 platform baseline) → often 0 reviewers clear the credible (≥0.5) bar, so
  `credible_avg`/gap are null and the gap insight stays hidden (honest-by-default). Balanced/Deep
  (App Store 0.12 + Reddit 0.11 baselines, longer reviews) surface credible reviewers + the gap.

## 7. Known Issues / Open Work

### 7.0 THE FUNNEL BUG + PAGINATION — ✅ RESOLVED (2026-06-10)
Original problem: coverage scraper pulled 500 raw but only ~14 reached the dashboard
(500 raw → 40 reach deep_classify → 14 survive relevance → 14 shown).

**FULLY FIXED by Claude Code (2026-06-10):**
- ✅ PART A.1: batch size 3 → configurable 8 (`DEEP_CLASSIFY_BATCH_SIZE`).
- ✅ The 40 cap: `ANALYZE_BATCH_MAX` (run_pipeline.py) is **150** (was 40). And §5b no longer caps at the
  initial batch — it then classifies ALL already-fetched leftovers + paginated pages (100+ reach classify).
- ✅ PART A.2: relevance no longer guts the set — tier gate keeps direct+ecosystem; `analyze_core` runs
  `strictness="off"` (its query-similarity prefilter, which cut 40→14, is bypassed).
- ✅ PART C.6 Google Play hardened; ✅ PART C.7 /api fallback fixed.
- ✅ Scraper-level pagination: YouTube `page_token`/`next_page_token`, Reddit `after`/`next_after`,
  `comment_id` on items (propagated via `_META_FIELDS`).
- ✅ Comment-ID dedup via `seen_ids` (across all rounds) + text-normalization backstop.
- ✅ **PART B keep-going loop (§5b `_paginate`):** captures cursors in §3, classifies leftovers free,
  then PAGINATES FORWARD (re-scrape with saved cursor) until `target_useful_reviews` (100) /
  `max_raw_fetch` / analyze_ceiling / exhaustion. Verified: useful climbs to target, zero re-classify.
- ✅ **`_scrape_query` (§5e coverage gap-loop) NOW passes cursors too** (`query_cursors` per (platform,
  query)) + dedups by comment ID — so repeated gap re-searches advance pages instead of re-fetching page 1.

**Live-verify next run:** restart backend (§3.2), watch the `[stream] funnel:` log + `paginate_round`
SSE events climb toward 100 useful. `max_raw_fetch` defaults to 500 — bump `SCRAPER_MAX_RAW_FETCH` if 100
useful needs more raw. (Below is the now-COMPLETED original prompt, kept for history.)

**Claude Code prompt to FINISH (give this FIRST in the next session):**
```
Read docs/CLAUDE_ONBOARDING.md §7.0. The scrapers have pagination cursors built
(page_token for YouTube, after for Reddit) but _scrape_query in stream.py doesn't
save or pass them. Wire it:

1. After each scrape call in _scrape_query, save the returned next_page_token (YouTube)
   and next_after (Reddit) per source into a dict (e.g. page_cursors[platform]).
2. On the next round/call for the same source, pass the saved cursor so it fetches
   the NEXT page (comments 501-1000), not page 1 again.
3. When a source returns no next cursor (no more pages), mark it exhausted and skip it.
4. The keep-going loop: after each page, classify + count useful. If below target (100),
   fetch next page using saved cursor. Stop when 100+ useful, OR all sources exhausted,
   OR max_raw_fetch cap.
5. Also: the cap that reduces 500 raw → 40 before deep_classify may still exist in
   analyze_core or stream.py — find and raise it so 100+ comments reach classification.

Verify on a live run that fresh pages are pulled (not repeats) and useful count climbs.
```

### 7.1 SPEED — #1 PERFORMANCE PROBLEM
Pipeline takes 500-1046 seconds (8-17 minutes). Deep classifier's sequential Ollama calls are the bottleneck. MUST be fixed before any demo. Options:
- Make deep_classify non-blocking (render dashboard first, deep signals fill async)
- Switch to gpt-4o-mini for deployed version (2-3s per call vs 60s)
- Both

**PARTIAL FIXES (2026-06-10, Claude chat session):**
- ✅ `FAST_MODE=1` env flag: skips deep classification + coverage loop, dashboard in ~2min.
- ✅ `max_tokens` scaled to `max(2500, BATCH_SIZE * 350)` — fewer truncated batches = fewer wasted calls.
- ❌ Still need gpt-4o-mini deployment for real speed.

### 7.1b OFF-TOPIC OVER-CLASSIFICATION (found 2026-06-10, live Apple Vision Pro run)
60% of classified comments (90/150) tagged off_topic. Root cause: prompt had no explicit
tier definitions — qwen2.5:7b guessed aggressively.

**FIXED (2026-06-10, Claude chat session):**
- ✅ Added explicit tier definitions (direct/ecosystem/tangential/off_topic) to classifier prompt.
- ✅ Added "CRITICAL BIAS: when in doubt, classify UP" instruction.
- ✅ max_tokens increased to prevent truncation (43/150 failed classification = wasted calls).
- Needs live verification on next run.

### 7.1c APP STORE TIMEOUT (found 2026-06-10)
Multi-country App Store scraping timed out at 8 seconds, leaked unhandled exception.

**FIXED (2026-06-10, Claude chat session):**
- ✅ App Store timeout raised from 8s to 30s.
- ✅ Reddit timeout raised from 8s to 10s.
- ✅ Skipped tasks now suppress exceptions (no more "Task exception was never retrieved").

### 7.2 Query Diversification
Reddit/YouTube often find one thread and pull all comments from it → 10 reviews about the same topic. The adaptive loop should search with multiple diverse queries per product to cover different aspects.

### 7.3 Clustering Over-Splits
10 reviews about charging split into 7 clusters. Small samples should produce fewer, broader clusters (2-3 max from 10 reviews).

### 7.4 Empty Sections Still Render
Some sections with all-zero values still appear. The evidence gate needs to be more aggressive about hiding content with insufficient data.

### 7.5 Situation Reports — Not Wired
Engine exists and is tested but not called from the pipeline.

### 7.6 Frontend Deep Signal Display
The deep classifier computes rich data (verified claims, switching flow, expectation gaps, causal chains) but the frontend doesn't display it yet. Needs new dashboard sections.

### 7.7 Customer vs Company View Differentiation
Currently very similar. Customer should lead with debate + Ask Q&A. Company should lead with metrics + roadmap.

### 7.8 Multi-Country App Store Scraping (PLANNED — next session)
Scrape the App Store / Google Play across multiple storefronts so coverage and language
diversity expand automatically — each country returns reviews in its own language:
- **Target countries:** US, UK, India (IN), Japan (JP), Germany (DE). (UK/US ≈ English; IN adds
  Hindi/English mix; JP adds Japanese; DE adds German.)
- **Where to hook in:** `backend/api/insightmesh/plugins.py :: _launch_appstore` already takes a single
  `country` (default `"us"`). Make it fan out over a country list — either (a) emit one launch per
  country, or (b) loop countries inside `backend/api/endpoints/appstore_scraper.py` and merge results.
  Tag each item with its country/language for the language-distribution stats + dedupe across stores.
- **Pattern to reuse:** the stream pipeline already fans out scrapes over `(platform, query)` and dedupes
  in §4 (see §6.15-equivalent diversification work) — multi-country is the same shape: fan out over
  `(platform, country)`, stamp the country, let the existing `seen`/`norm` dedupe collapse overlaps, and
  let the `(platform, …)`-aware interleave keep the analyzed batch balanced.
- **Why it's nearly free on language:** see §7.9 — the classifier + analyzer are multilingual, so foreign
  reviews flow through with no extra wiring; the App Store is just the cleanest multilingual source.

### 7.9 Multilingual handling — translation is OPTIONAL (design note)
- **Deep classifier needs NO changes for language.** Its prompt already works in any language because
  both backends (Ollama qwen2.5 / OpenAI gpt-4o-mini) are multilingual. Foreign-language reviews are
  classified directly — no special-casing required.
- **For gpt-4o-mini deployment: skip the translation step.** gpt-4o-mini handles 100+ languages natively,
  so it can classify/understand directly in the ORIGINAL language. Dropping the opus-mt translation hop
  (in `analyze_reviews.py` smart-understanding) is **faster AND more accurate** — translation both adds a
  model call and can hallucinate (it already does on romanized Indic text; see the existing guard there).
  Action when wiring gpt-4o-mini as primary: gate the translation/`opus-mt` path off and let the LLM read
  the original text. Keep translation only as a fallback when no multilingual LLM is available.

## 8. File Map
```
backend/
  api/endpoints/analyze_reviews.py    — 77KB core analyzer (NEVER full-read)
  api/insightmesh/stream.py           — SSE pipeline (relevance filter + deep classify wired here)
  api/insightmesh/run_pipeline.py     — non-stream pipeline + cache version gate
  api/insightmesh/debate.py           — /debate endpoint
  api/insightmesh/ask.py              — /ask Q&A endpoint (deep signals in context)
  api/insightmesh/plugins.py          — scraper config ("review" suffix, Reddit global search)
  api/routes.py                       — route registry + /api/llm/status
  insight/debate/engine.py            — multi-round RAG debate engine
  insight/debate/evidence_store.py    — numpy-backed retrieval index
  insight/deep_classify/classifier.py — 9+ signal deep classification
  insight/product_intelligence/generator.py — product domain understanding
  insight/why_layer/synthesizer.py    — per-sub-issue enrichment
  utils/llm.py                        — unified LLM client (Ollama → OpenAI → None)
  utils/db.py                         — SQLite history
  utils/cache.py                      — in-process caches
  main.py                             — FastAPI app + startup LLM status log
  .env                                — secrets (git-ignored)

insightmesh-fe/
  src/components/Insights.jsx         — main dashboard (2500+ lines)
  src/components/DebatePanel.jsx      — multi-round debate UI
  src/components/AskInsightMesh.jsx   — conversational Q&A
  src/components/ReviewsBrowser.jsx   — filterable review list
  src/lib/api.js                      — streamPipeline, runDebate, etc.
```

## 9. How to Run
```powershell
# Backend (from D:\IM_AI_folder)
.\dev.ps1 backend
# Frontend (from D:\IM_AI_folder\insightmesh-fe)
npm run dev
# Ollama
ollama list    # verify qwen2.5:7b is available
# Claude Code
cd D:\IM_AI_folder
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
claude
```

## 10. Next Session Priorities

### PRIORITY 0 (THE HEADLINE BUILD): Coverage-Driven Self-Expanding Scraper — ✅ BUILT (see §6.19)
**Status (2026-06-10): IMPLEMENTED and LIVE-VERIFIED with real qwen2.5:7b.** The engine is
`backend/insight/coverage/coverage_map.py`, wired into `stream.py`'s deferred enrich phase; the
expected feedback map lives on `ProductIntelligence.expected_feedback_categories`. Live check: "PS5" →
expected map `[controller, electrical/power, build/physical structure, heating/cooling, storage, disc
drive, software/UI, connectivity, fan noise, game library, online service quality]`; real deep_classify
mapped sample comments to the right categories; honest coverage report + gap targets rendered. See §6.19
for the full design + config. Remaining: a full end-to-end LIVE stream run is slow on Ollama (gated by
PRIORITY 1 SPEED — the coverage loop pulls more data); the frontend coverage-map display is PRIORITY 3.
The spec below is the original brief, kept for reference.

The vision: investigate a product like a human researcher.

The model should:
1. EXPECT feedback categories from understanding the product (Product Intelligence
   produces an "expected feedback map" — for PS5: controller/battery, electrical,
   structure/build, heating/cooling, storage, disc drive, software, connectivity,
   fan noise; for headphones: drivers/sound, battery, comfort, ANC, build, app,
   connectivity, mic). This is a STARTING expectation, NOT a fixed checklist.
2. Go DEEP within each category into sub-problems. "Battery" is not one slot —
   it holds fast-discharge, heating-while-charging, degradation, swelling,
   standby-drain, charger issues. Populate sub-problems from comments.
3. STAY OPEN to discovery. A comment revealing a problem that fits no expected
   category creates a NEW category. Unexpected issues are welcomed, the map grows.
4. COVERAGE-DRIVEN LOOP: scrape across MANY videos/threads (never stick to one).
   After each round, build a coverage map (comments per category/sub-problem).
   Saturation = new comments cluster with existing ones (embedding similarity)
   and stop adding sub-problems → that category is "understood," stop pulling it.
   Gap-driven expansion: for thin/empty categories, generate targeted searches
   ("<product> <category> problems/review") and scrape NEW sources for those.
   Skip what's already saturated. Stop when categories are covered AND no new
   ones emerge, OR max ~6 rounds, OR sources exhausted.
5. HONEST COVERAGE REPORT: "Well-covered: battery (incl. heating + discharge),
   build, software. Thin: connectivity (3). Discovered unexpected: disc drive
   grinding (5 owners). Couldn't find enough on: fan noise."

The goal is NOT "100 comments" — it's "enough to understand the WHOLE product,
every feature, with no major gaps." Target 100+ useful reviews as a result.

### Configurable scraper limits (part of the above):
```
SCRAPER_CONFIG = {
  "youtube": {max_videos: 8, max_comments_per_video: 50, fetch_replies: True, query_variants: 3},
  "reddit": {max_threads: 10, max_comments_per_thread: 30, include_replies: True, query_variants: 3, sort_modes: [relevance, top, new]},
  "appstore": {max_reviews: 50, countries: [us, gb, in, jp, fr]},
  "targets": {min_useful: 30, target_useful: 100, max_raw_fetch: 500},
}
```
Settings can be hidden from the UI but must drive the scraper. Multi-country
App Store scraping also gives automatic language diversity (model is already
multilingual via Ollama/gpt-4o-mini — confirmed working with HI-LATN, ES, AF, FR).

### PRIORITY 1: SPEED
Deep classification must not block the dashboard. Either make it async or switch
deployed version to gpt-4o-mini. Target: under 60 seconds. Note: the coverage
scraper will pull MORE data, making speed even more critical — gpt-4o-mini for
deployment solves both (2-3s/call vs 60s on Ollama).

### PRIORITY 2: Deployment Planning
Deploy frontend (Vercel) + backend (Railway/Render) + gpt-4o-mini for production.
Landing page, pre-computed showcase example, technical README. (Discussed,
NOT started — saved for next session.)

### PRIORITY 3: Frontend Polish
- Display deep signals (verified claims, switching flow, expectation gaps, causal chains)
- Stream debate messages one by one
- Hide truly empty sections
- Differentiate Customer vs Company views
- Show the coverage map (which features are well-understood vs thin)

### PRIORITY 6: Multi-Country App Store Scraping (see §7.8)
Fan out App Store / Google Play scraping over US, UK, IN, JP, DE so coverage + language diversity grow
automatically (each storefront returns reviews in its language). Hook in at `_launch_appstore` (already
has a `country` param). No classifier/analyzer language changes needed (§7.9). When on gpt-4o-mini, also
drop the optional translation step and classify directly in the original language (faster + more accurate).
