import React, { useMemo, useState } from "react";
import API_BASE from "../api.js";

function classNames(...xs) {
  return xs.filter(Boolean).join(" ");
}
function toPct(n) {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n * 100)}%`;
}
function formatMs(ms) {
  if (ms === undefined || ms === null) return "—";
  const s = ms / 1000;
  return s >= 60 ? `${(s / 60).toFixed(1)}m` : `${s.toFixed(1)}s`;
}

const CATEGORY_COLORS = {
  Praise: "bg-emerald-600 text-white",
  Complaint: "bg-rose-600 text-white",
  Suggestion: "bg-amber-500 text-black",
  Prediction: "bg-indigo-500 text-white",
  Neutral: "bg-slate-400 text-black",
};

export default function InsightsDashboard({
  apiBase = import.meta.env.VITE_API_BASE_URL || `${API_BASE}/api`,
  defaultQuery = "Tesla Model Y",
}) {
  const [query, setQuery] = useState(defaultQuery);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null); // final_report

  async function run() {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await fetch(`${apiBase}/insightmesh/run_pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filepath: null,
          query_override: query,
          platforms: ["youtube", "reddit"],
          mode: "fast",
          debug: true,
        }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(`HTTP ${res.status}: ${t}`);
      }
      const json = await res.json();
      setData(json.final_report);
    } catch (e) {
      setError(e?.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  const overview = data?.analysis?.overview;
  const totalsByCat = data?.analysis?.executive_summary?.totals_by_category || {};
  const topAspects = data?.analysis?.executive_summary?.top_aspects || [];
  const topReasonsOverall = data?.analysis?.executive_summary?.top_reasons_overall || {};
  const actionItems = data?.analysis?.action_items || [];

  const totalReviews = useMemo(() => {
    const stars = overview?.stars ?? {};
    const starTotals = Object.values(stars).reduce((a, v) => a + (Number(v) || 0), 0);
    if (starTotals) return starTotals;
    const mentionsSum = (topAspects || []).map((a) => a?.mentions || 0).reduce((a, b) => a + b, 0);
    return mentionsSum ? `~${mentionsSum}` : null;
  }, [overview, topAspects]);

  const mood = overview?.mood_index ?? null;
  const avgSent = overview?.average_sentiment ?? null;

  return (
    <div className="w-full max-w-6xl mx-auto p-4 space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Executive Insights</h1>
          <p className="text-sm text-zinc-400">
            Query: <span className="font-medium text-zinc-200">{data?.meta?.query_used ?? query}</span>{" "}
            {data?.meta?.elapsed_ms !== undefined && (
              <span className="ml-2 text-zinc-500">({formatMs(data.meta.elapsed_ms)})</span>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search term or product (e.g., Tesla Model Y)"
            className="border border-zinc-700 bg-zinc-900 text-zinc-100 rounded-lg px-3 py-2 w-72"
          />
          <button
            onClick={run}
            disabled={loading || !query.trim()}
            className={classNames(
              "px-4 py-2 rounded-lg font-medium",
              loading ? "bg-zinc-700 text-zinc-300" : "bg-blue-600 text-white hover:bg-blue-500"
            )}
          >
            {loading ? "Analyzing…" : "Run pipeline"}
          </button>
        </div>
      </div>

      {/* Errors */}
      {error && (
        <div className="bg-red-950/50 border border-red-800 text-red-200 rounded-lg p-3">{error}</div>
      )}

      {/* KPIs */}
      {data && (
        <div className="grid md:grid-cols-4 gap-3">
          <KpiCard title="Mood Index" subtitle="-1 to +1">
            <MoodBar value={mood} />
          </KpiCard>
          <KpiCard title="Avg Sentiment">
            <div className="text-2xl font-semibold">{avgSent ?? "—"}</div>
          </KpiCard>
          <KpiCard title="Total Reviews (approx)">
            <div className="text-2xl font-semibold">{totalReviews ?? "—"}</div>
          </KpiCard>
          <KpiCard title="Top Keyphrases">
            <div className="text-sm text-zinc-300">
              {overview?.top_keyphrases?.join(", ") || "—"}
            </div>
          </KpiCard>
        </div>
      )}

      {/* Stars & Category totals */}
      {data && (
        <div className="grid md:grid-cols-2 gap-4">
          <Card title="Star Distribution">
            <StarBars stars={overview?.stars || {}} />
          </Card>
          <Card title="Totals by Category">
            <div className="flex flex-wrap gap-2">
              {Object.entries(totalsByCat).length ? (
                Object.entries(totalsByCat).map(([k, v]) => (
                  <span
                    key={k}
                    className={classNames(
                      "px-3 py-1 rounded-full text-sm",
                      CATEGORY_COLORS[k] || "bg-zinc-800 text-zinc-200"
                    )}
                  >
                    {k}: <b>{String(v)}</b>
                  </span>
                ))
              ) : (
                <div className="text-zinc-400 text-sm">No category totals.</div>
              )}
            </div>
          </Card>
        </div>
      )}

      {/* Top Reasons Overall */}
      {data && Object.keys(topReasonsOverall).length > 0 && (
        <Card title="Top Reasons Overall">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {Object.entries(topReasonsOverall).map(([cat, reasons]) => (
              <div key={cat} className="rounded-xl border border-zinc-800 p-3">
                <span className={classNames("px-2 py-0.5 rounded text-xs font-medium", CATEGORY_COLORS[cat] || "bg-zinc-800 text-zinc-200")}>
                  {cat}
                </span>
                <ul className="mt-2 ml-4 list-disc text-sm space-y-1">
                  {(reasons || []).slice(0, 5).map((r, i) => <li key={i}>{String(r)}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Top Aspects */}
      {data && (
        <Card title="Top Aspects & Reasons">
          {topAspects.length === 0 ? (
            <div className="text-sm text-zinc-400">No aspect summaries available.</div>
          ) : (
            <div className="space-y-4">
              {topAspects.map((a) => (
                <AspectBlock key={a.aspect} aspect={a} />
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Action Items */}
      {data && (
        <Card title="Action Items (What to do next)">
          {actionItems.length === 0 ? (
            <div className="text-sm text-zinc-400">No action items returned.</div>
          ) : (
            <div className="space-y-3">
              {actionItems.map((it, i) => (
                <div key={i} className="border border-zinc-800 rounded-lg p-3">
                  <div className="font-semibold text-zinc-100">{it.theme}</div>
                  {it.why && <div className="text-sm text-zinc-400 mt-1">Why: {it.why}</div>}
                  <ul className="list-disc ml-5 mt-2 space-y-1">
                    {(it.suggestions || []).map((s, j) => (
                      <li key={j} className="text-sm text-zinc-200">{s}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Empty state */}
      {!data && !loading && !error && (
        <div className="text-center text-zinc-400 py-12">
          Enter a query and click <b>Run pipeline</b> to see insights.
        </div>
      )}
    </div>
  );
}

/** ---------- UI bits ---------- **/

function KpiCard({ title, subtitle, children }) {
  return (
    <div className="border border-zinc-800/80 rounded-xl p-3 bg-zinc-900/60">
      <div className="text-xs uppercase tracking-wide text-zinc-400">{title}</div>
      {subtitle && <div className="text-[11px] text-zinc-500">{subtitle}</div>}
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Card({ title, children }) {
  return (
    <div className="border border-zinc-800/80 rounded-xl p-4 bg-zinc-900/60">
      <div className="text-sm font-semibold mb-2 text-zinc-100">{title}</div>
      {children}
    </div>
  );
}

function MoodBar({ value }) {
  const pct = value === null ? 50 : Math.round(((value + 1) / 2) * 100); // -1..+1 -> 0..100
  return (
    <div>
      <div className="text-sm mb-1 text-zinc-200">{value === null ? "—" : Number(value).toFixed(3)}</div>
      <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
        <div className="h-full bg-gradient-to-r from-rose-500 via-amber-500 to-emerald-600" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function StarBars({ stars }) {
  const get = (n) => Number(stars?.[`${n} star`] ?? stars?.[`${n} stars`] ?? 0); // <-- cast to Number
  const total = [1,2,3,4,5].reduce((a,n)=>a+get(n),0);
  if (!total) return <div className="text-sm text-zinc-400">No star data.</div>;
  return (
    <div className="space-y-1">
      {[1,2,3,4,5].map((n) => {
        const c = get(n);
        const pct = Math.round((c / total) * 100);
        return (
          <div key={n} className="flex items-center gap-2">
            <div className="w-20 text-xs text-zinc-400">{n} star{n>1?"s":""}</div>
            <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-zinc-300" style={{ width: `${pct}%` }} />
            </div>
            <div className="w-10 text-right text-xs text-zinc-400">{pct}%</div>
          </div>
        );
      })}
    </div>
  );
}

function AspectBlock({ aspect }) {
  const total = aspect?.mentions || 0;
  return (
    <div className="border border-zinc-800 rounded-lg p-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-base font-semibold text-zinc-100">{aspect?.aspect}</div>
        <div className="text-sm text-zinc-400">
          Mentions: <b className="text-zinc-200">{total}</b> · Share: <b className="text-zinc-200">{toPct(aspect?.share_of_reviews)}</b>
        </div>
      </div>
      <div className="mt-2 grid md:grid-cols-2 gap-3">
        {(aspect?.by_category || []).map((c) => (
          <div key={c.category} className="border border-zinc-800 rounded-md p-2">
            <div className="flex items-center justify-between">
              <div className={classNames("px-2 py-0.5 rounded text-xs font-semibold", CATEGORY_COLORS[c.category] || "bg-zinc-800 text-zinc-200")}>
                {c.category}
              </div>
              <div className="text-xs text-zinc-400">Count: {c.count}</div>
            </div>
            {c.reasons?.length > 0 && (
              <div className="mt-2">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Reasons</div>
                <ul className="list-disc ml-5 text-sm space-y-1">
                  {c.reasons.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            )}
            {c.quotes?.length > 0 && (
              <div className="mt-2">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Representative quotes</div>
                <div className="space-y-2">
                  {c.quotes.slice(0, 2).map((q, i) => (
                    <blockquote key={i} className="text-sm text-zinc-200 bg-zinc-900/40 border border-zinc-800 rounded p-2">
                      “{q.quote}”
                      <div className="text-[11px] text-zinc-400 mt-1">
                        {q.sentiment ?? ""} {typeof q.sentiment_score === "number" ? `· score ${q.sentiment_score}` : ""}
                        {q.keyphrases?.length ? ` · ${q.keyphrases.slice(0, 3).join(", ")}` : ""}
                      </div>
                    </blockquote>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
