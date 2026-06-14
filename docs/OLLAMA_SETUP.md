# Ollama Setup — Unlock the AI Layer for Free

InsightMesh AI uses Ollama for the smart features (LLM-driven taxonomy, sub-cluster naming, why-narratives, smart summaries). Without it, everything still works but falls back to keyword-based output. With it, the depth and quality dramatically increase — and it's **free, local, and private**.

## Step 1 — Install Ollama (2 minutes)

**Windows / Mac / Linux:**

Go to https://ollama.com and download the installer. Run it. Done.

After install, Ollama runs as a background service on `http://localhost:11434`. You don't need to launch it manually — it auto-starts.

## Step 2 — Pull a model (one-time, ~3 GB)

Open a new terminal and run:

```powershell
ollama pull llama3.2:3b
```

This downloads Llama 3.2 (3 billion parameters) — small enough to run on a CPU, smart enough for our use cases. Wait for it to finish (typically 3-10 minutes depending on bandwidth).

**Alternative models** if you have more RAM and want better quality:

- `llama3.2:1b` — tiny, very fast, fine for sub-cluster naming
- `llama3.2:3b` — **recommended default**, good balance
- `qwen2.5:7b` — higher quality, needs ~8GB free RAM
- `mistral:7b` — also great, similar requirements

You can change the model used by InsightMesh in `.env`:

```
OLLAMA_MODEL=llama3.2:3b
```

## Step 3 — Verify it works

```powershell
ollama list
```

Should show your downloaded model(s).

```powershell
ollama run llama3.2:3b "Say hi in one word"
```

Should respond with one word and exit.

## Step 4 — Restart the InsightMesh backend

```powershell
# Ctrl+C the backend if it's running, then:
.\dev.ps1 backend
```

On startup, the backend probes Ollama. You should see something like this in the logs when an analysis runs:

```
[insightmesh.llm] backend=ollama model=llama3.2:3b
```

Or check the dashboard — the Smart Summary card should now show **✨ AI summary** in the badge, and the Aspect Hierarchy card should show **✨ AI taxonomy**.

## What "with Ollama" unlocks

| Feature | Without Ollama | With Ollama |
|---|---|---|
| Smart Summary | Heuristic narrative from structured signals | LLM-written 3-4 sentence story with nuance |
| Aspect Taxonomy | Hand-coded for ~4 domains (auto/audio/XR/phone) | Learned per product from real reviews — works on **any** product |
| Sub-cluster names | TF-IDF bigram (e.g. "Battery Charging") | Specific themes ("Highway Range Below Spec") |
| Why-layer narratives | Stitched from extracted signals | Plain-English explanation with persona / geo context |
| Phrase extraction | None | Praise/Complaint/Suggestion/Prediction phrases per review |
| Action items | Heuristic per aspect | Prioritized engineering-grade recommendations |

## When you're ready to invest later — OpenAI

When you want top-quality LLM output without the local-CPU latency, add to `.env`:

```
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
```

The system will auto-detect and prefer OpenAI when Ollama isn't running, or you can force it:

```python
# In backend code (already wired):
llm_client.chat(messages, prefer="openai")
```

**The LLM cache is persistent** (`backend/data/llm_cache.db`) — every prompt+model combination is cached forever. Re-runs of the same product use cached responses for free. This means once you "warm up" the cache for your top products, subsequent users get instant answers at zero cost.

## Troubleshooting

**Ollama not detected by InsightMesh:**

```powershell
curl http://localhost:11434/api/tags
```

If this fails, Ollama isn't running. On Windows, open Task Manager → check if "ollama" or "ollama-app" is running. If not, search for "Ollama" in Start menu and launch it.

**Out of memory errors:**

Switch to a smaller model:
```
OLLAMA_MODEL=llama3.2:1b
```

**Slow responses:**

3-7 second responses on first call are normal for the 3B model on CPU. Subsequent calls are faster (model stays in memory). If chronically slow, the model is too large for your machine — drop to `llama3.2:1b`.

## How to know it's working

1. Run any product analysis (or click a demo product)
2. Open the dashboard → check the **Smart Summary** card. If the badge says **✨ AI summary**, Ollama is in the loop.
3. Check the **detailed breakdown** card. If the badge says **✨ AI taxonomy**, the LLM proposed the aspect taxonomy for this product.
4. Each sub-issue should have a **Why →** narrative line. With Ollama these read like fluent English; without they're stitched-together templates.
