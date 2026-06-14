"""
Manual smoke test: does the deep classifier extract PRODUCT INTELLIGENCE from
ecosystem (game) comments, and correctly return nothing for pure noise?

Run:  d:\IM_AI_folder\myenv\Scripts\python.exe -m backend.insight.deep_classify._ps5_smoke_test
Requires Ollama (qwen2.5:7b) up. Not part of the automated suite — a human-readable probe.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

from backend.utils import llm as llm_client  # noqa: E402
from backend.insight.product_intelligence.generator import generate_product_intelligence  # noqa: E402
from backend.insight.deep_classify.classifier import deep_classify_reviews, aggregate_deep_signals  # noqa: E402

# (text, sentiment-stars, what we EXPECT: "intel" = should reveal product info, "noise" = should be null)
CASES = [
    ("GTA 6 runs at a locked 60fps on PS5, looks unreal", "5 stars", "intel"),
    ("The DualSense haptics make driving in GTA feel incredible, you feel every bump", "5 stars", "intel"),
    ("I bought a PS5 purely for Spider-Man 2 and it was worth it", "5 stars", "intel"),
    ("My console sounds like a jet engine and gets super hot when I play Elden Ring", "2 stars", "intel"),
    ("Load times went from 40 seconds on PS4 to like 2 seconds, the SSD is insane", "5 stars", "intel"),
    ("Spider-Man 2 has an amazing story and great voice acting", "5 stars", "noise"),
    ("GTA is haram and corrupts the youth", "1 star", "noise"),
    ("first", "1 star", "noise"),
    ("Returned my Xbox Series X for this, no regrets", "5 stars", "intel"),
    ("Stand quality is cheap plastic, my PS5 wobbles on the official stand", "2 stars", "intel"),
    ("Who else is watching this in 2026??", "3 stars", "noise"),
    ("The new firmware update bricked my console, had to RMA it", "1 star", "intel"),
]


def main() -> int:
    print(f"LLM backend: {llm_client.available_backend()}")
    intel = generate_product_intelligence("PlayStation 5", llm_client)
    print(f"Product intel: {intel.category} | direct={intel.direct_aspects} | ecosystem={intel.ecosystem_aspects}\n")

    per_review = [{"translated_text": t, "sentiment": s, "is_relevant": True} for t, s, _ in CASES]
    deep_classify_reviews(per_review, llm_client, intel)

    correct = 0
    for (text, _stars, expect), r in zip(CASES, per_review):
        pi = (r.get("deep") or {}).get("product_insight")
        got = "intel" if pi else "noise"
        ok = "OK " if got == expect else "XX "
        if got == expect:
            correct += 1
        print(f"{ok}[expect {expect:5} got {got:5}] {text[:70]}")
        if pi:
            print(f"        -> aspect={pi['aspect']} sent={pi['sentiment']} conf={pi['confidence']} via={pi['evidence_type']}")
            print(f"        -> insight: {pi['insight']}")
    print(f"\nScore: {correct}/{len(CASES)} cases matched expectation")

    agg = aggregate_deep_signals(per_review)
    print(f"\nn_product_insights={agg['n_product_insights']}  n_ecosystem_intelligence={agg['n_ecosystem_intelligence']}")
    print("Insights by aspect:")
    for asp, b in agg["product_insights_by_aspect"].items():
        print(f"  {asp}: +{b['positive']}/-{b['negative']} (from_ecosystem={b['from_ecosystem']})")
    return 0 if correct == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
