// src/components/ComparePanel.jsx
// Side-by-side comparison of two pipeline runs.
import React, { useEffect, useMemo, useState } from "react";
import { fetchHistory, fetchRun } from "../lib/api.js";

// ---------- atoms ----------
function Card({ title, children, right, className = "" }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 shadow-sm ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-base font-semibold text-zinc-100">{title}</h3>
        <div className="flex items-center gap-2">{right}</div>
      </div>
      <div className="text-sm leading-relaxed text-zinc-300">{children}</div>
    </div>
  );
}
function Badge({ children, tone = "zinc" }) {
  const tones = {
    zinc: "bg-zinc-800 text-zinc-200 border-zinc-700",
    green: "bg-green-900/30 text-green-200 border-green-800",
    red: "bg-red-900/30 text-red-200 border-red-800",
    amber: "bg-amber-900/30 text-amber-200 border-amber-800",
    blue: "bg-blue-900/30 text-blue-200 border-blue-800",
    indigo: "bg-indigo-900/30 text-indigo-200 border-indigo-800",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>;
}

const fmt = (d) => (d ? new Date(d).toLocaleString() : "—");
const moodTone = (m) => (typeof m !== "number" ? "zinc" : m > 0.1 ? "green" : m < -0.1 ? "red" : "amber");
const delta = (a, b) => {
  if (typeof a !== "number" || typeof b !== "number") return null;
  const d = b - a;
  const sign = d > 0 ? "+" : "";
  return `${sign}${d.toFixed(3)}`;
};

// ---------- One side of the comparison ----------
function RunSide({ run, title }) {
  if (!run) return <Card title={title}><div className="text-zinc-500">No run selected.</div></Card>;
  const report = run.report || {};
  const meta = report.meta || {};
  const overview = report.analysis?.overview || {};
  const exec = report.analysis?.executive_summary || {};
  const clusters = (overview.canonical_clusters || []).slice(0, 5);
  return (
    <Card
      title={title}
      right={<Badge tone={run.user_mode === "consumer" ? "blue" : "indigo"}>{run.user_mode}</Badge>}
    >
      <div className="mb-3">
        <div className="text-xs text-zinc-400">{fmt(run.created_at)}</div>
        <div className="mt-1 text-lg font-semibold text-zinc-100">{meta.query_used || run.query || "(no query)"}</div>
        <div className="mt-1 flex flex-wrap gap-1 text-xs">
          {(run.platforms || []).map((p) => <Badge key={p}>{p}</Badge>)}
          {meta.strictness && <Badge tone="indigo">strict: {meta.strictness}</Badge>}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-2">
          <div className="text-xs text-zinc-400">Mood</div>
          <Badge tone={moodTone(overview.mood_index)}>
            {typeof overview.mood_index === "number" ? overview.mood_index.toFixed(3) : "—"}
          </Badge>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-2">
          <div className="text-xs text-zinc-400">Avg sentiment</div>
          <div className="text-base font-semibold">
            {typeof overview.average_sentiment === "number" ? overview.average_sentiment.toFixed(2) : "—"}
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-2">
          <div className="text-xs text-zinc-400">Reviews</div>
          <div className="text-base font-semibold">{run.n_analyzed ?? "—"}</div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-2">
          <div className="text-xs text-zinc-400">Clusters</div>
          <div className="text-base font-semibold">{(overview.canonical_clusters || []).length}</div>
        </div>
      </div>

      {/* Category totals */}
      {Object.keys(exec.totals_by_category || {}).length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs text-zinc-400">Categories</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(exec.totals_by_category).map(([k, v]) => (
              <Badge key={k}>{k}: {v}</Badge>
            ))}
          </div>
        </div>
      )}

      {/* Top clusters */}
      <div className="mt-3">
        <div className="mb-1 text-xs text-zinc-400">Top clusters</div>
        {clusters.length === 0 && <div className="text-xs text-zinc-500">—</div>}
        <div className="space-y-1.5">
          {clusters.map((c) => (
            <div key={c.cluster_id} className="rounded-md border border-zinc-800 bg-zinc-900/40 p-2 text-xs">
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium text-zinc-100">{c.reason}</div>
                <Badge tone="blue">{c["share_%"]}%</Badge>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ---------- Diff strip across the top ----------
function DiffStrip({ a, b }) {
  if (!a || !b) return null;
  const oa = a.report?.analysis?.overview || {};
  const ob = b.report?.analysis?.overview || {};
  return (
    <Card title="Δ Differences" subtitle="Run B vs Run A (positive = B is higher)">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Diff label="Mood index" a={oa.mood_index} b={ob.mood_index} sigFig={3} />
        <Diff label="Avg sentiment" a={oa.average_sentiment} b={ob.average_sentiment} sigFig={2} />
        <Diff label="Reviews analyzed" a={a.n_analyzed} b={b.n_analyzed} sigFig={0} />
        <Diff label="Elapsed (ms)" a={a.elapsed_ms} b={b.elapsed_ms} sigFig={0} />
      </div>

      {/* Shared cluster themes */}
      <SharedThemes a={a} b={b} />
    </Card>
  );
}

function Diff({ label, a, b, sigFig = 2 }) {
  const d = (typeof a === "number" && typeof b === "number") ? b - a : null;
  const tone = d == null ? "zinc" : d > 0 ? "green" : d < 0 ? "red" : "zinc";
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <div className="text-base text-zinc-100">
          {typeof a === "number" ? a.toFixed(sigFig) : "—"} → {typeof b === "number" ? b.toFixed(sigFig) : "—"}
        </div>
      </div>
      <div className="mt-1">
        <Badge tone={tone}>{d == null ? "—" : `${d >= 0 ? "+" : ""}${d.toFixed(sigFig)}`}</Badge>
      </div>
    </div>
  );
}

function SharedThemes({ a, b }) {
  const ca = (a.report?.analysis?.overview?.canonical_clusters || []).map((c) => (c.reason || "").toLowerCase());
  const cb = (b.report?.analysis?.overview?.canonical_clusters || []).map((c) => (c.reason || "").toLowerCase());
  const aOnly = ca.filter((x) => !cb.some((y) => y === x || (x.length > 5 && y.includes(x))));
  const bOnly = cb.filter((x) => !ca.some((y) => y === x || (x.length > 5 && y.includes(x))));
  const shared = ca.filter((x) => cb.some((y) => y === x || (x.length > 5 && y.includes(x))));
  if (!ca.length && !cb.length) return null;
  return (
    <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
      <Section title="Only in A" items={aOnly} tone="red" />
      <Section title="Shared" items={shared} tone="green" />
      <Section title="Only in B" items={bOnly} tone="blue" />
    </div>
  );
}

function Section({ title, items, tone }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="mb-1 flex items-center justify-between">
        <div className="text-xs text-zinc-400">{title}</div>
        <Badge tone={tone}>{items.length}</Badge>
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-zinc-500">—</div>
      ) : (
        <ul className="ml-4 list-disc text-xs space-y-1 text-zinc-200">
          {items.slice(0, 6).map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      )}
    </div>
  );
}

