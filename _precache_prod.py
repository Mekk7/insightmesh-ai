# -*- coding: utf-8 -*-
"""
Pre-cache the 10 showcase products on BALANCED mode against the PRODUCTION
Railway backend. Writes run IDs to _precache_prod_manifest.json for featured.js.

- Streams each product (debug=True -> fresh recompute + guaranteed save_run).
- Reads the SSE until `complete`; captures the report, the analyzed review
  count, and the number of diversified queries from the `scrape_started`
  event (4 == the new Balanced preset is live; 3 == old code still deployed).
- Confirms the new run id by diffing max(id) before/after (robust to the
  worker briefly blocking on deep-classify).
- Retries once with an alternate query if a product comes back < 30 reviews.
- Deletes the old iPhone history run(s) first (per the re-cache request).
"""
import json
import time
import datetime
import requests

BASE = "https://insightmesh-ai-production.up.railway.app/api"
LOG = r"D:\IM_AI_folder\_precache_prod.log"
MANIFEST = r"D:\IM_AI_folder\_precache_prod_manifest.json"

PRODUCTS = [
    ("iPhone 16 Pro",            "iPhone 16 Pro",            "iPhone 16 Pro review"),
    ("Tesla Model Y",            "Tesla Model Y",            "Tesla Model Y review"),
    ("Sony WH-1000XM5",          "Sony WH-1000XM5",          "Sony WH-1000XM5 review"),
    ("PlayStation 5",            "PlayStation 5",            "PlayStation 5 review"),
    ("Apple Vision Pro",         "Apple Vision Pro",         "Apple Vision Pro review"),
    ("NVIDIA RTX 4090",          "NVIDIA RTX 4090",          "RTX 4090 review"),
    ("Samsung Galaxy S24 Ultra", "Samsung Galaxy S24 Ultra", "Galaxy S24 Ultra review"),
    ("Meta Quest 3",             "Meta Quest 3",             "Meta Quest 3 review"),
    ("Xbox Series X",            "Xbox Series X",            "Xbox Series X review"),
    ("MacBook Pro M3",           "MacBook Pro M3",           "MacBook Pro M3 review"),
]


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _get(url, **kw):
    kw.setdefault("timeout", 60)
    return requests.get(url, **kw)


def max_id():
    last = None
    for _ in range(20):
        try:
            items = _get(BASE + "/insightmesh/history?limit=1").json().get("items", [])
            return int(items[0]["id"]) if items else 0
        except Exception as e:
            last = e
            time.sleep(15)
    raise last


def review_count(report):
    try:
        return len((report or {}).get("analysis", {}).get("per_review", []) or [])
    except Exception:
        return -1


def delete_iphone_runs():
    """Delete any existing iPhone runs in production history (stale 25-review cache)."""
    try:
        items = _get(BASE + "/insightmesh/history?limit=200").json().get("items", [])
    except Exception as e:
        log("could not list history to delete iPhone: %s" % e)
        return
    for it in items:
        q = (it.get("query") or "").lower()
        if "iphone" in q:
            rid = it.get("id")
            try:
                r = requests.delete(BASE + "/insightmesh/history/%s" % rid, timeout=30)
                log("deleted old iPhone run id=%s (query=%r) -> %s" % (rid, it.get("query"), r.status_code))
            except Exception as e:
                log("failed deleting iPhone run id=%s: %s" % (rid, e))


def run_stream(query):
    """Stream a Balanced run; return (status, report, n_queries). Stops at `complete`."""
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
    n_queries = None
    try:
        with requests.post(BASE + "/insightmesh/run_pipeline/stream",
                           json=body, stream=True, timeout=(30, 900)) as resp:
            if resp.status_code != 200:
                return ("http_%s" % resp.status_code, None, None)
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("event:"):
                    event = raw.split(":", 1)[1].strip()
                elif raw.startswith("data:"):
                    data = raw.split(":", 1)[1].strip()
                    if event == "progress" and n_queries is None:
                        try:
                            p = json.loads(data)
                            if p.get("stage") == "scrape_started":
                                n_queries = len(p.get("queries") or [])
                        except Exception:
                            pass
                    elif event in ("complete", "enriched"):
                        try:
                            final = json.loads(data).get("final_report", None)
                        except Exception:
                            pass
                        if event == "complete":
                            return ("complete", final, n_queries)
                    elif event == "error":
                        return ("error", None, n_queries)
        return ("closed_no_complete", final, n_queries)
    except Exception as e:
        return ("exception: %s" % e, final, n_queries)


def count_from_history(run_id):
    try:
        return review_count(_get(BASE + "/insightmesh/history/%s" % run_id).json().get("report"))
    except Exception:
        return -1


def do_product(label, query, alt):
    log("--- %s : '%s' ---" % (label, query))
    before = max_id()
    t0 = time.time()
    status, report, nq = run_stream(query)
    secs = int(time.time() - t0)
    after = max_id()
    run_id = after if after > before else -1
    count = review_count(report) if report else (count_from_history(run_id) if run_id > 0 else -1)
    log("   status=%s id=%s reviews=%s queries=%s time=%ss" % (status, run_id, count, nq, secs))

    if count >= 0 and count < 30:
        log("   thin (%s<30) -> retry with '%s'" % (count, alt))
        before2 = max_id()
        t1 = time.time()
        status2, report2, nq2 = run_stream(alt)
        secs2 = int(time.time() - t1)
        after2 = max_id()
        run_id2 = after2 if after2 > before2 else -1
        count2 = review_count(report2) if report2 else (count_from_history(run_id2) if run_id2 > 0 else -1)
        log("   retry status=%s id=%s reviews=%s queries=%s time=%ss" % (status2, run_id2, count2, nq2, secs2))
        if count2 > count:
            run_id, count, query, secs = run_id2, count2, alt, secs + secs2

    return {"label": label, "query": query, "runId": run_id, "reviews": count, "seconds": secs}


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("=== precache PROD start %s ===\n" % datetime.datetime.now().isoformat())
    log("deleting old iPhone history runs...")
    delete_iphone_runs()
    results = []
    for label, query, alt in PRODUCTS:
        results.append(do_product(label, query, alt))

    log("==========================================================")
    log("RESULTS TABLE (PRODUCTION)")
    log("%-26s %-7s %-9s %s" % ("PRODUCT", "RUN_ID", "REVIEWS", "TIME(s)"))
    for r in results:
        log("%-26s %-7s %-9s %s" % (r["label"], r["runId"], r["reviews"], r["seconds"]))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("=== precache PROD done %s ===" % datetime.datetime.now().isoformat())


if __name__ == "__main__":
    main()
