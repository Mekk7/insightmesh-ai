// ============================================================
// Featured showcase products (recruiter demo)
// ============================================================
// 10 products across 7 categories (phones, cars, headphones, consoles,
// GPUs, VR headsets, laptops) — proving the system handles any product
// category. Each was pre-analyzed on Balanced mode (32-44 reviews).
//
// The saved report for each lives as a STATIC JSON file in the frontend at
// `public/featured/<slug>.json` (served at `/featured/<slug>.json`). Clicking
// a card loads that static file directly — instant, no backend call, no
// scraping/analysis, and redeploy-proof (does NOT depend on the Railway DB,
// which is ephemeral and wiped on every redeploy).
//
// To refresh: re-run `_export_featured.py` (reads the saved runs out of the
// local insightmesh.db into public/featured/*.json). No analysis needed.
// ============================================================

export const FEATURED = [
  { label: "iPhone 16 Pro",            query: "iPhone 16 Pro",            slug: "iphone-16-pro",            tagline: "Apple flagship phone", emoji: "📱" }, // 36 reviews
  { label: "Tesla Model Y",            query: "Tesla Model Y",            slug: "tesla-model-y",            tagline: "Best-selling EV",       emoji: "🚗" }, // 37 reviews
  { label: "Sony WH-1000XM5",          query: "Sony WH-1000XM5",          slug: "sony-wh-1000xm5",          tagline: "Noise-cancelling cans", emoji: "🎧" }, // 44 reviews
  { label: "PlayStation 5",            query: "PlayStation 5",            slug: "playstation-5",            tagline: "Flagship game console",  emoji: "🎮" }, // 34 reviews
  { label: "Apple Vision Pro",         query: "Apple Vision Pro",         slug: "apple-vision-pro",         tagline: "Spatial computing",      emoji: "🥽" }, // 35 reviews
  { label: "NVIDIA RTX 4090",          query: "NVIDIA RTX 4090",          slug: "nvidia-rtx-4090",          tagline: "Flagship GPU",           emoji: "🖥️" }, // 34 reviews
  { label: "Samsung Galaxy S24 Ultra", query: "Samsung Galaxy S24 Ultra", slug: "samsung-galaxy-s24-ultra", tagline: "Android flagship",       emoji: "📱" }, // 34 reviews
  { label: "Meta Quest 3",             query: "Meta Quest 3",             slug: "meta-quest-3",             tagline: "VR headset",             emoji: "🥽" }, // 37 reviews
  { label: "Xbox Series X",            query: "Xbox Series X",            slug: "xbox-series-x",            tagline: "4K game console",        emoji: "🎮" }, // 40 reviews
  { label: "MacBook Pro M3",           query: "MacBook Pro M3",           slug: "macbook-pro-m3",           tagline: "Pro laptop",             emoji: "💻" }, // 32 reviews
];
