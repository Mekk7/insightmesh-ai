# -*- coding: utf-8 -*-
"""Export the 10 cached featured reports from the LOCAL insightmesh.db into
static JSON files for the frontend (redeploy-proof, no backend dependency).
Reads saved rows only — no analysis, no scraping."""
import sqlite3
import json
import os

DB = r"D:\IM_AI_folder\backend\data\insightmesh.db"
OUT = r"D:\IM_AI_folder\insightmesh-fe\public\featured"

# (slug, local run id, clean display label)
ITEMS = [
    ("iphone-16-pro",            24, "iPhone 16 Pro"),
    ("tesla-model-y",            13, "Tesla Model Y"),
    ("sony-wh-1000xm5",          14, "Sony WH-1000XM5"),
    ("playstation-5",            16, "PlayStation 5"),
    ("apple-vision-pro",         17, "Apple Vision Pro"),
    ("nvidia-rtx-4090",          19, "NVIDIA RTX 4090"),
    ("samsung-galaxy-s24-ultra", 21, "Samsung Galaxy S24 Ultra"),
    ("meta-quest-3",             22, "Meta Quest 3"),
    ("xbox-series-x",            11, "Xbox Series X"),
    ("macbook-pro-m3",           23, "MacBook Pro M3"),
]

os.makedirs(OUT, exist_ok=True)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

summary = []
for slug, rid, label in ITEMS:
    row = conn.execute("SELECT report_json FROM pipeline_runs WHERE id = ?", (rid,)).fetchone()
    if not row or not row["report_json"]:
        print("MISSING id=%s (%s)" % (rid, label))
        continue
    report = json.loads(row["report_json"])
    # Normalize the displayed query to the clean label.
    report.setdefault("meta", {})
    report["meta"]["query_used"] = label
    n = len(report.get("analysis", {}).get("per_review", []) or [])
    path = os.path.join(OUT, slug + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
    kb = os.path.getsize(path) / 1024
    print("wrote %-28s reviews=%-3s %6.0f KB" % (slug + ".json", n, kb))
    summary.append((slug, label, n))

print("\n%d files written to %s" % (len(summary), OUT))
