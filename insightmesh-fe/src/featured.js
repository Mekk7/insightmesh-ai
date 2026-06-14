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
// To refresh: re-run `_precache.py`, then copy the new ids from
// `_precache_manifest.json` into the `runId` fields below.
// ============================================================

export const FEATURED = [
  { label: "iPhone 16 Pro",            query: "iPhone 16 Pro",            runId: 24, tagline: "Apple flagship phone", emoji: "📱" },
  { label: "Tesla Model Y",            query: "Tesla Model Y",            runId: 13, tagline: "Best-selling EV",       emoji: "🚗" },
  { label: "Sony WH-1000XM5",          query: "Sony WH-1000XM5",          runId: 14, tagline: "Noise-cancelling cans", emoji: "🎧" },
  { label: "PlayStation 5",            query: "PlayStation 5",            runId: 16, tagline: "Flagship game console",  emoji: "🎮" },
  { label: "Apple Vision Pro",         query: "Apple Vision Pro",         runId: 17, tagline: "Spatial computing",      emoji: "🥽" },
  { label: "NVIDIA RTX 4090",          query: "NVIDIA RTX 4090",          runId: 19, tagline: "Flagship GPU",           emoji: "🖥️" },
  { label: "Samsung Galaxy S24 Ultra", query: "Samsung Galaxy S24 Ultra", runId: 21, tagline: "Android flagship",       emoji: "📱" },
  { label: "Meta Quest 3",             query: "Meta Quest 3",             runId: 22, tagline: "VR headset",             emoji: "🥽" },
  { label: "Xbox Series X",            query: "Xbox Series X",            runId: 11, tagline: "4K game console",        emoji: "🎮" },
  { label: "MacBook Pro M3",           query: "MacBook Pro M3",           runId: 23, tagline: "Pro laptop",             emoji: "💻" },
];
