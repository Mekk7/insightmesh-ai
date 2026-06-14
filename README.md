# InsightMesh AI

> Dual-input product intelligence platform — feed it a product name or a sales CSV and get back sentiment, themes, complaints, suggestions, executive summaries, and forecasts.

![status](https://img.shields.io/badge/status-active%20development-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![node](https://img.shields.io/badge/node-20%2B-green)

> **🤖 Working with an AI assistant on this project?** Point it at
> [`docs/CLAUDE_ONBOARDING.md`](docs/CLAUDE_ONBOARDING.md) FIRST. That file is the
> self-contained context handoff (architecture, history, decisions, gotchas) so a
> fresh session gets fully up to speed with zero re-discovery. Keep it updated at
> the end of each major session.

---

## What it does

Two operating modes share the same downstream pipeline:

- **Consumer mode** — type `"Sony WH-1000XM6"` → scrape YouTube + Reddit → analyze → dashboard
- **Company mode** — upload a sales CSV → auto-detect the product column → categorize → scrape platforms for those products → analyze + forecast

Both modes flow through the same analyzer: relevance filtering, sentiment, zero-shot topic tagging, keyphrase extraction, canonical clustering (HDBSCAN → Agglomerative → greedy fallback), solution generation (heuristic + optional RAG + optional LLM), executive summarization, and prioritized action items.

---

## Architecture at a glance

```
┌──────────────────┐                ┌──────────────────────────────────┐
│  React + Vite    │   HTTP /api    │  FastAPI                         │
│  Dashboard       │ ─────────────► │  ├─ /understand  (CSV profile)   │
│  (Tailwind)      │                │  ├─ /forecast    (Prophet)       │
└──────────────────┘                │  ├─ /reviews/scrape/{yt,reddit}  │
                                    │  ├─ /reviews/analyze             │
                                    │  └─ /insightmesh                 │
                                    │     ├─ /categorize               │
                                    │     ├─ /run_pipeline    ◄── main │
                                    │     ├─ /orchestrate              │
                                    │     └─ /history                  │
                                    └────────────────┬─────────────────┘
                                                     │
              ┌──────────────────────────────────────┼──────────────────────────┐
              ▼                                      ▼                          ▼
       YouTube Data API                    Reddit (PRAW)               OpenAI / Local models
                                                                       (transformers, prophet)
```

**Storage:** SQLite for run history (`backend/data/insightmesh.db`), filesystem for uploaded CSVs and generated profile HTMLs.

**Caching:** In-process TTL cache for pipeline runs (skip repeat scrapes within ~15 min by default).

---

## Quick start (local)

### Prerequisites
- Python 3.10+
- Node 20+
- An OpenAI API key (optional but recommended)
- A YouTube Data API v3 key (required for YouTube scraping)
- Reddit app credentials (required for Reddit scraping)

### 1. Clone & set up env
```bash
git clone <your-repo-url>
cd IM_AI_folder
cp .env.example .env
# Edit .env and fill in your real API keys (NEVER commit this file)
```

### 2. Backend
```bash
# Create a virtualenv (or use the existing myenv/)
python -m venv myenv
# Windows
myenv\Scripts\activate
# macOS/Linux
source myenv/bin/activate

pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Run the FastAPI server
uvicorn backend.main:app --reload --port 8000
# → http://127.0.0.1:8000/docs  (Swagger UI)
```

### 3. Frontend
```bash
cd insightmesh-fe
npm install
npm run dev
# → http://127.0.0.1:5173
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000` (see `vite.config.js`).

---

## Run with Docker (one command)

```bash
docker compose up --build
# Frontend:  http://localhost:5173
# Backend:   http://localhost:8000
```

The `backend` service mounts a named volume (`insightmesh-data`) so your run history and uploaded CSVs survive container restarts.

---

## Endpoint cheat sheet

| Method | Path | What |
|---|---|---|
| `POST` | `/api/insightmesh/run_pipeline` | **Main entry point.** Consumer or company mode → full report |
| `POST` | `/api/insightmesh/categorize` | Tag a CSV by product/category |
| `POST` | `/api/reviews/analyze` | Analyzer only — pass raw review strings |
| `POST` | `/api/reviews/scrape/youtube` | YouTube search + comments |
| `POST` | `/api/reviews/scrape/reddit` | Reddit subreddit comments |
| `POST` | `/api/understand/upload` | Upload CSV → role detection + profile |
| `POST` | `/api/forecast/predict` | Upload sales CSV → Prophet forecast |
| `GET`  | `/api/insightmesh/history` | List past pipeline runs |
| `GET`  | `/api/insightmesh/history/{id}` | Get one past run (full report) |
| `GET`  | `/api/insightmesh/history/stats` | Aggregate stats |
| `GET`  | `/api/insightmesh/history/cache/stats` | Cache hit-rate diagnostics |
| `POST` | `/api/insightmesh/history/cache/clear` | Clear in-process caches |

Full OpenAPI docs at `http://localhost:8000/docs`.

---

## Configuration (env vars)

All optional unless noted. See `.env.example` for the full annotated list.

| Var | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Enables GPT phrase extraction, action items, solution refinement |
| `YOUTUBE_API_KEY` | — | **Required** for YouTube scraping |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USER_AGENT` | — | **Required** for Reddit scraping |
| `API_PREFIX` | `/api` | Mount path for the API router |
| `CORS_ORIGINS` | `*` | Comma-separated origins; tighten in prod |
| `FILTER_STRICTNESS` | `normal` | `low \| normal \| high \| ultra` |
| `ANALYZE_BATCH_MAX` | `40` | Cap on reviews per analyze call (cost guard) |
| `PIPELINE_CACHE_TTL_SEC` | `900` | Pipeline-result cache TTL (skip when `debug=true`) |
| `PIPELINE_CACHE_SIZE` | `64` | Max cached pipeline results |
| `INSIGHTMESH_DB_PATH` | `backend/data/insightmesh.db` | SQLite location for run history |

---

## Project structure

```
IM_AI_folder/
├── backend/
│   ├── api/
│   │   ├── endpoints/       # understand, forecast, analyze_reviews, scrape_*
│   │   └── insightmesh/     # categorize, run_pipeline, orchestrator, history, plugins
│   ├── core/                # (reserved for forecast engine abstractions)
│   ├── data/                # raw CSVs, processed HTMLs, SQLite DB
│   ├── insight/
│   │   ├── actions/         # suggester (4T: triage/temp/targeted/telemetry)
│   │   ├── filters/         # universal relevance prefilter (auto-lexicon)
│   │   ├── reasons/         # canonical clustering (HDBSCAN/Agglom/greedy)
│   │   └── solutions/       # solution generator (heuristic + RAG + LLM)
│   ├── models/              # (reserved for shared Pydantic schemas)
│   ├── utils/               # column_guesser, filtering, cache, db
│   └── main.py              # FastAPI app + lifespan
├── insightmesh-fe/          # React 19 + Vite + Tailwind + Recharts
│   └── src/
│       ├── App.jsx          # main dashboard (Insights/Scraper/Analyzer/Understand/Forecast)
│       └── components/
├── requirements.txt
├── pyproject.toml
├── Dockerfile.backend
├── Dockerfile.frontend
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Roadmap

- [x] Phase 1 — Critical bug fixes, security hygiene
- [x] Phase 2 — Caching, run-history persistence, Docker deployment
- [ ] Phase 3 — Test coverage, refactor, kill dead code
- [ ] Phase 4 — Run-history UI, comparison mode, PDF/Markdown export, real-time progress (SSE)
- [ ] Phase 5 — CI/CD, multi-worker production tuning, optional Redis cache, auth

---

## License

Proprietary. All rights reserved.
