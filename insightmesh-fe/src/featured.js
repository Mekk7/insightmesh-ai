// ============================================================
// Featured showcase products (recruiter demo)
// ============================================================
// 10 products across 7 categories (phones, cars, headphones, consoles,
// GPUs, VR headsets, laptops) — proving the system handles any product
// category. Each is pre-analyzed on Balanced mode (30-44 reviews) and
// persisted to run history. `runId` points at that saved run so the
// dashboard loads INSTANTLY (no live scraping / LLM calls) when clicked.
//
// If `runId` is null (e.g. the saved run was cleared), clicking the card
// gracefully falls back to a live Balanced analysis of `query`.
//
// runId values are PRODUCTION (Railway) history IDs — the deployed frontend
// loads them from the production backend via fetchRun(id). Each was re-cached
// on Balanced with 32-95 reviews (see review counts below).
//
// To refresh: re-run `_precache_prod.py` against production, then copy the new
// ids from `_precache_prod_manifest.json` into the `runId` fields below.
// ============================================================

export const FEATURED = [
  { label: "iPhone 16 Pro",            query: "iPhone 16 Pro",            runId: 2,  tagline: "Apple flagship phone", emoji: "📱" }, // 45 reviews
  { label: "Tesla Model Y",            query: "Tesla Model Y",            runId: 3,  tagline: "Best-selling EV",       emoji: "🚗" }, // 32 reviews
  { label: "Sony WH-1000XM5",          query: "Sony WH-1000XM5",          runId: 4,  tagline: "Noise-cancelling cans", emoji: "🎧" }, // 50 reviews
  { label: "PlayStation 5",            query: "PlayStation 5",            runId: 12, tagline: "Flagship game console",  emoji: "🎮" }, // 62 reviews
  { label: "Apple Vision Pro",         query: "Apple Vision Pro",         runId: 6,  tagline: "Spatial computing",      emoji: "🥽" }, // 95 reviews
  { label: "NVIDIA RTX 4090",          query: "NVIDIA RTX 4090",          runId: 7,  tagline: "Flagship GPU",           emoji: "🖥️" }, // 46 reviews
  { label: "Samsung Galaxy S24 Ultra", query: "Samsung Galaxy S24 Ultra", runId: 8,  tagline: "Android flagship",       emoji: "📱" }, // 49 reviews
  { label: "Meta Quest 3",             query: "Meta Quest 3",             runId: 9,  tagline: "VR headset",             emoji: "🥽" }, // 89 reviews
  { label: "Xbox Series X",            query: "Xbox Series X",            runId: 10, tagline: "4K game console",        emoji: "🎮" }, // 44 reviews
  { label: "MacBook Pro M3",           query: "MacBook Pro M3",           runId: 11, tagline: "Pro laptop",             emoji: "💻" }, // 88 reviews
];
