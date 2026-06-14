// insightmesh-fe/src/components/ReviewsBrowser.jsx
//
// The "actually show me the reviews" panel.
//
// WHY THIS EXISTS
// ---------------
// The dashboard was all aggregate analysis (TrustScore, clusters, charts) but a
// human deciding "should I buy this?" wants to READ what real people said. The
// old UI buried just 3 sample comments at the bottom. This surfaces EVERY review
// we collected, browsable, searchable, filterable, with:
//   - the original text (in any language)
//   - a faithful English translation underneath (when foreign / code-mixed)
//   - star rating, category, emotion, platform, likes, sarcasm flag, buyer intent
//   - a link back to the source when we have one
//
// It's the heart of the consumer experience: transparent evidence, not just a verdict.

import React, { useMemo, useState } from "react";

// ---- Small presentational helpers (kept local so this file is drop-in) ----

const STAR_TO_NUM = { "1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5 };

const CAT_TONE = {
  Praise: { chip: "bg-emerald-900/30 text-emerald-200 border-emerald-800", dot: "bg-emerald-500" },
  Complaint: { chip: "bg-rose-900/30 text-rose-200 border-rose-800", dot: "bg-rose-500" },
  Suggestion: { chip: "bg-violet-900/30 text-violet-200 border-violet-800", dot: "bg-violet-500" },
  Prediction: { chip: "bg-amber-900/30 text-amber-200 border-amber-800", dot: "bg-amber-500" },
  Neutral: { chip: "bg-zinc-800 text-zinc-300 border-zinc-700", dot: "bg-zinc-500" },
};

const PLATFORM_ICON = {
  youtube: "▶",
  reddit: "⬛",
  tiktok: "♪",
  amazon: "▢",
  appstore: "",
  googleplay: "▷",
};

// Review-intelligence accessor — the Intelligence Synthesizer attaches an
// `_intelligence` block (composite 0-1 + HIGH/MEDIUM/LOW). Falls back to the
// legacy flat `quality` field so the browser still works pre-synthesis.
function reviewIntel(r) {
  const intel = r && r._intelligence;
  if (intel && typeof intel.composite === "number") return intel;
  if (r && typeof r.quality === "number") {
    const c = r.quality;
    return { composite: c, label: c >= 0.5 ? "HIGH" : c >= 0.3 ? "MEDIUM" : "LOW", _legacy: true };
  }
  return null;
}

function intelScore(r) {
  const i = reviewIntel(r);
  return i ? i.composite : 0;
}

function Stars({ n }) {
  const v = Math.max(0, Math.min(5, n || 0));
  return (
    <span className="text-amber-400 text-xs tracking-tight" aria-label={`${v} out of 5 stars`}>
      {"★".repeat(v)}
      <span className="text-zinc-700">{"★".repeat(5 - v)}</span>
    </span>
  );
}

function sourceUrl(r) {
  // Best-effort deep link back to where the comment came from.
  const p = (r.platform || "").toLowerCase();
  if (p === "youtube" && r.source_id) return `https://www.youtube.com/watch?v=${r.source_id}`;
  if (p === "reddit") {
    if (r.subreddit && r.source_id) return `https://www.reddit.com/r/${r.subreddit}/comments/${r.source_id}`;
    if (r.source_id) return `https://www.reddit.com/comments/${r.source_id}`;
  }
  return null;
}

// One review card
function ReviewCard({ r }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const stars = STAR_TO_NUM[r.sentiment] || 0;
  const cat = r.review_category || "Neutral";
  const tone = CAT_TONE[cat] || CAT_TONE.Neutral;
  const lang = (r.language || "").toUpperCase();
  const isForeign = lang && !["EN", "UNKNOWN", "?", ""].includes(lang);
  const english = r.translated_text || r.understanding_english || null;
  const link = sourceUrl(r);
  const platform = (r.platform || "").toLowerCase();
  const picon = PLATFORM_ICON[platform];

  // What to show as the main, readable text:
  // - foreign + we have an English gloss → show English as primary, original toggle below
  // - else → show original
  const primaryText = isForeign && english ? english : (r.original || "");
  const secondaryText = isForeign && english ? (r.original || "") : null;

  const emotion = r.emotion && r.emotion !== "neutral" ? r.emotion : null;
  const sarcastic = (r.sarcasm || {}).is_sarcastic || r.is_sarcastic_llm;
  const intent = r.buyer_intent && r.buyer_intent !== "UNKNOWN" ? r.buyer_intent : null;
  const intel = reviewIntel(r);
  const isHigh = intel && intel.label === "HIGH";
  const isLow = intel && intel.label === "LOW";
  const cred = r._credibility || null;
  const credHigh = cred && cred.credibility_label === "HIGH";
  const credLow = cred && cred.credibility_label === "LOW";

  return (
    <div className={`rounded-xl border bg-zinc-900/40 p-3.5 transition hover:border-zinc-700 ${isHigh ? "border-l-2 border-l-emerald-500/70 border-zinc-800" : "border-zinc-800"} ${(isLow || credLow) ? "opacity-80" : ""}`}>
      {/* Header row: platform / author / lang on the left, rating + category on the right */}
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0 text-[11px] text-zinc-500">
          {picon ? <span className="text-zinc-400" title={platform}>{picon}</span> : null}
          {r.author ? <span className="truncate max-w-[10rem] text-zinc-400">{r.author}</span> : <span className="text-zinc-600">anonymous</span>}
          {lang && (
            <span className="rounded border border-zinc-700/70 px-1 py-0 font-mono text-[9px] uppercase text-zinc-500" title="Detected language">
              {lang}
            </span>
          )}
          {r.published_at && (
            <span className="text-zinc-600">{String(r.published_at).slice(0, 10)}</span>
          )}
          {intel && typeof intel.composite === "number" && (
            <span
              className={`font-mono text-[9px] ${isHigh ? "text-emerald-400" : "text-zinc-600"}`}
              title={`Review intelligence ${intel.composite.toFixed(2)} (${intel.label})${intel.specificity != null ? ` · specificity ${intel.specificity}, depth ${intel.depth}, actionability ${intel.actionability}, uniqueness ${intel.uniqueness}` : ""}`}
            >
              q{intel.composite.toFixed(2)}
            </span>
          )}
          {isHigh && (
            <span className="rounded bg-emerald-900/40 px-1.5 py-0 text-[9px] font-medium uppercase tracking-wider text-emerald-300">detailed</span>
          )}
          {credHigh && (
            <span
              className="inline-flex items-center gap-0.5 rounded border border-emerald-800 bg-emerald-900/40 px-1.5 py-0 text-[9px] font-medium text-emerald-400"
              title={`Reviewer credibility ${typeof cred.credibility === "number" ? cred.credibility.toFixed(2) : cred.credibility} — weighted by ownership, specificity & depth`}
            >
              🛡 verified depth
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {stars > 0 && <Stars n={stars} />}
          <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${tone.chip}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
            {cat}
          </span>
        </div>
      </div>

      {/* Main readable text */}
      <p className="text-sm leading-relaxed text-zinc-200 whitespace-pre-wrap break-words">
        {primaryText || <span className="italic text-zinc-600">(empty)</span>}
      </p>

      {/* Original (collapsed) when we showed a translation */}
      {secondaryText && (
        <div className="mt-1.5">
          <button
            onClick={() => setShowOriginal((v) => !v)}
            className="text-[11px] text-zinc-500 hover:text-zinc-300"
          >
            {showOriginal ? "▾ hide original" : "▸ show original"}
            <span className="ml-1 text-zinc-600">({lang})</span>
          </button>
          {showOriginal && (
            <p className="mt-1 border-l-2 border-zinc-700 pl-2 text-[12px] italic text-zinc-400 whitespace-pre-wrap break-words">
              {secondaryText}
            </p>
          )}
        </div>
      )}

      {/* Footer chips: emotion / sarcasm / intent / likes / source link */}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-zinc-800/70 pt-2 text-[10px]">
        {emotion && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-400" title="Detected emotion">
            {emotion}
            {typeof r.emotion_score === "number" && r.emotion_score >= 0.4 ? ` ${(r.emotion_score * 100).toFixed(0)}%` : ""}
          </span>
        )}
        {sarcastic && (
          <span className="rounded bg-violet-900/40 px-1.5 py-0.5 font-mono text-violet-200" title="Sarcasm detected">↝ sarcasm</span>
        )}
        {intent && (
          <span className="rounded bg-blue-900/30 px-1.5 py-0.5 font-mono text-blue-200" title="Buyer intent">{intent.toLowerCase()}</span>
        )}
        {typeof r.like_count === "number" && r.like_count > 0 && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-400" title="Likes / upvotes">♥ {r.like_count}</span>
        )}
        {r.canonical_reason && (
          <span className="text-zinc-500"><span className="text-zinc-600">theme:</span> {r.canonical_reason}</span>
        )}
        {link && (
          <a
            href={link}
            target="_blank"
            rel="noreferrer"
            className="ml-auto text-zinc-500 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-300"
          >
            view source ↗
          </a>
        )}
      </div>
    </div>
  );
}

export default function ReviewsBrowser({ reviews = [], productName = "" }) {
  const [activeCat, setActiveCat] = useState("All");
  const [activeLang, setActiveLang] = useState("All");
  const [sentFilter, setSentFilter] = useState("All"); // All | Positive | Negative
  const [sortBy, setSortBy] = useState("quality"); // quality | positive | negative | recent
  const [search, setSearch] = useState("");
  const [visible, setVisible] = useState(12);

  // Category counts for the filter chips
  const catCounts = useMemo(() => {
    const c = { All: reviews.length, Praise: 0, Complaint: 0, Suggestion: 0, Neutral: 0, Prediction: 0 };
    for (const r of reviews) {
      const k = r.review_category || "Neutral";
      c[k] = (c[k] || 0) + 1;
    }
    return c;
  }, [reviews]);

  // Distinct languages present
  const langs = useMemo(() => {
    const s = new Set();
    for (const r of reviews) {
      const l = (r.language || "").toUpperCase();
      if (l && l !== "UNKNOWN" && l !== "?") s.add(l);
    }
    return Array.from(s).sort();
  }, [reviews]);

  const filtered = useMemo(() => {
    let out = reviews.slice();

    if (activeCat !== "All") out = out.filter((r) => (r.review_category || "Neutral") === activeCat);
    if (activeLang !== "All") out = out.filter((r) => (r.language || "").toUpperCase() === activeLang);

    if (sentFilter === "Positive") out = out.filter((r) => (STAR_TO_NUM[r.sentiment] || 3) >= 4);
    if (sentFilter === "Negative") out = out.filter((r) => (STAR_TO_NUM[r.sentiment] || 3) <= 2);

    const q = search.trim().toLowerCase();
    if (q) {
      out = out.filter((r) => {
        const blob = `${r.original || ""} ${r.translated_text || ""} ${r.canonical_reason || ""}`.toLowerCase();
        return blob.includes(q);
      });
    }

    const starOf = (r) => STAR_TO_NUM[r.sentiment] || 3;
    if (sortBy === "quality") out.sort((a, b) => intelScore(b) - intelScore(a));
    else if (sortBy === "positive") out.sort((a, b) => starOf(b) - starOf(a) || intelScore(b) - intelScore(a));
    else if (sortBy === "negative") out.sort((a, b) => starOf(a) - starOf(b) || intelScore(b) - intelScore(a));
    else if (sortBy === "recent") out.sort((a, b) => String(b.published_at || "").localeCompare(String(a.published_at || "")));

    return out;
  }, [reviews, activeCat, activeLang, sentFilter, sortBy, search]);

  // Reset pagination when filters change
  const shown = filtered.slice(0, visible);

  if (!reviews.length) return null;

  const chip = (label, count, active, onClick, tone = "zinc") => {
    const toneCls = active
      ? (tone === "green" ? "border-emerald-700 bg-emerald-900/40 text-emerald-100"
        : tone === "red" ? "border-rose-700 bg-rose-900/40 text-rose-100"
        : tone === "violet" ? "border-violet-700 bg-violet-900/40 text-violet-100"
        : "border-blue-700 bg-blue-900/40 text-blue-100")
      : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800";
    return (
      <button
        key={label}
        onClick={onClick}
        className={`rounded-full border px-3 py-1 text-xs transition ${toneCls}`}
      >
        {label}
        {typeof count === "number" && <span className={`ml-1.5 font-mono text-[10px] ${active ? "opacity-80" : "text-zinc-500"}`}>{count}</span>}
      </button>
    );
  };

  return (
    <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 shadow-sm">
      {/* Header */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-medium text-zinc-100">
            All reviews <span className="text-zinc-500">({reviews.length})</span>
          </h3>
          <p className="mt-0.5 text-xs text-zinc-400">
            Read exactly what people said about {productName || "this product"} — originals + translations
          </p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="mb-3 space-y-2">
        {/* Category chips */}
        <div className="flex flex-wrap items-center gap-1.5">
          {chip("All", catCounts.All, activeCat === "All", () => { setActiveCat("All"); setVisible(12); })}
          {catCounts.Praise > 0 && chip("Praise", catCounts.Praise, activeCat === "Praise", () => { setActiveCat("Praise"); setVisible(12); }, "green")}
          {catCounts.Complaint > 0 && chip("Complaint", catCounts.Complaint, activeCat === "Complaint", () => { setActiveCat("Complaint"); setVisible(12); }, "red")}
          {catCounts.Suggestion > 0 && chip("Suggestion", catCounts.Suggestion, activeCat === "Suggestion", () => { setActiveCat("Suggestion"); setVisible(12); }, "violet")}
          {catCounts.Neutral > 0 && chip("Neutral", catCounts.Neutral, activeCat === "Neutral", () => { setActiveCat("Neutral"); setVisible(12); })}
        </div>

        {/* Search + selectors */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[180px] flex-1">
            <input
              value={search}
              onChange={(e) => { setSearch(e.target.value); setVisible(12); }}
              placeholder="Search inside reviews…"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 pr-8 text-xs text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-blue-600"
            />
            {search && (
              <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300">×</button>
            )}
          </div>

          <select
            value={sentFilter}
            onChange={(e) => { setSentFilter(e.target.value); setVisible(12); }}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200"
            title="Filter by sentiment"
          >
            <option value="All">All sentiment</option>
            <option value="Positive">Positive (4–5★)</option>
            <option value="Negative">Negative (1–2★)</option>
          </select>

          {langs.length > 1 && (
            <select
              value={activeLang}
              onChange={(e) => { setActiveLang(e.target.value); setVisible(12); }}
              className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200"
              title="Filter by language"
            >
              <option value="All">All languages</option>
              {langs.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          )}

          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200"
            title="Sort"
          >
            <option value="quality">Most useful</option>
            <option value="positive">Most positive</option>
            <option value="negative">Most critical</option>
            <option value="recent">Most recent</option>
          </select>
        </div>
      </div>

      {/* Results */}
      {filtered.length === 0 ? (
        <div className="py-10 text-center text-xs text-zinc-500">
          No reviews match these filters.
          <button onClick={() => { setActiveCat("All"); setActiveLang("All"); setSentFilter("All"); setSearch(""); }} className="ml-1 text-blue-400 hover:text-blue-300">Clear filters</button>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {shown.map((r, i) => <ReviewCard key={i} r={r} />)}
          </div>

          {visible < filtered.length && (
            <div className="mt-3 flex justify-center">
              <button
                onClick={() => setVisible((v) => v + 12)}
                className="rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2 text-xs text-zinc-300 hover:bg-zinc-800"
              >
                Show more ({filtered.length - visible} more)
              </button>
            </div>
          )}
          <div className="mt-2 text-center text-[10px] text-zinc-600">
            Showing {Math.min(visible, filtered.length)} of {filtered.length}{activeCat !== "All" || sentFilter !== "All" || activeLang !== "All" || search ? " filtered" : ""} reviews
          </div>
        </>
      )}
    </div>
  );
}
