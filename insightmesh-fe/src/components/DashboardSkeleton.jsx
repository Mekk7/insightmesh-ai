// insightmesh-fe/src/components/DashboardSkeleton.jsx
//
// Modern skeleton loader shown while a FRESH analysis is in flight.
//
// Why this exists:
//   When the user fires a new search (e.g. "PS5") while an old result (e.g.
//   "Sony WH-1000XM6") is still on screen, we must NOT keep showing the stale
//   dashboard underneath a spinner — that reads as broken. Instead we clear the
//   old result instantly and render this skeleton: placeholder cards shaped like
//   the real dashboard, with a gradient shimmer sweep (LinkedIn / YouTube style).
//
// Perceived-performance principle: showing the *shape* of what's coming makes
// the wait feel shorter than a lone spinner on an empty page, and it eliminates
// any chance of stale data being read as current.
//
// The optional `productName` + `elapsedHint` props let us show a tasteful
// "Analyzing PS5…" line so the user knows exactly what's cooking.

import React from "react";

// A single shimmering block. `className` controls size/shape via Tailwind.
function Bar({ className = "" }) {
  return <div className={`im-shimmer rounded-md bg-zinc-800/40 ${className}`} />;
}

function SkeletonCard({ children, className = "" }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-5 ${className}`}>
      {children}
    </div>
  );
}

export default function DashboardSkeleton({ productName = "", stage = "", platforms = [] }) {
  const niceStage = (() => {
    const s = (stage || "").toLowerCase();
    if (!s) return "Gathering reviews across platforms…";
    if (s.includes("mode")) return "Understanding your query…";
    if (s.includes("scrape") && s.includes("done")) return "Reviews collected — cleaning up…";
    if (s.includes("scrape")) return "Pulling reviews from YouTube & Reddit…";
    if (s.includes("clean") || s.includes("dedupe")) return "Removing noise & duplicates…";
    if (s.includes("analyze_started") || s === "analyze") return "Analyzing sentiment & themes…";
    if (s.includes("analyze_done")) return "Building your dashboard…";
    return "Working on it…";
  })();

  return (
    <div className="space-y-4" aria-busy="true" aria-live="polite">
      {/* Live status line — tells the user exactly what's loading */}
      <div className="flex items-center gap-3 rounded-2xl border border-blue-900/40 bg-blue-950/20 px-5 py-3.5">
        <span className="relative flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-500 opacity-60" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-500" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-blue-100">
            {productName ? <>Analyzing <span className="text-blue-300">{productName}</span>…</> : "Analyzing…"}
          </div>
          <div className="text-[11px] text-blue-300/70">{niceStage}</div>
        </div>
        {platforms.length > 0 && (
          <div className="hidden sm:flex items-center gap-1">
            {platforms.map((p) => (
              <span key={p} className="rounded-full border border-blue-800/40 bg-blue-950/40 px-2 py-0.5 text-[10px] uppercase tracking-wide text-blue-300/80">
                {p}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Hero banner placeholder */}
      <div className="relative overflow-hidden rounded-2xl border border-zinc-800/70 bg-gradient-to-br from-zinc-900/60 via-zinc-900/30 to-transparent">
        <div className="absolute top-0 left-0 right-0 h-1 im-shimmer bg-zinc-700/40" />
        <div className="flex items-center justify-between gap-4 p-5 pt-6">
          <div className="flex-1 space-y-2.5">
            <Bar className="h-2.5 w-24" />
            <Bar className="h-6 w-2/3" />
            <Bar className="h-3 w-1/2" />
          </div>
          <Bar className="h-9 w-40 rounded-full" />
        </div>
      </div>

      {/* Executive-summary card placeholder */}
      <SkeletonCard>
        <div className="flex items-start gap-3">
          <Bar className="mt-1 h-2 w-2 rounded-full" />
          <div className="flex-1 space-y-2.5">
            <Bar className="h-2.5 w-28" />
            <Bar className="h-5 w-3/5" />
            <Bar className="h-3 w-full" />
            <Bar className="h-3 w-11/12" />
            <div className="grid grid-cols-1 gap-1.5 pt-1 md:grid-cols-2">
              <Bar className="h-3 w-5/6" />
              <Bar className="h-3 w-4/6" />
            </div>
          </div>
        </div>
      </SkeletonCard>

      {/* Green "comments analyzed" strip placeholder */}
      <div className="overflow-hidden rounded-2xl border border-zinc-800/70 bg-zinc-900/40">
        <div className="flex items-center justify-between gap-3 px-5 py-3">
          <Bar className="h-3.5 w-64" />
          <div className="hidden sm:flex gap-1">
            <Bar className="h-4 w-7" />
            <Bar className="h-4 w-7" />
            <Bar className="h-4 w-7" />
          </div>
        </div>
      </div>

      {/* 4 stat tiles */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3.5">
            <Bar className="h-2.5 w-20" />
            <Bar className="mt-2 h-7 w-16" />
            <Bar className="mt-2 h-2.5 w-24" />
          </div>
        ))}
      </div>

      {/* TrustScore placeholder (ring + verdict) */}
      <SkeletonCard>
        <div className="flex items-start gap-4">
          <Bar className="h-20 w-20 shrink-0 rounded-2xl" />
          <div className="flex-1 space-y-2.5 pt-1">
            <Bar className="h-2.5 w-40" />
            <Bar className="h-4 w-3/4" />
            <Bar className="h-1.5 w-full max-w-md rounded-full" />
          </div>
        </div>
      </SkeletonCard>

      {/* Two chart-shaped cards side by side */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <SkeletonCard key={i}>
            <Bar className="h-3 w-40" />
            <Bar className="mt-2 h-2.5 w-28" />
            <div className="mt-4 flex h-40 items-end gap-2">
              {/* Fake bar-chart columns of varying heights */}
              {[60, 85, 45, 70, 95, 55, 75, 40].map((h, j) => (
                <div key={j} className="flex-1">
                  <div className="im-shimmer rounded-t bg-zinc-800/40" style={{ height: `${h}%` }} />
                </div>
              ))}
            </div>
          </SkeletonCard>
        ))}
      </div>

      {/* A wide list-shaped card (e.g. complaints / aspect breakdown) */}
      <SkeletonCard>
        <Bar className="h-3 w-48" />
        <Bar className="mt-2 h-2.5 w-32" />
        <div className="mt-4 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <Bar className="h-3.5 w-1/3" />
                <Bar className="h-3 w-12" />
              </div>
              <Bar className="h-1.5 w-full rounded-full" />
              <Bar className="mt-2 h-3 w-4/5" />
            </div>
          ))}
        </div>
      </SkeletonCard>
    </div>
  );
}
