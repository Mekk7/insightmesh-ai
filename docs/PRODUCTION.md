# InsightMesh AI — Production Deployment Guide

This guide goes beyond `docker compose up` and covers what you actually need before exposing this to users or the internet.

---

## Quick checklist

Before you deploy:

- [ ] **Rotated all API keys** (OpenAI, YouTube, Reddit) — see Security section below
- [ ] **`.env`** is on the server but **not** in git
- [ ] **`INSIGHTMESH_API_KEY`** is set to a strong random value (32+ bytes hex)
- [ ] **`CORS_ORIGINS`** is restricted to your real frontend domain(s) — not `*`
- [ ] **Rate limits** are tuned to your traffic shape
- [ ] **HTTPS** is terminated by a reverse proxy in front (nginx, Caddy, Cloudflare)
- [ ] **`RATE_LIMIT_TRUSTED_PROXIES`** points at that proxy's IP (so per-IP limits use the real client)
- [ ] **Backups** of `backend/data/insightmesh.db` are scheduled
- [ ] **Health checks** wired into your orchestrator (`GET /ping`)
- [ ] **Logs** going somewhere durable (`LOG_FORMAT=json` for structured ingestion)

---

## Deploying to Railway

The repo ships two ready-to-use entrypoints for Railway (or any PaaS):

- **`railway.json`** — builds from `Dockerfile.backend` and starts uvicorn bound to Railway's `$PORT`, with `/ping` as the healthcheck.
- **`Procfile`** — buildpack/nixpacks fallback (`web: uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2`).

Steps:
1. Create a new Railway project from this repo. Railway auto-detects `railway.json` (Dockerfile build).
2. Add the env vars from `.env.example` in the Railway dashboard — at minimum `OPENAI_API_KEY`, `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-4o-mini`, `YOUTUBE_API_KEY`, and the `REDDIT_*` keys.
3. Set `CORS_ORIGINS` to your deployed frontend URL.
4. Deploy. The first boot cold-starts the ML deps; `/ping` returns once ready.

The frontend (`insightmesh-fe`) is a static Vite build — deploy it separately (Railway static, Vercel, Netlify, or the bundled `Dockerfile.frontend` + nginx) and point `VITE_API_BASE_URL` at the backend's `/api`.

### Featured products (recruiter demo)

The landing page shows 10 pre-analyzed products (across phones, cars, headphones, consoles, GPUs, VR headsets, and laptops) that load instantly. To (re)generate them against a running backend:

```powershell
myenv\Scripts\python.exe _precache.py
```

This streams each product through the `/run_pipeline/stream` endpoint on **Balanced** mode (which honors `analysis_depth` and loops scraping to ~40-50 reviews, unlike the non-stream endpoint), persists them to run history, and writes `_precache_manifest.json`. Products that come back thin (<30 reviews) are automatically retried with an alternate query (e.g. `"RTX 4090 review"`). Copy the resulting `runId` values from the manifest into `insightmesh-fe/src/featured.js`. Cards with a `runId` load the saved run instantly; cards with `runId: null` fall back to a live analysis.

> Note: each Balanced stream run takes ~3-13 min and the worker can briefly block on the background deep-classify pass between runs — run the script when the backend is otherwise idle.

---

## Architecture (recommended production layout)

```
        Internet
            │
            ▼
   ┌─────────────────────┐
   │   Cloudflare /      │   ← TLS termination, DDoS, optional WAF
   │   nginx / Caddy     │
   └─────────┬───────────┘
             │  HTTP, X-Forwarded-For: <real-client>
   ┌─────────▼───────────┐
   │  insightmesh-       │   ← Multi-worker FastAPI (uvicorn / gunicorn)
   │  backend            │     Auth + rate limits enforced here
   │  (Docker or k8s)    │
   └─────────┬───────────┘
             │
   ┌─────────▼───────────┐    ┌────────────────────┐
   │   SQLite (volume)   │    │  Redis (optional)  │   ← For multi-worker cache
   │   /app/backend/data │    │  swap in for       │
   │   /insightmesh.db   │    │  TTLCache later    │
   └─────────────────────┘    └────────────────────┘
```

---

## Security

### 1. Rotate every credential you've shared during development

