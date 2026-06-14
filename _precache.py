# -*- coding: utf-8 -*-
"""
Pre-cache showcase products on BALANCED mode via the streaming endpoint.

The streaming endpoint (stream.py) honors analysis_depth — Balanced targets
~50 useful reviews (max_raw_fetch 400) and loops scraping until it gets there,
which the non-stream endpoint does not. It also persists to run history via
save_run() *before* emitting the `complete` SSE event, so once we receive
`complete` the run is safely in history and we can stop reading.

For each product:
  - stream a fresh Balanced run (debug=True forces a recompute + guaranteed save)
  - read the SSE until `complete`, capture the report + review count
  - confirm the new history id by diffing max(id) before/after
  - if reviews < 30, retry once with an alternate query
Writes a results table to _precache_run2.log and a manifest to _precache_manifest.json.
"""
import json
import time
import datetime
import requests

BASE = "http://127.0.0.1:8000/api"
LOG = r"D:\IM_AI_folder\_precache_run2.log"
MANIFEST = r"D:\IM_AI_folder\_precache_manifest.json"

PRODUCTS = [
    ("iPhone 16 Pro",          "iPhone 16 Pro",          "iPhone 16 Pro review"),
    ("Tesla Model Y",          "Tesla Model Y",          "Tesla Model Y review"),
    ("Sony WH-1000XM5",        "Sony WH-1000XM5",        "Sony WH-1000XM5 review"),
    ("PlayStation 5",          "PlayStation 5",          "PlayStation 5 review"),
    ("Apple Vision Pro",       "Apple Vision Pro",       "Apple Vision Pro review"),
    ("NVIDIA RTX 4090",        "NVIDIA RTX 4090",        "RTX 4090 review"),
    ("Samsung Galaxy S24 Ultra", "Samsung Galaxy S24 Ultra", "Galaxy S24 Ultra review"),
    ("Meta Quest 3",           "Meta Quest 3",           "Meta Quest 3 review"),
    ("Xbox Series X",          "Xbox Series X",          "Xbox Series X review"),
    ("MacBook Pro M3",         "MacBook Pro M3",         "MacBook Pro M3 review"),
]

# Already pre-computed in a prior validation run — skip to avoid a redundant
# paid run. Keyed by label; value is the result row to record as-is.
PRECOMPUTED = {
    "Xbox Series X": {"label": "Xbox Series X", "query": "Xbox Series X", "runId": 11, "reviews": 40, "seconds": 633},
}


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def max_id():
    try:
        r = requests.get(BASE + "/insightmesh/history?limit=1", timeout=30)
        items = r.json().get("items", [])
        return int(items[0]["id"]) if items else 0
    except Exception as e:
        log("   max_id error: %s" % e)
        return -1


def review_count(report):
    try:
        return len((report or {}).get("analysis", {}).get("per_review", []) or [])
    except Exception:
        return -1


def run_stream(query):
    """Stream a Balanced run; return (status, report). Stops at `complete`."""
    body = {
        "input_mode": "consumer",
        "platforms": ["youtube", "reddit"],
        "mode": "fast",
        "query_override": query,
        "analysis_depth": "balanced",
        "debug": True,
    }
    final = None
    event = None
    try:
        with requests.post(BASE + "/insightmesh/run_pipeline/stream",
                           json=body, stream=True, timeout=(30, 900)) as resp:
            if resp.status_code != 200:
                return ("http_%s" % resp.status_code, None)
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("event:"):
                    event = raw.split(":", 1)[1].strip()
                elif raw.startswith("data:"):
                    data = raw.split(":", 1)[1].strip()
                    if event in ("complete", "enriched"):
                        try:
                            payload = json.loads(data)
                            final = payload.get("final_report", payload)
                        except Exception:
                            pass
                        if event == "complete":
                            # save_run() already ran server-side — safe to stop.
                            return ("complete", final)
                    elif event == "error":
                        return ("error", None)
        return ("closed_no_complete", final)
    except Exception as e:
        return ("exception: %s" % e, final)


def do_product(label, query, alt):
    log("--- %s : '%s' ---" % (label, query))
    before = max_id()
    t0 = time.time()
    status, report = run_stream(query)
    secs = int(time.time() - t0)
    after = max_id()
    run_id = after if after > before else -1
    count = review_count(report) if report else (
        # fall back to the freshly-saved row if the report wasn't captured
        _count_from_history(run_id) if run_id > 0 else -1)
    log("   status=%s id=%s reviews=%s time=%ss" % (status, run_id, count, secs))

    if count >= 0 and count < 30:
        log("   thin (%s<30) -> retry with '%s'" % (count, alt))
        before2 = max_id()
        t1 = time.time()
        status2, report2 = run_stream(alt)
        secs2 = int(time.time() - t1)
        after2 = max_id()
        run_id2 = after2 if after2 > before2 else -1
        count2 = review_count(report2) if report2 else (
            _count_from_history(run_id2) if run_id2 > 0 else -1)
        log("   retry status=%s id=%s reviews=%s time=%ss" % (status2, run_id2, count2, secs2))
        if count2 > count:
            run_id, count, query, secs = run_id2, count2, alt, secs + secs2

    return {"label": label, "query": query, "runId": run_id, "reviews": count, "seconds": secs}


def _count_from_history(run_id):
    try:
        r = requests.get(BASE + "/insightmesh/history/%s" % run_id, timeout=60)
        return review_count(r.json().get("report"))
    except Exception:
        return -1


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("=== precache2 start %s ===\n" % datetime.datetime.now().isoformat())
    results = []
    for label, query, alt in PRODUCTS:
        if label in PRECOMPUTED:
            pc = PRECOMPUTED[label]
            log("--- %s : SKIP (precomputed) id=%s reviews=%s ---" % (label, pc["runId"], pc["reviews"]))
            results.append(pc)
            continue
        results.append(do_product(label, query, alt))

    log("==========================================================")
    log("RESULTS TABLE")
    log("%-26s %-7s %-9s %s" % ("PRODUCT", "RUN_ID", "REVIEWS", "TIME(s)"))
    for r in results:
        log("%-26s %-7s %-9s %s" % (r["label"], r["runId"], r["reviews"], r["seconds"]))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("=== precache2 done %s ===" % datetime.datetime.now().isoformat())


if __name__ == "__main__":
    main()