// ---------- Main panel ----------
export default function ComparePanel({ initialIds }) {
  const [recentRuns, setRecentRuns] = useState([]);
  const [aId, setAId] = useState(initialIds?.[0] || "");
  const [bId, setBId] = useState(initialIds?.[1] || "");
  const [runA, setRunA] = useState(null);
  const [runB, setRunB] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  // Load list of recent runs for dropdowns
  useEffect(() => {
    (async () => {
      try {
        const d = await fetchHistory({ limit: 100 });
        setRecentRuns(d.items || []);
      } catch (e) {
        setErr(e?.message || String(e));
      }
    })();
  }, []);

  // Auto-load when ids change
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!aId && !bId) return;
      setLoading(true);
      setErr(null);
      try {
        const [a, b] = await Promise.all([
          aId ? fetchRun(aId) : Promise.resolve(null),
          bId ? fetchRun(bId) : Promise.resolve(null),
        ]);
        if (cancelled) return;
        setRunA(a);
        setRunB(b);
      } catch (e) {
        if (!cancelled) setErr(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [aId, bId]);

  // Set initial selection when initialIds prop changes
  useEffect(() => {
    if (initialIds && initialIds.length >= 1) setAId(String(initialIds[0]));
    if (initialIds && initialIds.length >= 2) setBId(String(initialIds[1]));
  }, [initialIds]);

  return (
    <div className="space-y-4">
      <Card title="Pick two runs to compare" subtitle="Run A vs Run B — see side-by-side metrics and diff">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <RunPicker label="Run A" value={aId} options={recentRuns} onChange={setAId} excludeId={bId} />
          <RunPicker label="Run B" value={bId} options={recentRuns} onChange={setBId} excludeId={aId} />
        </div>
        {err && <div className="mt-2 rounded-lg border border-red-800 bg-red-950/40 p-2 text-xs text-red-200">{err}</div>}
        {loading && <div className="mt-2 text-xs text-zinc-400">Loading…</div>}
      </Card>

      {runA && runB && <DiffStrip a={runA} b={runB} />}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <RunSide run={runA} title="Run A" />
        <RunSide run={runB} title="Run B" />
      </div>
    </div>
  );
}

function RunPicker({ label, value, options, onChange, excludeId }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-zinc-400">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
      >
        <option value="">— pick a run —</option>
        {options
          .filter((r) => String(r.id) !== String(excludeId))
          .map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id} — {r.query || r.filepath || "(no query)"} — {new Date(r.created_at).toLocaleDateString()}
            </option>
          ))}
      </select>
    </label>
  );
}