If you ever pasted an API key into a chat (e.g., during this build process), **treat it as public** and rotate it:

- **OpenAI**: https://platform.openai.com/api-keys → revoke + regenerate
- **YouTube Data API**: Google Cloud Console → APIs & Services → Credentials
- **Reddit**: https://www.reddit.com/prefs/apps → reset secret
- **Reddit user password**: Reddit account settings (apps don't need it for read-only scraping — leave `REDDIT_PASSWORD` blank)

### 2. Turn on API auth

```bash
# Generate a strong key
export INSIGHTMESH_API_KEY="$(openssl rand -hex 32)"

# In clients:
curl -H "X-API-Key: <that key>" https://api.example.com/api/insightmesh/run_pipeline ...
```

The backend bypasses auth for `/`, `/ping`, `/docs`, `/redoc`, `/openapi.json` — so the OpenAPI docs still work for you.

To rotate keys without downtime, set multiple at once:
```bash
INSIGHTMESH_API_KEYS=newkey,oldkey   # roll clients to newkey, then drop oldkey
```

### 3. Lock down CORS

```bash
# Not this:
CORS_ORIGINS=*

# This:
CORS_ORIGINS=https://insights.yourcompany.com,https://staging.yourcompany.com
```

### 4. Validate uploads

The `/understand/upload` and `/forecast/predict` endpoints accept arbitrary CSVs. They run on the server's filesystem under `backend/data/raw/`. The code already filenames them safely, but in a multi-tenant deployment add:
- File size limit at the reverse proxy (`client_max_body_size 20M;` in nginx)
- A periodic cleanup job for `backend/data/raw/` (otherwise it grows unbounded)

---

## Scaling

### Workers

The default Docker `CMD` runs `uvicorn ... --workers 2`. Tune based on your machine:

```dockerfile
# Rule of thumb: workers = 2 * CPU_cores + 1, capped by RAM
# Each worker loads transformers/spacy independently — ~1.5-2GB RAM each.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

Or use `gunicorn` with uvicorn workers (better signal handling):
```bash
gunicorn backend.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --graceful-timeout 30
```

### Heavy ML deps are a footgun

`transformers + torch + sentence-transformers + bertopic + prophet` adds up to ~3 GB on disk and 1.5+ GB RAM **per worker**. For high-concurrency deployments, split into two services:

- `insightmesh-api`: routes, history, exports, scrapers, forecast (lightweight)
- `insightmesh-analyzer`: a separate service that owns `analyze_core`, called over HTTP

The codebase already supports this: set `USE_HTTP_ANALYZER=1` and point the `BASE_URL` at the analyzer service. The router auto-discovers candidate URLs.

### Caching: in-process vs Redis

The current `backend/utils/cache.py` is **per-worker**. With multiple workers, each one builds its own cache. That's fine if your cache hit rate is already high (most users hit the same query within a worker's session) but breaks down at scale.

To swap for Redis, replace the `TTLCache` class with thin wrappers around `redis.Redis`:

```python
# Sketch:
import redis
_r = redis.from_url(os.getenv("REDIS_URL"))
def get(key): return json.loads(_r.get(key) or "null")
def set(key, val): _r.set(key, json.dumps(val), ex=ttl)
```

The `scraper_cache()` / `pipeline_cache()` interface stays the same — only the backing store changes.

### Rate limiting at scale

Same caveat as caching — the limiter is per-worker. For accurate global limits:

1. Move the limiter into Redis (`redis-py` + Lua INCR + EXPIRE), OR
2. Apply rate limits at the reverse proxy (nginx `limit_req_zone`, Cloudflare WAF rules) and disable the backend's limiter (`RATE_LIMIT_ENABLED=0`).

For most teams option 2 is simpler and gives you DDoS protection for free.

---

## Persistence & backups

```bash
# Volume from docker-compose.yml
docker volume inspect insightmesh-data

# Manual backup
docker run --rm -v insightmesh-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/insightmesh-backup-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm -v insightmesh-data:/data -v $(pwd):/backup alpine \
  sh -c "cd /data && tar xzf /backup/insightmesh-backup-XXXX.tar.gz --strip-components=1"
```

For SQLite specifically, you can also use the `.backup` command for hot copies:
```bash
sqlite3 backend/data/insightmesh.db ".backup '/path/to/backup.db'"
```

---

## Observability

### Structured logs

Set `LOG_FORMAT=json` in production. Each log line becomes:
```json
{"ts":"2026-05-27T18:32:11Z","level":"INFO","logger":"insightmesh.stream","msg":"...",
 "platform":"youtube","kept":42,"elapsed_ms":1234}
```

Ship to your log aggregator (Loki, Datadog, CloudWatch, etc.) and dashboard on:
- Pipeline success/error rate (filter `logger="insightmesh.api.insightmesh.run_pipeline"`)
- Per-platform fetch counts (`platform` field)
- Cache hit rate (`logger="insightmesh.cache"`)
- Auth rejections (`logger="insightmesh.auth"`)

### Health checks

| Endpoint | Use for |
|---|---|
| `GET /ping` | Load balancer health check (cheapest) |
| `GET /__routes__` | Smoke test — verifies all routers mounted |
| `GET /api/insightmesh/history/cache/stats` | Cache hit rate + size monitoring |
| `GET /api/insightmesh/history/stats` | DB connectivity + aggregate run health |

### Recommended alerts

- **5xx error rate** > 1% over 5 minutes
- **`/api/insightmesh/run_pipeline` p99 latency** > 60s
- **Cache hit rate** drops below 30%
- **DB file size** approaches available disk (`du backend/data/insightmesh.db`)
- **Rate-limit 429s** suddenly spiking (potential abuse)

---

## Cost optimization tips

1. **`SKIP_PHRASE_EXTRACTION=1`** disables the GPT-3.5 per-review classification. Falls back to heuristics — saves OpenAI cost. Recommended for high-volume.
2. **`SKIP_ACTION_ITEMS=1`** disables the GPT-4 action items call. Heuristic actions are good enough for many use-cases.
3. **`ANALYZE_BATCH_MAX=20`** (default 40) caps reviews per analyze call.
4. **`PIPELINE_CACHE_TTL_SEC=3600`** raise from 15 min default to reduce repeat work for popular queries.
5. **Use `Haiku 4.5` or `gpt-4o-mini`** instead of `gpt-4-0613` for action items — comparable quality, 10× cheaper. Set `SUGGESTIONS_MODEL=gpt-4o-mini`.

---

## Troubleshooting

### "Cache HIT but I want fresh data"

Add `"debug": true` to the request body. The pipeline always skips cache when debug is on.

### Backend slow to start

Heavy ML deps (transformers, sentence-transformers) load lazily, but the first `analyze_core` call still cold-starts them. Solutions:
- Pre-warm at startup by adding a dummy analyze call to `lifespan`
- Or use HTTP analyzer split (see Scaling section)

### "Rate limit 429" but I'm only one user

You're behind a proxy that's not in `RATE_LIMIT_TRUSTED_PROXIES`, so all your requests look like they come from the proxy's IP. Fix:
```bash
RATE_LIMIT_TRUSTED_PROXIES=10.0.0.1   # your proxy's internal IP
```

### "PRAW errors: 401 Unauthorized"

Reddit credentials are bad. Test with:
```bash
curl http://localhost:8000/api/reviews/scrape/reddit/_ping
```
Expect `{"ok": true, "status": "ready"}`. If `not_ready`, check the `REDDIT_*` env vars.

---

## What this repo does NOT do (yet)

Worth knowing before you ship:

- **No user accounts / multi-tenancy.** Auth is a single shared key. For per-user data isolation, add a JWT layer and a `user_id` column to `pipeline_runs`.
- **No request signing or replay protection.** API key auth is fine for trusted clients; not enough for public-facing.
- **No data retention policy.** Run history grows forever. Add a cron job to `DELETE FROM pipeline_runs WHERE created_at < datetime('now', '-90 days')` if you need GDPR compliance.
- **No webhook callbacks.** Pipeline runs are synchronous — clients block until done. For async, add a job queue (RQ, Celery, or Arq).
- **No PII scrubbing.** Scraped Reddit/YouTube comments are stored verbatim in the history. Add a scrubber if your users could trigger scrapes of comments containing emails/names.
