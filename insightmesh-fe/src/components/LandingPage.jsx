import React, { useState } from "react";
import { FEATURED } from "../featured.js";

// ============================================================
// Landing page — the first thing a recruiter sees.
// Clean, dark, professional. Hero + 3 feature highlights +
// featured products grid + free-text search.
// ============================================================

// Edit this once with your real name.
const AUTHOR = "Vamshi Konyala";

function FeatureIcon({ name }) {
  const common = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" };
  if (name === "confidence") {
    // shield-check
    return (
      <svg {...common}><path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" /><path d="M9 12l2 2 4-4" /></svg>
    );
  }
  if (name === "competitive") {
    // bar-chart / versus
    return (
      <svg {...common}><path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" /></svg>
    );
  }
  // advisor — sparkle / lightbulb
  return (
    <svg {...common}><path d="M9 18h6" /><path d="M10 22h4" /><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1h6c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z" /></svg>
  );
}

const FEATURES = [
  { icon: "confidence",  title: "Confidence-scored insights", desc: "Every claim is backed by evidence and a confidence score — no hand-wavy summaries." },
  { icon: "competitive", title: "Competitive intelligence",   desc: "See how a product stacks up against rivals on the aspects buyers actually mention." },
  { icon: "advisor",     title: "Purchase advisor",           desc: "A clear buy / wait / skip verdict synthesized from thousands of real reviews." },
];

export default function LandingPage({ onLaunch }) {
  const [q, setQ] = useState("");

  const submit = (e) => {
    e?.preventDefault?.();
    const query = q.trim();
    if (!query) return;
    onLaunch({ query });
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* subtle gradient backdrop */}
      <div className="pointer-events-none fixed inset-0 -z-10 bg-[radial-gradient(60rem_40rem_at_50%_-10%,rgba(59,130,246,0.12),transparent),radial-gradient(50rem_30rem_at_90%_10%,rgba(139,92,246,0.10),transparent)]" />

      <div className="mx-auto max-w-5xl px-6 py-16 md:py-24">
        {/* Hero */}
        <header className="text-center">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900/60 px-3 py-1 text-xs text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            AI-powered review intelligence
          </div>
          <h1 className="text-4xl font-bold tracking-tight md:text-6xl">
            InsightMesh&nbsp;AI
            <span className="block bg-gradient-to-r from-blue-400 via-violet-400 to-blue-400 bg-clip-text text-transparent">
              Product Review Intelligence
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-base text-zinc-400 md:text-lg">
            Search any product. Get AI-powered intelligence in 90 seconds.
          </p>

          {/* Search */}
          <form onSubmit={submit} className="mx-auto mt-8 flex max-w-xl flex-col gap-2 sm:flex-row">
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search any product — e.g. AirPods Pro, Steam Deck, Galaxy S24…"
              className="w-full rounded-xl border border-zinc-700 bg-zinc-900/80 px-4 py-3 text-sm outline-none transition focus:border-blue-600 focus:ring-2 focus:ring-blue-700/40"
            />
            <button
              type="submit"
              className="shrink-0 rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
              disabled={!q.trim()}
            >
              Analyze →
            </button>
          </form>
          <div className="mt-2 text-xs text-zinc-600">Runs on Balanced mode · YouTube + Reddit · ~90s</div>
        </header>

        {/* Feature highlights */}
        <section className="mt-16 grid grid-cols-1 gap-4 md:grid-cols-3">
          {FEATURES.map((f) => (
            <div key={f.title} className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-5">
              <div className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl border border-blue-900/50 bg-blue-950/40 text-blue-300">
                <FeatureIcon name={f.icon} />
              </div>
              <div className="text-sm font-semibold text-zinc-100">{f.title}</div>
              <div className="mt-1 text-xs leading-relaxed text-zinc-400">{f.desc}</div>
            </div>
          ))}
        </section>

        {/* Featured products */}
        <section className="mt-16">
          <div className="mb-4 flex items-end justify-between">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Featured products</h2>
              <p className="text-xs text-zinc-500">Pre-analyzed — one click loads the full dashboard instantly.</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {FEATURED.map((p) => (
              <button
                key={p.label}
                onClick={() => onLaunch({ query: p.query, slug: p.slug })}
                className="group flex flex-col items-start gap-2 rounded-2xl border border-zinc-800/80 bg-zinc-900/50 p-4 text-left transition hover:border-blue-700/70 hover:bg-zinc-900"
              >
                <div className="text-2xl">{p.emoji}</div>
                <div className="text-sm font-semibold text-zinc-100">{p.label}</div>
                <div className="text-[11px] text-zinc-500">{p.tagline}</div>
                <div className="mt-1 inline-flex items-center gap-1 text-[10px] font-medium">
                  {p.slug ? (
                    <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-emerald-300 ring-1 ring-emerald-800/60">⚡ instant</span>
                  ) : (
                    <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-400 ring-1 ring-zinc-700">live ~90s</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Footer */}
        <footer className="mt-20 border-t border-zinc-900 pt-6 text-center text-xs text-zinc-500">
          Built by <span className="text-zinc-300">{AUTHOR}</span> — AI/ML Engineer
        </footer>
      </div>
    </div>
  );
}
