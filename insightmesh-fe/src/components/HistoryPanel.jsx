// src/components/HistoryPanel.jsx
// Browse, view, export, delete, and re-run past pipeline runs.
import React, { useEffect, useMemo, useState } from "react";
import {
  fetchHistory,
  fetchRun,
  deleteRun,
  searchRuns,
  fetchStats,
  exportUrlMd,
  exportUrlHtml,
  exportReportMd,
  exportReportHtml,
  downloadBlob,
  api,
} from "../lib/api.js";

// ---------- UI atoms (light copies of App.jsx primitives) ----------
function Card({ title, right, subtitle, children, className = "" }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 shadow-sm ${className}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-zinc-100">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
        </div>
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
function Pill({ children, onClick, danger = false, small = false }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md border px-2 py-1 text-xs ${
        small ? "text-[11px]" : ""
      } ${
        danger
          ? "border-rose-800 bg-rose-900/30 text-rose-200 hover:bg-rose-900/50"
          : "border-zinc-700 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
      }`}
    >
      {children}
    </button>
  );
}

const fmt = (d) => (d ? new Date(d).toLocaleString() : "—");
const fmtMs = (ms) => (typeof ms === "number" ? (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`) : "—");

// ---------- Run detail modal ----------
function RunDetailDialog({ run, onClose, onRerun }) {
  if (!run) return null;
  const report = run.report || {};
  const meta = report.meta || {};
  const overview = report.analysis?.overview || {};
  const clusters = overview.canonical_clusters || [];

  const exportLive = async (fmt) => {
    try {
      const blob = fmt === "md"
        ? await exportReportMd(report)
        : await exportReportHtml(report);
      downloadBlob(blob, `insightmesh_run_${run.id}.${fmt}`);
    } catch (e) {
      console.error(e);
      alert(`Export failed: ${e.message || e}`);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-5xl overflow-auto rounded-2xl border border-zinc-700 bg-zinc-900 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-zinc-100">
              Run #{run.id} — {meta.query_used || run.query || "(no query)"}
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              {fmt(run.created_at)} · {fmtMs(meta.elapsed_ms)} · mode {meta.user_mode || run.user_mode}
              {meta.from_cache ? " · (cached)" : ""}
            </p>
          </div>
          <button className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-sm hover:bg-zinc-700" onClick={onClose}>
            Close
          </button>
        </div>

        {/* Top actions */}
        <div className="mb-4 flex flex-wrap gap-2">
          <Pill onClick={() => onRerun?.(run)}>↻ Re-run with same params</Pill>
          <Pill onClick={() => exportLive("md")}>⬇ Markdown</Pill>
          <Pill onClick={() => exportLive("html")}>⬇ HTML (print → PDF)</Pill>
          <a
            href={`${api.defaults.baseURL}/insightmesh/export/run/${run.id}.html`}
            target="_blank"
            rel="noreferrer"
            className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
          >
            ⧉ Open in new tab
          </a>
        </div>

        {/* Quick stats */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Mood index" value={overview.mood_index?.toFixed?.(3) ?? "—"} />
          <Stat label="Avg sentiment" value={overview.average_sentiment?.toFixed?.(2) ?? "—"} />
          <Stat label="Reviews analyzed" value={run.n_analyzed ?? "—"} />
          <Stat label="Clusters" value={clusters.length} />
        </div>

        {/* Clusters */}
        {clusters.length > 0 && (
          <div className="mt-5">
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">Top clusters</h3>
            <div className="space-y-2">
              {clusters.slice(0, 8).map((c) => (
                <div key={c.cluster_id} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="font-medium text-zinc-100">{c.reason}</div>
                    <div className="flex shrink-0 gap-1">
                      <Badge tone="blue">{c["share_%"]}%</Badge>
                      <Badge>{c.count}</Badge>
                    </div>
                  </div>
                  {c.solution?.bullets?.length > 0 && (
                    <ul className="ml-4 mt-2 list-disc text-xs space-y-1 text-zinc-300">
                      {c.solution.bullets.slice(0, 2).map((b, i) => <li key={i}>{b}</li>)}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Raw JSON (collapsible) */}
        <details className="mt-5">
          <summary className="cursor-pointer text-xs text-zinc-400 hover:text-zinc-200">
            Show raw final_report JSON
          </summary>
          <pre className="mt-2 max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-300">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 text-lg font-semibold text-zinc-100">{value}</div>
    </div>
  );
}

// ---------- Main panel ----------
export default function HistoryPanel({ onRerun, onCompareSelect }) {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [needle, setNeedle] = useState("");
  const [userMode, setUserMode] = useState("");        // "" | "consumer" | "company"
  const [onlySuccessful, setOnlySuccessful] = useState(false);
  const [openRun, setOpenRun] = useState(null);
  const [selected, setSelected] = useState(new Set()); // for compare

  const refresh = async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = needle.trim()
        ? await searchRuns(needle.trim(), 50)
        : await fetchHistory({
            limit: 50,
            userMode: userMode || null,
            onlySuccessful,
          });
      setItems(data.items || []);
      try { setStats(await fetchStats()); } catch (e) { /* non-fatal */ }
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [userMode, onlySuccessful]);

  const onSearch = (e) => { e.preventDefault?.(); refresh(); };

  const openDetail = async (id) => {
    try {
      const full = await fetchRun(id);
      setOpenRun(full);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    }
  };

  const remove = async (id) => {
    if (!confirm(`Delete run #${id}?`)) return;
    try {
      await deleteRun(id);
      setItems((xs) => xs.filter((x) => x.id !== id));
      setSelected((s) => { const next = new Set(s); next.delete(id); return next; });
    } catch (e) {
      alert(`Delete failed: ${e.message || e}`);
    }
  };

  const toggleSelect = (id) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else if (next.size < 2) next.add(id);
      return next;
    });
  };

  const compareNow = () => {
    if (selected.size !== 2) {
      alert("Pick exactly 2 runs to compare.");
      return;
    }
    onCompareSelect?.(Array.from(selected));
  };

  return (
    <div className="space-y-4">
      {/* Stats strip */}
      {stats && (
        <Card title="Overview" subtitle="Aggregate run statistics">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Total runs" value={stats.total_runs ?? 0} />
            <Stat label="Successful" value={stats.successful ?? 0} />
            <Stat label="Avg mood" value={stats.avg_mood_index?.toFixed?.(2) ?? "—"} />
            <Stat label="Avg duration" value={fmtMs(stats.avg_elapsed_ms)} />
          </div>
        </Card>
      )}

      {/* Filters / search */}
      <Card title="Past runs" subtitle="Click any row to inspect, export, or re-run">
        <form onSubmit={onSearch} className="mb-3 flex flex-wrap items-center gap-2">
          <input
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
            placeholder="Search by query or filepath…"
            className="flex-1 min-w-[200px] rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-700"
          />
          <select
            value={userMode}
            onChange={(e) => setUserMode(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-xs"
          >
            <option value="">all modes</option>
            <option value="consumer">consumer</option>
            <option value="company">company</option>
          </select>
          <label className="flex items-center gap-1 text-xs text-zinc-300">
            <input
              type="checkbox"
              className="accent-blue-600"
              checked={onlySuccessful}
              onChange={(e) => setOnlySuccessful(e.target.checked)}
            />
            successful only
          </label>
          <Pill onClick={onSearch}>{loading ? "…" : "Search"}</Pill>
          <Pill onClick={() => { setNeedle(""); setUserMode(""); setOnlySuccessful(false); refresh(); }}>Reset</Pill>
        </form>

        {/* Selection bar */}
        {selected.size > 0 && (
          <div className="mb-2 flex items-center justify-between rounded-lg border border-indigo-800/60 bg-indigo-900/20 px-3 py-2 text-xs">
            <span className="text-indigo-200">
              {selected.size} selected{selected.size === 2 ? " — ready to compare" : " (pick 1 more)"}
            </span>
            <div className="flex gap-2">
              <Pill onClick={compareNow}>Compare →</Pill>
              <Pill onClick={() => setSelected(new Set())}>Clear</Pill>
            </div>
          </div>
        )}

        {/* Table */}
        <div className="overflow-auto rounded-xl border border-zinc-800">
          <table className="min-w-full text-left text-sm">
            <thead className="sticky top-0 bg-zinc-900/90 text-xs text-zinc-400 backdrop-blur">
              <tr>
                <th className="px-2 py-2 w-8"></th>
                <th className="px-2 py-2 w-12">ID</th>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Query / file</th>
                <th className="px-3 py-2">Mode</th>
                <th className="px-3 py-2">Kept</th>
                <th className="px-3 py-2">Mood</th>
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2 w-32">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr
                  key={r.id}
                  className={`border-t border-zinc-800 ${selected.has(r.id) ? "bg-indigo-900/20" : "hover:bg-zinc-900/60"}`}
                >
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      className="accent-indigo-500"
                      checked={selected.has(r.id)}
                      onChange={() => toggleSelect(r.id)}
                    />
                  </td>
                  <td className="px-2 py-2 font-mono text-xs text-zinc-400">{r.id}</td>
                  <td className="px-3 py-2 text-xs text-zinc-300">{fmt(r.created_at)}</td>
                  <td className="px-3 py-2 max-w-xs truncate" title={r.query || r.filepath || ""}>
                    {r.query || r.filepath || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={r.user_mode === "consumer" ? "blue" : "indigo"}>{r.user_mode}</Badge>
                    {r.error && <span className="ml-1"><Badge tone="red">err</Badge></span>}
                  </td>
                  <td className="px-3 py-2 text-xs">{r.n_kept ?? "—"}</td>
                  <td className="px-3 py-2 text-xs">
                    {typeof r.mood_index === "number" ? (
                      <Badge tone={r.mood_index > 0.1 ? "green" : r.mood_index < -0.1 ? "red" : "amber"}>
                        {r.mood_index.toFixed(2)}
                      </Badge>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-zinc-400">{fmtMs(r.elapsed_ms)}</td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      <Pill small onClick={() => openDetail(r.id)}>view</Pill>
                      <a
                        href={exportUrlMd(r.id)}
                        download
                        className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] hover:bg-zinc-800"
                      >
                        ⬇md
                      </a>
                      <Pill small danger onClick={() => remove(r.id)}>×</Pill>
                    </div>
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-zinc-500">
                  {loading ? "Loading…" : "No runs yet. Run a pipeline from the Insights tab."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {err && <div className="mt-2 rounded-lg border border-red-800 bg-red-950/40 p-2 text-xs text-red-200">{err}</div>}
      </Card>

      {openRun && <RunDetailDialog run={openRun} onClose={() => setOpenRun(null)} onRerun={onRerun} />}
    </div>
  );
}
