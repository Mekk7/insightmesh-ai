import React, { useMemo, useState, useEffect, useCallback } from "react";
import axios from "axios";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  AreaChart,
  Area,
  BarChart,
  Bar,
} from "recharts";
import HistoryPanel from "./components/HistoryPanel.jsx";
import ComparePanel from "./components/ComparePanel.jsx";
import ProgressStrip from "./components/ProgressStrip.jsx";
import Insights from "./components/Insights.jsx";
import LandingPage from "./components/LandingPage.jsx";
import API_BASE from "./api.js";
import { exportReportMd, exportReportHtml, downloadBlob, streamPipeline } from "./lib/api.js";

/**
 * InsightMesh AI — Full React App (single file)
 * Upgrades:
 * - Explicit Run for Company (CSV) + runActive() used by quick actions
 * - Strictness normalized (low|normal|high) everywhere to match backend
 * - Cmd/Ctrl+Enter runs active tab
 * - Time presets (7d/30d/90d)
 * - LocalStorage persistence for controls & last uploaded path
 * - Download JSON report, Last-run chip
 * - Input clamping and small UX polish
 */

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || `${API_BASE}/api`,
  timeout: Number(import.meta.env.VITE_API_TIMEOUT ?? 0),
});

// ---------------- Small utils ----------------
const STORAGE_KEY = "insightmesh_ui_v1";

const clamp = (n, lo, hi) => {
  const v = Number(n);
  if (Number.isNaN(v)) return lo;
  return Math.max(lo, Math.min(hi, v));
};

const downloadJSON = (obj, filename = "insightmesh_report.json") => {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

const nowISOShort = () => new Date().toISOString().replace("T", " ").slice(0, 19);

// ---------------- API helpers ----------------
const runPipelineConsumer = async ({
  query,
  platforms,
  strictness,
  timeFrom,
  timeTo,
  debug,
  platformSettings,
}) => {
  const body = {
    input_mode: "consumer",
    filepath: null,
    platforms,
    mode: "fast",
    query_override: query,
    time_from: timeFrom || null,
    time_to: timeTo || null,
    strictness: strictness || null,
    debug: !!debug,
    platform_settings: platformSettings || null,
  };
  const { data } = await api.post("/insightmesh/run_pipeline", body);
  return data?.final_report ?? data;
};

const runPipelineCompany = async ({
  filepath,
  platforms,
  strictness,
  timeFrom,
  timeTo,
  debug,
  platformSettings,
}) => {
  const body = {
    input_mode: "company",
    filepath,
    platforms,
    mode: "fast",
    query_override: null,
    time_from: timeFrom || null,
    time_to: timeTo || null,
    strictness: strictness || null,
    debug: !!debug,
    platform_settings: platformSettings || null,
  };
  const { data } = await api.post("/insightmesh/run_pipeline", body);
  return data?.final_report ?? data;
};

// Analyzer remains the same
const analyzeReviews = async ({ reviews, query = null, strictness = null }) => {
  const payload = { reviews, ...(query ? { query } : {}), ...(strictness ? { strictness } : {}) };
  const { data } = await api.post("/reviews/analyze", payload);
  return data;
};

const uploadCSV = async (url, file) => {
  const fd = new FormData();
  fd.append("file", file);
  const { data } = await api.post(url, fd, { headers: { "Content-Type": "multipart/form-data" } });
  return data;
};

// ---------------- UI atoms ----------------
function Card({ title, children, right, className = "", subtitle }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-6 shadow-sm ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-zinc-100">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">{right}</div>
      </div>
      <div className="text-zinc-300 text-sm leading-relaxed">{children}</div>
    </div>
  );
}

function Badge({ children, tone = "zinc" }) {
  const map = {
    zinc: "bg-zinc-800 text-zinc-200 border-zinc-700",
    green: "bg-green-900/30 text-green-200 border-green-800",
    red: "bg-red-900/30 text-red-200 border-red-800",
    amber: "bg-amber-900/30 text-amber-200 border-amber-800",
    blue: "bg-blue-900/30 text-blue-200 border-blue-800",
    indigo: "bg-indigo-900/30 text-indigo-200 border-indigo-800",
    violet: "bg-violet-900/30 text-violet-200 border-violet-800",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${map[tone]}`}>{children}</span>;
}

function KPITile({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-zinc-100">{value ?? "—"}</div>
      {hint && <div className="mt-1 text-xs text-zinc-500">{hint}</div>}
    </div>
  );
}

function Chip({ children }) {
  return <span className="rounded-full border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-200">{children}</span>;
}

function IconButton({ label = "", onClick, children }) {
  return (
    <button
      aria-label={label}
      title={label}
      className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs hover:bg-zinc-800"
      onClick={onClick}
    >
      {children ?? "⚙"}
    </button>
  );
}

function ErrorToast({ error, onClose }) {
  if (!error) return null;
  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-red-800 bg-red-950/70 px-4 py-3 text-sm text-red-100 shadow-lg">
      <div className="mb-2 font-semibold">Error</div>
      <div className="whitespace-pre-wrap break-words">{String(error)}</div>
      <div className="mt-2 text-right">
        <button className="rounded-md border border-red-800/60 px-2 py-0.5 text-xs hover:bg-red-900/40" onClick={onClose}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

function Tabs({ tabs, value, onChange }) {
  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {tabs.map((t) => (
        <button
          key={t}
          className={`rounded-xl border px-3 py-1.5 text-sm ${
            value === t ? "border-blue-700 bg-blue-900/30 text-blue-200" : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
          }`}
          onClick={() => onChange(t)}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

function Meter({ value = 0, label, tone = "blue" }) {
  const tones = {
    blue: "bg-blue-600",
    green: "bg-green-600",
    amber: "bg-amber-500",
    red: "bg-rose-600",
    indigo: "bg-indigo-600",
  };
  return (
    <div>
      {label && <div className="mb-1 text-xs text-zinc-400">{label}</div>}
      <div className="h-2 w-full rounded-full bg-zinc-800">
        <div className={`h-2 rounded-full ${tones[tone]}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
    </div>
  );
}

// ---------------- Shared helpers ----------------
const CATEGORY_COLORS = {
  Praise: "bg-emerald-600 text-white",
  Complaint: "bg-rose-600 text-white",
  Suggestion: "bg-amber-500 text-black",
  Prediction: "bg-indigo-500 text-white",
  Neutral: "bg-slate-400 text-black",
};

const starLabelToNumber = (label) => {
  if (!label) return 3;
  const l = String(label).toLowerCase();
  if (l.startsWith("1")) return 1;
  if (l.startsWith("2")) return 2;
  if (l.startsWith("3")) return 3;
  if (l.startsWith("4")) return 4;
  if (l.startsWith("5")) return 5;
  return 3;
};

const toneForSentiment = (label) => {
  const n = starLabelToNumber(label);
  if (n >= 4) return "green";
  if (n <= 2) return "red";
  return "amber";
};

const percent = (n) => (typeof n === "number" ? Math.round(n) : 0);

const fmtDate = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
};

const lastNDaysISO = (days) => {
  const now = new Date();
  const past = new Date(now.getTime() - days * 86400000);
  return { from: past.toISOString().slice(0, 16), to: now.toISOString().slice(0, 16) };
};

// Merge cluster_suggestions onto canonical cluster list by cluster_id
function mergeClusterSuggestions(canonical = [], suggestions = []) {
  const byId = new Map(canonical.map((c) => [Number(c.cluster_id), { ...c }]));
  for (const s of suggestions) {
    const id = Number(s?.cluster_id);
    if (!byId.has(id)) continue;
    const prev = byId.get(id);
    byId.set(id, { ...prev, _suggestions: Array.isArray(s?.suggestions) ? s.suggestions : [], _rationale: s?.rationale || null });
  }
  return Array.from(byId.values());
}

// Executive brief fallback composer (if backend didn’t send executive_brief yet)
function buildExecutiveBriefFallback(overview, perReviewLen) {
  const canonical = overview?.canonical_clusters ?? [];
  if (!canonical.length) return null;
  const top = [...canonical].sort((a, b) => (b?.count || 0) - (a?.count || 0))[0];
  if (!top) return null;
  const bullets = top?.solution?.bullets || [];
  return {
    biggest_pain: {
      theme: top.reason || "Top user pain",
      share_pct: Math.round(top?.["share_%"] || 0),
      evidence_quote: Array.isArray(top?.quotes) && top.quotes.length ? (typeof top.quotes[0] === "string" ? top.quotes[0] : top.quotes[0]?.quote) : null,
      why_it_matters: "Frequent negative mentions; impacts access/experience",
    },
    quick_win: {
      actions: bullets.slice(0, 2).length ? bullets.slice(0, 2) : ["Publish concise FAQ with workaround", "Instrument and reproduce with trace IDs"],
      expected_impact: "Visible drop in related complaints",
    },
    risk_or_opportunity: (top?.solution?.high_risk
      ? { label: "Safety risk", note: "Potential harm mentioned; add detection test & comms" }
      : { label: "Opportunity", note: "Prototype minimal enhancement behind a flag" }),
    _fallback: true,
    _n: perReviewLen || 0,
  };
}

// Cluster card UI
function ClusterCard({ c }) {
  const bullets = c?.solution?.bullets ?? [];
  const sources = c?.solution?.source ?? [];
  const confidence = c?.solution?.confidence ?? 0;
  const expGap = !!c?.solution?.expectation_gap;
  const highRisk = !!c?.solution?.high_risk;
  const suggestions = c?._suggestions ?? [];

  return (
    <div className="rounded-xl border border-zinc-800 p-4 bg-zinc-900/40">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex-1">
          <div className="text-sm font-semibold text-zinc-100">{c?.reason || "Cluster"}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
            <Chip>count: {c?.count ?? 0}</Chip>
            <Chip>share: {c?.["share_%"] ?? 0}%</Chip>
            {typeof c?.support === "number" && <Chip>support: {Math.round((c.support || 0) * 100)}%</Chip>}
            {typeof c?.centroid_sim_mean === "number" && <Chip>cohesion: {(c.centroid_sim_mean || 0).toFixed(2)}</Chip>}
            {expGap && <Badge tone="amber">expectation gap</Badge>}
            {highRisk && <Badge tone="red">risk</Badge>}
          </div>
        </div>
        <Badge tone="indigo">cluster #{String(c?.cluster_id ?? "")}</Badge>
      </div>

      <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-12">
        <div className="md:col-span-7 space-y-2">
          <Meter value={percent(c?.["share_%"])} label="Share of reviews" tone="blue" />
          {typeof c?.support === "number" && <Meter value={percent((c.support || 0) * 100)} label="Label support" tone="green" />}
          {typeof confidence === "number" && <Meter value={percent(confidence * 100)} label="Solution confidence" tone="indigo" />}
        </div>
        <div className="md:col-span-5">
          {bullets.length > 0 && (
            <div>
              <div className="mb-1 text-xs text-zinc-400">Solution bullets</div>
              <ul className="ml-4 list-disc text-sm space-y-1">
                {bullets.map((b, i) => (
                  <li key={i}>{String(b)}</li>
                ))}
              </ul>
            </div>
          )}
          {c?.solution?.backlog && (
            <div className="mt-2 rounded-lg border border-zinc-800 bg-zinc-900/50 p-2 text-xs text-zinc-300">
              <span className="font-medium text-zinc-200">Backlog:</span> {c.solution.backlog}
            </div>
          )}
        </div>
      </div>

      {(suggestions.length > 0 || sources.length > 0) && (
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-12">
          {suggestions.length > 0 && (
            <div className="md:col-span-7">
              <div className="mb-1 text-xs text-zinc-400">Auto suggestions</div>
              <ul className="ml-4 list-disc text-sm space-y-1">
                {suggestions.slice(0, 5).map((s, i) => (
                  <li key={i}>{String(s)}</li>
                ))}
              </ul>
            </div>
          )}
          {sources.length > 0 && (
            <div className="md:col-span-5">
              <div className="mb-1 text-xs text-zinc-400">Sources</div>
              <ul className="ml-4 list-disc text-xs space-y-1">
                {sources.map((src, i) => (
                  <li key={i}>
                    <span className="text-zinc-400">{src?.path ? `${src.path} — ` : ""}</span>
                    <span className="text-zinc-300">{src?.preview ?? ""}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {(c?.quotes ?? []).length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs text-zinc-400">Representative quotes</div>
          {(c.quotes || []).slice(0, 2).map((q, i) => (
            <blockquote key={i} className="mb-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2 text-xs italic text-zinc-300">
              “{typeof q === "string" ? q : q?.quote ?? ""}”
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}

// ===================== INSIGHTS (dual input) =====================
function InsightsPanel({ rerunSeed = null, onRerunConsumed = () => {} }) {
  // load saved prefs
  const saved = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; }
  }, []);

  // ---------- Section 1: Control Center ----------
  const [controlTab, setControlTab] = useState(saved.controlTab ?? "Consumer");
  const [platforms, setPlatforms] = useState(saved.platforms ?? { youtube: true, reddit: true });

  // Per-platform settings (defaults per spec)
  const [showYTSettings, setShowYTSettings] = useState(false);
  const [ytMaxVideos, setYtMaxVideos] = useState(saved.ytMaxVideos ?? 6);
  const [ytMaxComments, setYtMaxComments] = useState(saved.ytMaxComments ?? 80);

  const [showRdtSettings, setShowRdtSettings] = useState(false);
  const [rdtSubs, setRdtSubs] = useState(saved.rdtSubs ?? "TeslaMotors, electricvehicles");
  const [rdtTimeFilter, setRdtTimeFilter] = useState(saved.rdtTimeFilter ?? "month");
  const [rdtMaxPosts, setRdtMaxPosts] = useState(saved.rdtMaxPosts ?? 80);
  const [rdtCommentsMode, setRdtCommentsMode] = useState(saved.rdtCommentsMode ?? "top"); // top | all | 0

  const [strictness, setStrictness] = useState(saved.strictness ?? "normal");
  const [timeFrom, setTimeFrom] = useState(saved.timeFrom ?? "");
  const [timeTo, setTimeTo] = useState(saved.timeTo ?? "");
  const [debug, setDebug] = useState(Boolean(saved.debug) ?? false); // default OFF per spec

  // Consumer flow
  const [query, setQuery] = useState(saved.query ?? "Tesla Model Y");

  // Company flow
  const [csvUploading, setCsvUploading] = useState(false);
  const [uploadedPath, setUploadedPath] = useState(saved.uploadedPath ?? "");

  // Output
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [lastRunAt, setLastRunAt] = useState(null);

  // Streaming (SSE) mode
  const [stream, setStream] = useState(Boolean(saved.stream) ?? false);
  const [streamEvents, setStreamEvents] = useState([]);

  // persist settings
  useEffect(() => {
    const toSave = {
      controlTab,
      platforms,
      ytMaxVideos,
      ytMaxComments,
      rdtSubs,
      rdtTimeFilter,
      rdtMaxPosts,
      rdtCommentsMode,
      strictness,
      timeFrom,
      timeTo,
      debug,
      query,
      uploadedPath,
    };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave)); } catch {}
  }, [
    controlTab, platforms, ytMaxVideos, ytMaxComments, rdtSubs, rdtTimeFilter, rdtMaxPosts,
    rdtCommentsMode, strictness, timeFrom, timeTo, debug, query, uploadedPath
  ]);

  // Consume re-run seed from parent (when user clicks "Re-run" in History)
  useEffect(() => {
    if (!rerunSeed) return;
    if (rerunSeed.mode === "company" && rerunSeed.filepath) {
      setControlTab("Company (CSV)");
      setUploadedPath(rerunSeed.filepath);
    } else {
      setControlTab("Consumer");
      if (rerunSeed.query) setQuery(rerunSeed.query);
    }
    if (rerunSeed.strictness) setStrictness(rerunSeed.strictness);
    if (rerunSeed.time_from) setTimeFrom(rerunSeed.time_from);
    if (rerunSeed.time_to) setTimeTo(rerunSeed.time_to);
    if (Array.isArray(rerunSeed.platforms)) {
      setPlatforms({
        youtube: rerunSeed.platforms.includes("youtube"),
        reddit: rerunSeed.platforms.includes("reddit"),
      });
    }
    onRerunConsumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rerunSeed]);

  // keyboard shortcut: Cmd/Ctrl + Enter to run active
  const runActiveRef = React.useRef(null);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (runActiveRef.current) runActiveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selectedPlatforms = useMemo(
    () => Object.entries(platforms).filter(([, v]) => v).map(([k]) => k),
    [platforms]
  );

  const platformSettings = useMemo(
    () => ({
      youtube: platforms.youtube
        ? { max_videos: clamp(ytMaxVideos, 1, 12), max_comments_per_video: clamp(ytMaxComments, 1, 500) }
        : null,
      reddit: platforms.reddit
        ? {
            subreddits: rdtSubs.split(",").map((s) => s.trim()).filter(Boolean),
            time_filter: rdtTimeFilter,
            max_posts: clamp(rdtMaxPosts, 1, 500),
            comments_mode: rdtCommentsMode,
          }
        : null,
    }),
    [platforms, ytMaxVideos, ytMaxComments, rdtSubs, rdtTimeFilter, rdtMaxPosts, rdtCommentsMode]
  );

  const runConsumer = async () => {
    setLoading(true);
    setErr(null);
    if (stream) setStreamEvents([]);
    try {
      let data;
      if (stream) {
        const body = {
          input_mode: "consumer",
          filepath: null,
          platforms: selectedPlatforms,
          mode: "fast",
          query_override: query,
          time_from: timeFrom || null,
          time_to: timeTo || null,
          strictness: strictness || null,
          debug: !!debug,
          platform_settings: platformSettings || null,
        };
        data = await streamPipeline(body, (evt) => setStreamEvents((xs) => [...xs, evt]));
      } else {
        data = await runPipelineConsumer({
          query, platforms: selectedPlatforms, strictness,
          timeFrom: timeFrom || null, timeTo: timeTo || null,
          debug, platformSettings,
        });
      }
      setRes(data);
      setLastRunAt(nowISOShort());
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setLoading(false);
    }
  };

  const runCompany = async () => {
    if (!uploadedPath) {
      setErr("Upload a CSV first, then click Run.");
      return;
    }
    setLoading(true);
    setErr(null);
    if (stream) setStreamEvents([]);
    try {
      let data;
      if (stream) {
        const body = {
          input_mode: "company",
          filepath: uploadedPath,
          platforms: selectedPlatforms,
          mode: "fast",
          query_override: null,
          time_from: timeFrom || null,
          time_to: timeTo || null,
          strictness: strictness || null,
          debug: !!debug,
          platform_settings: platformSettings || null,
        };
        data = await streamPipeline(body, (evt) => setStreamEvents((xs) => [...xs, evt]));
      } else {
        data = await runPipelineCompany({
          filepath: uploadedPath, platforms: selectedPlatforms, strictness,
          timeFrom: timeFrom || null, timeTo: timeTo || null,
          debug, platformSettings,
        });
      }
      setRes(data);
      setLastRunAt(nowISOShort());
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setLoading(false);
    }
  };

  const runActive = useCallback(() => {
    if (controlTab === "Consumer") return runConsumer();
    return runCompany();
  }, [controlTab, runConsumer, runCompany]);

  // expose for keyboard shortcut
  runActiveRef.current = runActive;

  const onCSV = async (file) => {
    setCsvUploading(true);
    setErr(null);
    try {
      const u = await uploadCSV("/understand/upload", file);
      const path = u?.raw_path || u?.rawPath || "";
      setUploadedPath(path);
      if (!path) throw new Error("Upload succeeded but no server path returned.");
      setLoading(true);
      const data = await runPipelineCompany({
        filepath: path,
        platforms: selectedPlatforms,
        strictness,
        timeFrom: timeFrom || null,
        timeTo: timeTo || null,
        debug,
        platformSettings,
      });
      setRes(data);
      setLastRunAt(nowISOShort());
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setCsvUploading(false);
      setLoading(false);
    }
  };

  // ---------- Derived ----------
  const analysis = res?.analysis || {};
  const meta = analysis?.meta || res?.meta || {};
  const overview = analysis?.overview;
  const exec = analysis?.executive_summary;
  const perReview = (analysis?.per_review ?? []).map((r) => ({
    ...r,
    category: r.review_category,
    topics: r.topic_labels,
    text: r.original,
    reason: r.canonical_reason || "",
  }));

  // Executive brief (prefer backend, fallback to canonical)
  const N = perReview.length;
  const brief = analysis?.executive_brief || buildExecutiveBriefFallback(overview, N);
  const timeChip =
    meta?.time_from || meta?.time_to
      ? `${fmtDate(meta.time_from) || "?"} → ${fmtDate(meta.time_to) || "?"}`
      : (timeFrom && timeTo ? `${timeFrom.slice(0, 10)} → ${timeTo.slice(0, 10)}` : "Window: not set");

  // New: canonical clusters + cluster suggestions merged
  const canonical = overview?.canonical_clusters ?? [];
  const clusterSuggestions = overview?.cluster_suggestions ?? [];
  const mergedClusters = useMemo(() => mergeClusterSuggestions(canonical, clusterSuggestions), [canonical, clusterSuggestions]);

  // Signal Funnel data (pull from new debug schema)
  const dbg = res?.debug || {};
  const mergeDbg = dbg?.merge || {};
  const perPlatformCounts = dbg?.per_platform_counts || {};
  const perPlatformDrops = dbg?.per_platform_drops || {};

  const aggCounts = useMemo(() => {
    let fetched_raw = 0, text_extracted = 0, cleaned = 0, deduped = 0;
    for (const p of Object.values(perPlatformCounts)) {
      fetched_raw += Number(p?.fetched_raw || 0);
      text_extracted += Number(p?.text_extracted || 0);
      cleaned += Number(p?.cleaned || 0);
      deduped += Number(p?.deduped || 0);
    }
    return { fetched_raw, text_extracted, cleaned, deduped };
  }, [perPlatformCounts]);

  const aggDrops = useMemo(() => {
    const out = {};
    for (const p of Object.values(perPlatformDrops)) {
      for (const [k, v] of Object.entries(p || {})) {
        out[k] = (out[k] || 0) + Number(v || 0);
      }
    }
    return out;
  }, [perPlatformDrops]);

  const requestedLabel = useMemo(() => {
    const parts = [];
    if (platforms.youtube) parts.push(`YouTube ${ytMaxVideos}×${ytMaxComments}=${Number(ytMaxVideos) * Number(ytMaxComments)}`);
    if (platforms.reddit) parts.push(`Reddit ${rdtMaxPosts} posts`);
    return parts.join(" • ");
  }, [platforms, ytMaxVideos, ytMaxComments, rdtMaxPosts]);

  // Quick outcome adjusters
  const actStrictLow = () => { setStrictness("low"); runActive(); };
  const actMoreYT = () => { setYtMaxComments((v) => clamp(Number(v) + 40, 1, 500)); runActive(); };
  const actWindowDays = (days) => {
    const w = lastNDaysISO(days);
    setTimeFrom(w.from);
    setTimeTo(w.to);
    runActive();
  };

  // ---------- UI ----------
  return (
    <div className="space-y-6">
      {/* Section 1 — Control Center */}
      <Card
        title="Control Center"
        subtitle="Run end-to-end using a product query or a CSV dataset"
        right={
          <>
            {lastRunAt && <Badge tone="indigo">Last run: {lastRunAt}</Badge>}
            <Badge>Pipeline</Badge>
          </>
        }
      >
        <Tabs tabs={["Consumer", "Company (CSV)"]} value={controlTab} onChange={setControlTab} />

        {controlTab === "Consumer" && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
            {/* Consumer query + Run */}
            <div className="md:col-span-7 rounded-xl border border-zinc-800/70 bg-zinc-900/50 p-4">
              <div className="mb-2 text-sm font-medium text-zinc-200">Product / query</div>
              <div className="flex flex-col gap-2 md:flex-row md:items-center">
                <input
                  className="w-full rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-700"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="e.g., Sony WH-1000XM6"
                  onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runActive(); }}
                />
                <button
                  onClick={runConsumer}
                  disabled={loading}
                  className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {loading ? "Running…" : "Run"}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-zinc-400">
                <span>Presets:</span>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(7)}>Last 7d</button>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(30)}>Last 30d</button>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(90)}>Last 90d</button>
              </div>
            </div>

            {/* Platforms + gear */}
            <div className="md:col-span-5 rounded-xl border border-zinc-800/70 bg-zinc-900/50 p-4">
              <div className="mb-2 text-sm font-medium text-zinc-200">Platforms</div>
              <div className="flex flex-wrap items-center gap-3">
                {/* YouTube */}
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={platforms.youtube}
                    onChange={(e) => setPlatforms((p) => ({ ...p, youtube: e.target.checked }))}
                  />
                  <span>YouTube</span>
                </label>
                <IconButton label="YouTube settings" onClick={() => setShowYTSettings((v) => !v)} />
                {showYTSettings && (
                  <div className="mt-2 w-full rounded-lg border border-zinc-800 bg-zinc-900 p-3 text-xs">
                    <div className="mb-2 text-zinc-400">YouTube settings</div>
                    <div className="grid grid-cols-2 gap-2">
                      <label className="flex items-center gap-2">
                        <span>Max videos</span>
                        <input
                          type="number"
                          min={1}
                          max={12}
                          className="w-20 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                          value={ytMaxVideos}
                          onChange={(e) => setYtMaxVideos(e.target.value)}
                          onBlur={(e) => setYtMaxVideos(clamp(e.target.value, 1, 12))}
                        />
                      </label>
                      <label className="flex items-center gap-2">
                        <span>Comments/video</span>
                        <input
                          type="number"
                          min={10}
                          max={500}
                          className="w-28 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                          value={ytMaxComments}
                          onChange={(e) => setYtMaxComments(e.target.value)}
                          onBlur={(e) => setYtMaxComments(clamp(e.target.value, 1, 500))}
                        />
                      </label>
                    </div>
                  </div>
                )}

                {/* Reddit */}
                <label className="ml-2 flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={platforms.reddit}
                    onChange={(e) => setPlatforms((p) => ({ ...p, reddit: e.target.checked }))}
                  />
                  <span>Reddit</span>
                </label>
                <IconButton label="Reddit settings" onClick={() => setShowRdtSettings((v) => !v)} />
                {showRdtSettings && (
                  <div className="mt-2 w-full rounded-lg border border-zinc-800 bg-zinc-900 p-3 text-xs">
                    <div className="mb-2 text-zinc-400">Reddit settings</div>
                    <div className="grid grid-cols-2 gap-2">
                      <label className="col-span-2 flex items-center gap-2">
                        <span>Subreddits</span>
                        <input
                          className="flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                          value={rdtSubs}
                          onChange={(e) => setRdtSubs(e.target.value)}
                          placeholder="comma-separated"
                        />
                      </label>
                      <label className="flex items-center gap-2">
                        <span>Time</span>
                        <select
                          value={rdtTimeFilter}
                          onChange={(e) => setRdtTimeFilter(e.target.value)}
                          className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                        >
                          {["day", "week", "month", "year", "all"].map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex items-center gap-2">
                        <span>Max posts</span>
                        <input
                          type="number"
                          min={10}
                          max={500}
                          className="w-24 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                          value={rdtMaxPosts}
                          onChange={(e) => setRdtMaxPosts(e.target.value)}
                          onBlur={(e) => setRdtMaxPosts(clamp(e.target.value, 1, 500))}
                        />
                      </label>
                      <label className="flex items-center gap-2">
                        <span>Comments</span>
                        <select
                          value={rdtCommentsMode}
                          onChange={(e) => setRdtCommentsMode(e.target.value)}
                          className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                        >
                          <option value="top">top</option>
                          <option value="all">all</option>
                          <option value="0">0</option>
                        </select>
                      </label>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Controls row */}
            <div className="md:col-span-12 mt-2 grid grid-cols-1 gap-3 md:grid-cols-12">
              <div className="md:col-span-4 flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1 text-xs">
                  <input type="checkbox" className="accent-blue-600" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Show debug
                </label>
                <label className="flex items-center gap-1 text-xs" title="Stream live progress via SSE">
                  <input type="checkbox" className="accent-blue-600" checked={stream} onChange={(e) => setStream(e.target.checked)} /> Stream progress
                </label>
              </div>
              <div className="md:col-span-8 grid grid-cols-2 gap-2 md:grid-cols-4">
                <select value={strictness} onChange={(e) => setStrictness(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs">
                  <option value="low">strictness: low</option>
                  <option value="normal">strictness: normal</option>
                  <option value="high">strictness: high</option>
                </select>
                <input
                  type="datetime-local"
                  value={timeFrom}
                  onChange={(e) => setTimeFrom(e.target.value)}
                  className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
                  placeholder="time_from"
                />
                <input
                  type="datetime-local"
                  value={timeTo}
                  onChange={(e) => setTimeTo(e.target.value)}
                  className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
                  placeholder="time_to"
                />
                <div className="flex items-center text-xs text-zinc-400">Optional time window</div>
              </div>
            </div>
          </div>
        )}

        {controlTab === "Company (CSV)" && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
            <div className="md:col-span-7 rounded-xl border border-zinc-800/70 bg-zinc-900/50 p-4">
              <div className="mb-2 text-sm font-medium text-zinc-200">Upload & Run</div>
              <div className="flex gap-2 items-center">
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(e) => e.target.files?.[0] && onCSV(e.target.files[0])}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
                />
                <button
                  onClick={runCompany}
                  disabled={!uploadedPath || loading}
                  className="shrink-0 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  title={uploadedPath ? "Run analysis on the last uploaded CSV" : "Upload a CSV to enable Run"}
                >
                  {loading ? "Running…" : "Run"}
                </button>
              </div>
              {csvUploading && <div className="mt-2 text-xs text-zinc-400">Uploading…</div>}
              {uploadedPath && <div className="mt-1 truncate text-xs text-zinc-500">Last dataset: {uploadedPath}</div>}
              <div className="mt-2 text-xs text-zinc-500">
                Tip: tweak strictness, platforms, or time window, then click <span className="text-zinc-300 font-medium">Run</span> to re-execute without re-uploading.
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-zinc-400">
                <span>Presets:</span>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(7)}>Last 7d</button>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(30)}>Last 30d</button>
                <button className="rounded-md border border-zinc-700 px-2 py-0.5 hover:bg-zinc-800" onClick={() => actWindowDays(90)}>Last 90d</button>
              </div>
            </div>

            {/* Platforms + gear (same as consumer) */}
            <div className="md:col-span-5 rounded-xl border border-zinc-800/70 bg-zinc-900/50 p-4">
              <div className="mb-2 text-sm font-medium text-zinc-200">Platforms</div>
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={platforms.youtube}
                    onChange={(e) => setPlatforms((p) => ({ ...p, youtube: e.target.checked }))}
                  />
                  <span>YouTube</span>
                </label>
                <IconButton label="YouTube settings" onClick={() => setShowYTSettings((v) => !v)} />
                <label className="ml-2 flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={platforms.reddit}
                    onChange={(e) => setPlatforms((p) => ({ ...p, reddit: e.target.checked }))}
                  />
                  <span>Reddit</span>
                </label>
                <IconButton label="Reddit settings" onClick={() => setShowRdtSettings((v) => !v)} />
              </div>
              {(showYTSettings || showRdtSettings) && (
                <div className="mt-2 text-xs text-zinc-500">Use the gear icons to configure per-platform extraction.</div>
              )}
            </div>

            {/* Shared controls */}
            <div className="md:col-span-12 mt-2 grid grid-cols-1 gap-3 md:grid-cols-12">
              <div className="md:col-span-4 flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1 text-xs">
                  <input type="checkbox" className="accent-blue-600" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Show debug
                </label>
                <label className="flex items-center gap-1 text-xs" title="Stream live progress via SSE">
                  <input type="checkbox" className="accent-blue-600" checked={stream} onChange={(e) => setStream(e.target.checked)} /> Stream progress
                </label>
              </div>
              <div className="md:col-span-8 grid grid-cols-2 gap-2 md:grid-cols-4">
                <select value={strictness} onChange={(e) => setStrictness(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs">
                  <option value="low">strictness: low</option>
                  <option value="normal">strictness: normal</option>
                  <option value="high">strictness: high</option>
                </select>
                <input
                  type="datetime-local"
                  value={timeFrom}
                  onChange={(e) => setTimeFrom(e.target.value)}
                  className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
                />
                <input
                  type="datetime-local"
                  value={timeTo}
                  onChange={(e) => setTimeTo(e.target.value)}
                  className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
                />
                <div className="flex items-center text-xs text-zinc-400">Optional time window</div>
              </div>
            </div>
          </div>
        )}

        {/* Streaming progress (only visible when stream mode is on) */}
        {stream && (loading || streamEvents.length > 0) && (
          <ProgressStrip events={streamEvents} error={err} />
        )}

        {/* Signal Funnel (shown when there is a result) */}
        {res && (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <div className="mb-2 text-sm font-medium text-zinc-100">Signal Funnel</div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
              <div className="md:col-span-9 space-y-1 text-sm">
                <div>
                  <span className="text-zinc-400">Requested: </span>
                  <span className="font-medium">{requestedLabel || "—"}</span>
                </div>
                <div>
                  <span className="text-zinc-400">Fetched (raw strings): </span>
                  <span className="font-medium">{aggCounts.text_extracted || "—"}</span>
                </div>
                <div>
                  <span className="text-zinc-400">After clean (quality gates): </span>
                  <span className="font-medium">{aggCounts.cleaned || "—"}</span>
                </div>
                <div>
                  <span className="text-zinc-400">After dedupe: </span>
                  <span className="font-medium">{aggCounts.deduped || "—"}</span>
                </div>
                <div>
                  <span className="text-zinc-400">After compaction (Analyzed pool): </span>
                  <span className="font-medium">{mergeDbg?.final_comments_len ?? "—"}</span>
                </div>
                <div className="mt-2">
                  <div className="mb-1 text-xs text-zinc-400">Dropped by reason</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(aggDrops).map(([k, v]) => (
                      <Chip key={k}>
                        {k}: {String(v)}
                      </Chip>
                    ))}
                    {Object.keys(aggDrops).length === 0 && <span className="text-xs text-zinc-500">—</span>}
                  </div>
                </div>
              </div>
              <div className="md:col-span-3">
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 text-xs">
                  <div className="mb-1 font-medium text-zinc-200">Change outcome</div>
                  <div className="space-y-1">
                    <button className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800" onClick={actMoreYT}>
                      ↑ Increase YouTube comments (+40) & re-run
                    </button>
                    <button className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800" onClick={actStrictLow}>
                      ↓ Loosen strictness (Low) & re-run
                    </button>
                    <div className="grid grid-cols-3 gap-1">
                      <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800" onClick={() => actWindowDays(7)}>
                        ↔ 7d
                      </button>
                      <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800" onClick={() => actWindowDays(30)}>
                        ↔ 30d
                      </button>
                      <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800" onClick={() => actWindowDays(90)}>
                        ↔ 90d
                      </button>
                    </div>
                    <button
                      className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800"
                      onClick={() => res && downloadJSON(res)}
                    >
                      ⤓ Download report (JSON)
                    </button>
                    <div className="grid grid-cols-2 gap-1">
                      <button
                        className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800"
                        title="Download Markdown report"
                        onClick={async () => {
                          if (!res) return;
                          try {
                            const blob = await exportReportMd(res);
                            downloadBlob(blob, "insightmesh_report.md");
                          } catch (e) {
                            alert("Markdown export failed: " + (e.message || e));
                          }
                        }}
                      >
                        ⤓ .md
                      </button>
                      <button
                        className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 hover:bg-zinc-800"
                        title="Download HTML report (print → PDF)"
                        onClick={async () => {
                          if (!res) return;
                          try {
                            const blob = await exportReportHtml(res);
                            downloadBlob(blob, "insightmesh_report.html");
                          } catch (e) {
                            alert("HTML export failed: " + (e.message || e));
                          }
                        }}
                      >
                        ⤓ .html
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* ---------- Section 2 — Executive Summary (decision-first) ---------- */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
        <Card
          title="Executive Summary"
          className="md:col-span-12"
          subtitle="Highlights from reviews"
          right={
            res ? (
              <IconButton label="Copy summary" onClick={async () => {
                const summaryText = (() => {
                  if (!res) return "";
                  const b = res?.analysis?.executive_brief || brief;
                  if (!b) return "";
                  const lines = [
                    `Biggest pain: ${b.biggest_pain?.theme ?? "-"}` + (typeof b.biggest_pain?.share_pct === "number" ? ` (${b.biggest_pain.share_pct}% of reviews)` : ""),
                    `Quick win: ${(b.quick_win?.actions || []).join(" • ") || "-"}` + (b.quick_win?.expected_impact ? ` (${b.quick_win.expected_impact})` : ""),
                    `${b.risk_or_opportunity?.label || "Risk/Opportunity"}: ${b.risk_or_opportunity?.note || "-"}`,
                  ];
                  return lines.join("\n");
                })();
                try { await navigator.clipboard.writeText(summaryText); } catch {}
              }}>
                📋
              </IconButton>
            ) : null
          }
        >
          {!res && <div className="text-sm text-zinc-500">Run the pipeline using search or CSV.</div>}
          {res && (
            <div className="space-y-3">
              {/* Chips */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Chip>N={N || "—"}</Chip>
                <Chip>{timeChip}</Chip>
                <Chip>Strictness: {meta?.strictness ?? strictness}</Chip>
                {N > 0 && N < 30 && <Badge tone="amber">Small sample — directional</Badge>}
              </div>

              {/* Three bullets */}
              {brief ? (
                <ul className="list-disc pl-5 text-sm space-y-2">
                  <li>
                    <span className="font-semibold">Biggest pain:</span>{" "}
                    {brief.biggest_pain?.theme || "—"}{" "}
                    {typeof brief.biggest_pain?.share_pct === "number" && (
                      <span className="text-zinc-400">({brief.biggest_pain.share_pct}% of reviews)</span>
                    )}
                    {brief.biggest_pain?.evidence_quote && (
                      <div className="mt-1 text-xs italic text-zinc-400">“{brief.biggest_pain.evidence_quote}”</div>
                    )}
                  </li>
                  <li>
                    <span className="font-semibold">Quick win:</span>{" "}
                    {(brief.quick_win?.actions || []).join(" • ") || "—"}
                    {brief.quick_win?.expected_impact && (
                      <span className="ml-1 text-zinc-400">({brief.quick_win.expected_impact})</span>
                    )}
                  </li>
                  <li>
                    <span className="font-semibold">{brief.risk_or_opportunity?.label || "Risk/Opportunity"}:</span>{" "}
                    {brief.risk_or_opportunity?.note || "—"}
                  </li>
                </ul>
              ) : (
                <div className="text-sm text-zinc-500">No clear themes yet.</div>
              )}
            </div>
          )}
        </Card>

        {/* Existing sections kept as-is */}
        {(overview || res) && (
          <Card title="Key Metrics" className="md:col-span-12">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
              <KPITile label="Mood Index" value={typeof overview?.mood_index === "number" ? overview.mood_index.toFixed(2) : "—"} hint="range −1…+1" />
              <KPITile label="Avg Sentiment" value={overview?.average_sentiment?.toFixed?.(2) ?? "—"} />
              <KPITile label="Total Reviews" value={N || "—"} />
              <KPITile label="Strictness" value={meta?.strictness ?? strictness} />
            </div>
          </Card>
        )}

        {(overview?.stars || exec?.totals_by_category) && (
          <Card title="Distribution" className="md:col-span-12" subtitle="Star mix & category totals">
            <DistributionBlock overview={overview} exec={exec} />
          </Card>
        )}

        {!!(exec?.top_reasons_overall) && (
          <Card title="Top Reasons Overall" className="md:col-span-12" subtitle="Most-cited reasons by category">
            <TopReasonsBlock exec={exec} />
          </Card>
        )}

        {!!(exec?.top_aspects?.length) && (
          <Card title="Top Aspects" className="md:col-span-12" subtitle="Themes and reasons drawn from reviews">
            <TopAspectsBlock exec={exec} />
          </Card>
        )}

        {/* Model Intelligence */}
        {(meta?.kept_count !== undefined || Object.keys(meta?.dropped_summary || {}).length > 0) && (
          <ModelIntelligenceBlock meta={meta} analysis={analysis} />
        )}

        {/* Canonical clusters with solutions + suggestions */}
        {mergedClusters?.length > 0 && (
          <Card title="Canonical Clusters (Reason-first)" className="md:col-span-12" subtitle="Cohesive themes, representative quotes, and solution ideas">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {mergedClusters.map((c) => (
                <ClusterCard key={c.cluster_id} c={c} />
              ))}
            </div>
          </Card>
        )}

        {/* Per-review table */}
        <PerReviewTable res={res} perReview={perReview} />
      </div>
      <ErrorToast error={err} onClose={() => setErr(null)} />
    </div>
  );
}

// --- Extracted blocks reused from your original file to keep size readable ----
function DistributionBlock({ overview, exec }) {
  const stars = overview?.stars ?? {};
  const starData = useMemo(() => {
    return [1, 2, 3, 4, 5].map((n) => ({ label: `${n}★`, count: Number(stars[`${n} star`] ?? stars[`${n} stars`] ?? 0) }));
  }, [stars]);
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
      <div className="md:col-span-7 h-48">
        <ResponsiveContainer>
          <BarChart data={starData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="label" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="count" name="Count" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="md:col-span-5 flex flex-wrap items-start gap-2">
        {Object.keys(exec?.totals_by_category || {}).length === 0 && <div className="text-sm text-zinc-500">No category totals.</div>}
        {Object.entries(exec?.totals_by_category || {}).map(([k, v]) => (
          <Chip key={k}>
            {k}: {String(v)}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function TopReasonsBlock({ exec }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
      {Object.entries(exec.top_reasons_overall).map(([cat, reasons]) => (
        <div key={cat} className="rounded-xl border border-zinc-800 p-3">
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${CATEGORY_COLORS[cat] ?? "bg-zinc-800 text-zinc-200"}`}>{cat}</span>
          <ul className="mt-2 ml-4 list-disc text-sm space-y-1">
            {(reasons || []).slice(0, 5).map((r, i) => (
              <li key={i}>{String(r)}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function TopAspectsBlock({ exec }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {(exec.top_aspects || []).slice(0, 6).map((a, i) => (
        <div key={i} className="rounded-xl border border-zinc-800 p-4">
          <div className="mb-1 flex items-center justify-between">
            <div className="text-sm font-medium text-zinc-100">{a?.aspect ?? "Aspect"}</div>
            <Badge tone="amber">{a?.mentions ?? 0} mentions</Badge>
          </div>
          {typeof a?.share_of_reviews === "number" && (
            <div className="mb-2 text-xs text-zinc-400">{Math.round(a.share_of_reviews * 100)}% of reviews</div>
          )}
          <div className="space-y-2">
            {(a?.by_category ?? []).slice(0, 3).map((bc, j) => (
              <div key={j}>
                <div className="text-xs text-zinc-400">{bc?.category ?? "Category"}</div>
                <ul className="ml-4 list-disc text-sm">
                  {(bc?.reasons ?? []).slice(0, 2).map((r, k) => (
                    <li key={k}>{String(r)}</li>
                  ))}
                </ul>
                {(bc?.quotes ?? []).slice(0, 2).map((q, qx) => (
                  <blockquote key={qx} className="mt-1 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2 text-xs italic text-zinc-300">
                    “{q?.quote ?? ""}”
                  </blockquote>
                ))}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ModelIntelligenceBlock({ meta, analysis }) {
  return (
    <Card
      title="Model Intelligence"
      className="md:col-span-12"
      subtitle="Why items were kept/dropped, strictness & auto-lexicon that guided relevance"
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
        <div className="md:col-span-5 space-y-2">
          <div className="text-sm">
            <span className="text-zinc-400">Strictness:</span>{" "}
            <span className="font-medium">{meta?.strictness ?? "(not provided)"}</span>
          </div>
          <div className="text-sm">
            Kept <span className="font-medium">{meta?.kept_count ?? "—"}</span> of{" "}
            <span className="font-medium">{meta?.input_count ?? "—"}</span>
          </div>
          <Meter
            label="Kept ratio"
            value={percent(((meta?.kept_count || 0) / Math.max(1, meta?.input_count || 1)) * 100)}
            tone="green"
          />
          <div className="mt-2">
            <div className="mb-1 text-xs text-zinc-400">Dropped by reason</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(meta?.dropped_summary || {}).map(([k, v]) => (
                <Chip key={k}>
                  {k}: {String(v)}
                </Chip>
              ))}
              {Object.keys(meta?.dropped_summary || {}).length === 0 && <span className="text-xs text-zinc-500">None</span>}
            </div>
          </div>
        </div>
        <div className="md:col-span-7">
          <div className="mb-1 text-xs text-zinc-400">Auto-lexicon terms (steered relevance)</div>
          <div className="flex max-h-24 flex-wrap gap-2 overflow-auto">
            {(meta?.terms_used || []).slice(0, 50).map((t, i) => (
              <Badge key={i} tone="blue">
                {String(t)}
              </Badge>
            ))}
            {(meta?.terms_used || []).length === 0 && <span className="text-xs text-zinc-500">—</span>}
          </div>
          {analysis?.topics_debug && (
            <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2 text-xs text-zinc-300">
              <div className="mb-1 text-zinc-400">Clustering mode:</div>
              <pre className="whitespace-pre-wrap break-words">{JSON.stringify(analysis.topics_debug, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function PerReviewTable({ res, perReview }) {
  return (
    <Card title="Per-review" right={<Badge>table</Badge>} className="md:col-span-12">
      <div className="overflow-auto rounded-xl border border-zinc-800">
        <table className="min-w-full text-left text-sm">
          <thead className="sticky top-0 bg-zinc-900/90 text-zinc-400 backdrop-blur">
            <tr>
              <th className="px-3 py-2">Sentiment</th>
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2">Reason</th>
              <th className="px-3 py-2">Topics</th>
              <th className="px-3 py-2">Keyphrases</th>
              <th className="px-3 py-2">Text</th>
            </tr>
          </thead>
          <tbody>
            {perReview.map((r, i) => (
              <tr key={i} className="border-t border-zinc-800 odd:bg-zinc-900/40">
                <td className="px-3 py-2">
                  <Badge tone={toneForSentiment(r?.sentiment)}>{r?.sentiment ?? "—"}</Badge>
                </td>
                <td className="px-3 py-2">{r?.category ?? "—"}</td>
                <td className="px-3 py-2 max-w-xs truncate" title={r?.reason || ""}>
                  {r?.reason || "—"}
                </td>
                <td className="px-3 py-2">
                  <div className="flex max-w-xs flex-wrap gap-1">
                    {(r?.topics ?? []).map((t, j) => (
                      <Badge key={j}>{String(t)}</Badge>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div className="flex max-w-xs flex-wrap gap-1">
                    {(r?.keyphrases ?? []).slice(0, 6).map((k, j) => (
                      <Badge key={j}>{String(k)}</Badge>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2 max-w-3xl truncate" title={r?.text}>
                  {r?.text ?? ""}
                </td>
              </tr>
            ))}
            {!perReview.length && (
              <tr>
                <td className="px-3 py-6 text-center text-zinc-500" colSpan={6}>
                  {res ? "No rows (did filters eliminate everything?)" : "Run the pipeline to populate rows."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ===================== SCRAPER SANDBOX =====================
function ScraperPanel() {
  // YouTube
  const [ytQuery, setYtQuery] = useState("Tesla Model Y");
  const [ytMaxVideos, setYtMaxVideos] = useState(4);
  const [ytMaxComments, setYtMaxComments] = useState(60);
  const [ytAfter, setYtAfter] = useState("");
  const [ytBefore, setYtBefore] = useState("");
  const [ytStrict, setYtStrict] = useState("normal");
  const [ytRes, setYtRes] = useState(null);
  const [ytLoading, setYtLoading] = useState(false);

  // Reddit
  const [rdtSubs, setRdtSubs] = useState("TeslaMotors, electricvehicles");
  const [rdtQuery, setRdtQuery] = useState("Tesla Model Y");
  const [rdtLimit, setRdtLimit] = useState(80);
  const [rdtTime, setRdtTime] = useState("month");
  const [rdtStrict, setRdtStrict] = useState("normal");
  const [rdtRes, setRdtRes] = useState(null);
  const [rdtLoading, setRdtLoading] = useState(false);
  const [err, setErr] = useState(null);

  const runYT = async () => {
    setYtLoading(true);
    setErr(null);
    try {
      const { data } = await api.post("/reviews/scrape/youtube", {
        query: ytQuery,
        max_videos: clamp(ytMaxVideos, 1, 6),
        max_comments: clamp(ytMaxComments, 1, 100),
        published_after: ytAfter || null,
        published_before: ytBefore || null,
        strictness: ytStrict,
      });
      setYtRes(data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setYtLoading(false);
    }
  };

  const runReddit = async () => {
    setRdtLoading(true);
    setErr(null);
    try {
      const { data } = await api.post("/reviews/scrape/reddit", {
        subreddits: rdtSubs.split(",").map((s) => s.trim()).filter(Boolean),
        query: rdtQuery,
        limit: clamp(rdtLimit, 1, 200),
        time_filter: rdtTime,
        strictness: rdtStrict,
      });
      setRdtRes(data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setRdtLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="YouTube Scraper" right={<Badge>Sandbox</Badge>}>
        <div className="space-y-2">
          <input className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" value={ytQuery} onChange={(e) => setYtQuery(e.target.value)} />
          <div className="grid grid-cols-2 gap-2 text-sm">
            <label className="flex items-center gap-1">
              <span>max_videos</span>
              <input
                type="number"
                min={1}
                max={6}
                className="w-20 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                value={ytMaxVideos}
                onChange={(e) => setYtMaxVideos(e.target.value)}
                onBlur={(e) => setYtMaxVideos(clamp(e.target.value, 1, 6))}
              />
            </label>
            <label className="flex items-center gap-1">
              <span>max_comments</span>
              <input
                type="number"
                min={1}
                max={100}
                className="w-24 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                value={ytMaxComments}
                onChange={(e) => setYtMaxComments(e.target.value)}
                onBlur={(e) => setYtMaxComments(clamp(e.target.value, 1, 100))}
              />
            </label>
            <label className="flex items-center gap-1">
              <span>after</span>
              <input
                type="datetime-local"
                className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                value={ytAfter}
                onChange={(e) => setYtAfter(e.target.value)}
              />
            </label>
            <label className="flex items-center gap-1">
              <span>before</span>
              <input
                type="datetime-local"
                className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
                value={ytBefore}
                onChange={(e) => setYtBefore(e.target.value)}
              />
            </label>
            <label className="flex items-center gap-1">
              <span>strictness</span>
              <select value={ytStrict} onChange={(e) => setYtStrict(e.target.value)} className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1">
                <option>low</option>
                <option>normal</option>
                <option>high</option>
              </select>
            </label>
            <div className="flex items-center justify-end">
              <button onClick={runYT} disabled={ytLoading} className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
                {ytLoading ? "Running…" : "Scrape"}
              </button>
            </div>
          </div>
          {ytRes && (
            <div className="mt-2 space-y-2">
              <div className="text-sm text-zinc-400">videos: {(ytRes?.results ?? []).length}</div>
              <div className="max-h-64 overflow-auto rounded-lg border border-zinc-800">
                <table className="min-w-full text-left text-sm">
                  <thead>
                    <tr className="text-zinc-400">
                      <th className="px-2 py-1">video</th>
                      <th className="px-2 py-1">kept_comments (sample)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(ytRes?.results ?? []).map((v, i) => (
                      <tr key={i} className="border-t border-zinc-800">
                        <td className="px-2 py-1">
                          <div className="font-medium">{v?.title}</div>
                          <div className="text-xs text-zinc-400">
                            {v?.channel} · {v?.published}
                          </div>
                        </td>
                        <td className="px-2 py-1">
                          <ul className="list-disc pl-4 text-xs">
                            {(v?.kept_comments ?? []).slice(0, 5).map((c, j) => (
                              <li key={j} className="mb-1">
                                {c}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </Card>

      <Card title="Reddit Scraper" right={<Badge>Sandbox</Badge>}>
        <div className="space-y-2">
          <input className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" value={rdtSubs} onChange={(e) => setRdtSubs(e.target.value)} />
          <input className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" value={rdtQuery} onChange={(e) => setRdtQuery(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-2 text-sm mt-2">
          <label className="flex items-center gap-1">
            <span>limit</span>
            <input
              type="number"
              min={1}
              max={200}
              className="w-24 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
              value={rdtLimit}
              onChange={(e) => setRdtLimit(e.target.value)}
              onBlur={(e) => setRdtLimit(clamp(e.target.value, 1, 200))}
            />
          </label>
          <label className="flex items-center gap-1">
            <span>time</span>
            <select value={rdtTime} onChange={(e) => setRdtTime(e.target.value)} className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1">
              {["hour", "day", "week", "month", "year", "all"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1">
            <span>strictness</span>
            <select value={rdtStrict} onChange={(e) => setRdtStrict(e.target.value)} className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1">
              <option>low</option>
              <option>normal</option>
              <option>high</option>
            </select>
          </label>
          <div className="flex items-center justify-end">
            <button onClick={async () => {
              setRdtLoading(true);
              setErr(null);
              try {
                const { data } = await api.post("/reviews/scrape/reddit", {
                  subreddits: rdtSubs.split(",").map((s) => s.trim()).filter(Boolean),
                  query: rdtQuery,
                  limit: clamp(rdtLimit, 1, 200),
                  time_filter: rdtTime,
                  strictness: rdtStrict,
                });
                setRdtRes(data);
              } catch (e) {
                const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
                setErr(msg);
              } finally {
                setRdtLoading(false);
              }
            }} disabled={rdtLoading} className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
              {rdtLoading ? "Running…" : "Scrape"}
            </button>
          </div>
        </div>
        {rdtRes && (
          <div className="mt-2 space-y-2">
            <div className="text-sm text-zinc-400">kept_count: {rdtRes?.kept_count ?? "—"}</div>
            <div className="max-h-64 overflow-auto rounded-lg border border-zinc-800">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="text-zinc-400">
                    <th className="px-2 py-1">kept_comments (sample)</th>
                  </tr>
                </thead>
                <tbody>
                  {(rdtRes?.kept_comments ?? []).slice(0, 25).map((c, i) => (
                    <tr key={i} className="border-t border-zinc-800">
                      <td className="px-2 py-1 text-xs">{c}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Card title="Dropped by reason">
              <pre className="max-h-40 overflow-auto text-xs">{JSON.stringify(rdtRes?.dropped_by_reason ?? {}, null, 2)}</pre>
            </Card>
          </div>
        )}
        <ErrorToast error={err} onClose={() => setErr(null)} />
      </Card>
    </div>
  );
}

// ===================== ANALYZER =====================
function AnalyzerPanel() {
  const [text, setText] = useState("");
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const [query, setQuery] = useState("");
  const [strictness, setStrictness] = useState("normal");

  const run = async () => {
    setLoading(true);
    setErr(null);
    try {
      const reviews = text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
      const data = await analyzeReviews({ reviews, query: query || null, strictness });
      setRes(data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setLoading(false);
    }
  };

  const meta = res?.meta || {};
  const overview = res?.overview || {};
  const exec = res?.executive_summary || {};
  const canonical = overview?.canonical_clusters ?? [];
  const clusterSuggestions = overview?.cluster_suggestions ?? [];
  const mergedClusters = useMemo(() => mergeClusterSuggestions(canonical, clusterSuggestions), [canonical, clusterSuggestions]);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="Input (one per line)" right={<Badge>Analyzer</Badge>}>
        <textarea
          rows={12}
          className="h-64 w-full rounded-xl border border-zinc-700 bg-zinc-900 p-3 text-sm outline-none focus:ring-2 focus:ring-blue-700"
          placeholder={`Love the ride quality but software feels buggy\nCharging network is a lifesaver\nPrice is too high for features`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run(); }}
        />
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3">
          <input
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
            placeholder="optional query (improves relevance)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            value={strictness}
            onChange={(e) => setStrictness(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
          >
            <option value="low">strictness: low</option>
            <option value="normal">strictness: normal</option>
            <option value="high">strictness: high</option>
          </select>
          <div className="flex justify-end">
            <button onClick={run} disabled={loading} className="w-full rounded-xl bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50">
              {loading ? "Analyzing…" : "Analyze"}
            </button>
          </div>
        </div>
      </Card>

      <Card title="Overview">
        {!res && <div className="text-sm text-zinc-500">Run the analyzer to view results.</div>}
        {res && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <div className="text-zinc-400">Average sentiment</div>
                <div className="text-lg">{overview?.average_sentiment?.toFixed?.(2) ?? "—"}</div>
              </div>
              <div>
                <div className="text-zinc-400">Top keyphrases</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(overview?.top_keyphrases ?? []).slice(0, 16).map((k, i) => (
                    <Badge key={i}>{String(k)}</Badge>
                  ))}
                </div>
              </div>
            </div>

            {(meta?.kept_count !== undefined || Object.keys(meta?.dropped_summary || {}).length > 0) && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
                <div className="mb-1 text-sm font-medium text-zinc-100">Model Intelligence</div>
                <div className="text-xs text-zinc-400 mb-2">
                  strictness <span className="font-medium text-zinc-200">{meta?.strictness ?? strictness}</span> · kept{" "}
                  <span className="font-medium text-zinc-200">{meta?.kept_count ?? "—"}</span> of{" "}
                  <span className="font-medium text-zinc-200">{meta?.input_count ?? "—"}</span>
                </div>
                <Meter value={percent(((meta?.kept_count || 0) / Math.max(1, meta?.input_count || 1)) * 100)} tone="green" label="Kept ratio" />
                <div className="mt-2">
                  <div className="mb-1 text-xs text-zinc-400">Dropped by reason</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(meta?.dropped_summary || {}).map(([k, v]) => (
                      <Chip key={k}>
                        {k}: {String(v)}
                      </Chip>
                    ))}
                    {Object.keys(meta?.dropped_summary || {}).length === 0 && <span className="text-xs text-zinc-500">None</span>}
                  </div>
                </div>
                <div className="mt-3">
                  <div className="mb-1 text-xs text-zinc-400">Auto-lexicon terms</div>
                  <div className="flex max-h-24 flex-wrap gap-2 overflow-auto">
                    {(meta?.terms_used || []).slice(0, 50).map((t, i) => (
                      <Badge key={i} tone="blue">
                        {String(t)}
                      </Badge>
                    ))}
                    {(meta?.terms_used || []).length === 0 && <span className="text-xs text-zinc-500">—</span>}
                  </div>
                </div>
              </div>
            )}

            {/* Canonical Clusters */}
            {mergedClusters?.length > 0 && (
              <div className="mt-3">
                <div className="mb-2 text-sm font-medium text-zinc-100">Canonical Clusters</div>
                <div className="grid grid-cols-1 gap-3">
                  {mergedClusters.map((c) => (
                    <ClusterCard key={c.cluster_id} c={c} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Card>
      <ErrorToast error={err} onClose={() => setErr(null)} />
    </div>
  );
}

// ===================== UNDERSTAND =====================
function UnderstandPanel() {
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const onFile = async (f) => {
    setLoading(true);
    setErr(null);
    try {
      const data = await uploadCSV("/understand/upload", f);
      setRes(data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="Upload CSV" right={<Badge>Understand</Badge>}>
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
          className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
        />
        {loading && <div className="mt-2 text-sm text-zinc-400">Uploading…</div>}
      </Card>
      <Card title="Profile / Preview">
        {!res && <div className="text-sm text-zinc-500">Upload a CSV to see guessed columns & preview.</div>}
        {res && (
          <div className="space-y-3 text-sm">
            <div>
              <div className="text-zinc-400">Guessed columns</div>
              <div className="mt-1 flex flex-wrap gap-2">
                {Object.entries(res?.guessed_columns ?? {}).map(([k, v]) => (
                  <Badge key={k} tone="blue">
                    {k}: {String(v)}
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <div className="text-zinc-400">Columns</div>
              <div className="mt-1 flex flex-wrap gap-2">
                {(res?.columns ?? []).map((c, i) => (
                  <Badge key={i}>{String(c)}</Badge>
                ))}
              </div>
            </div>
            <div className="max-h-64 overflow-auto rounded-lg border border-zinc-800">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="text-zinc-400">
                    {(res?.columns ?? []).map((c, i) => (
                      <th key={i} className="px-2 py-1">
                        {String(c)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(res?.preview ?? []).map((row, i) => (
                    <tr key={i} className="border-t border-zinc-800">
                      {(res?.columns ?? []).map((c, j) => (
                        <td key={j} className="px-2 py-1 text-xs">
                          {String(row?.[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {res?.report_path && (
              <a className="inline-block rounded-lg bg-zinc-800 px-3 py-1 text-xs hover:bg-zinc-700" href={res.report_path} target="_blank" rel="noreferrer">
                Open Profile Report
              </a>
            )}
          </div>
        )}
      </Card>
      <ErrorToast error={err} onClose={() => setErr(null)} />
    </div>
  );
}

// ===================== FORECAST =====================
function ForecastPanel() {
  const [periods, setPeriods] = useState(30);
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const onFile = async (f) => {
    setLoading(true);
    setErr(null);
    try {
      const data = await uploadCSV(`/forecast/predict?periods=${encodeURIComponent(periods)}`, f);
      setRes(data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(msg);
    } finally {
      setLoading(false);
    }
  };

  const chartData = useMemo(() => res?.forecast ?? [], [res]);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-5">
      <Card title="Upload CSV" right={<Badge>Forecast</Badge>}>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-sm">
            <span>periods</span>
            <input
              type="number"
              min={1}
              max={365}
              value={periods}
              onChange={(e) => setPeriods(e.target.value)}
              onBlur={(e) => setPeriods(clamp(e.target.value, 1, 365))}
              className="w-24 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1"
            />
          </label>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
          />
        </div>
        {loading && <div className="mt-2 text-sm text-zinc-400">Uploading…</div>}
      </Card>
      <div className="md:col-span-4">
        <Card title="Forecast Chart">
          {chartData.length ? (
            <div className="h-64 w-full">
              <ResponsiveContainer>
                <AreaChart data={chartData} margin={{ left: 8, right: 8, top: 8, bottom: 8 }}>
                  <defs>
                    <linearGradient id="yhat" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopOpacity={0.5} />
                      <stop offset="95%" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="ds" minTickGap={24} />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Area type="monotone" dataKey="yhat_lower" name="Lower" fillOpacity={0.15} />
                  <Area type="monotone" dataKey="yhat_upper" name="Upper" fillOpacity={0.1} />
                  <Line type="monotone" dataKey="yhat" name="yhat" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="text-sm text-zinc-500">Upload a CSV to render the forecast.</div>
          )}
        </Card>
        {res && (
          <Card title="Forecast Table">
            <div className="max-h-64 overflow-auto rounded-lg border border-zinc-800">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="text-zinc-400">
                    <th className="px-2 py-1">ds</th>
                    <th className="px-2 py-1">yhat</th>
                    <th className="px-2 py-1">lower</th>
                    <th className="px-2 py-1">upper</th>
                  </tr>
                </thead>
                <tbody>
                  {chartData.map((r, i) => (
                    <tr key={i} className="border-t border-zinc-800">
                      <td className="px-2 py-1 text-xs">{String(r?.ds ?? "")}</td>
                      <td className="px-2 py-1 text-xs">{String(r?.yhat ?? "")}</td>
                      <td className="px-2 py-1 text-xs">{String(r?.yhat_lower ?? "")}</td>
                      <td className="px-2 py-1 text-xs">{String(r?.yhat_upper ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
      <ErrorToast error={err} onClose={() => setErr(null)} />
    </div>
  );
}

// ===================== APP SHELL =====================
export default function App() {
  const [tab, setTab] = useState("Insights");
  // Landing page gate — recruiters see the landing page first, then enter the app.
  const [entered, setEntered] = useState(false);
  // Cross-tab state
  const [compareIds, setCompareIds] = useState(null);   // [idA, idB] when user picks runs in History
  const [rerunSeed, setRerunSeed] = useState(null);     // Pre-fill InsightsPanel with these params

  // Hook from LandingPage → enter the dashboard and either load a pre-cached
  // featured run by id (instant) or kick off a live Balanced run for a free search.
  const onLaunch = ({ query, runId }) => {
    if (runId) {
      setRerunSeed({ loadRunId: runId, query, analysis_depth: "balanced" });
    } else if (query) {
      setRerunSeed({ query, autoRun: true, analysis_depth: "balanced" });
    }
    setTab("Insights");
    setEntered(true);
  };

  // Hook from HistoryPanel → jump to Compare tab with two preselected runs
  const onCompareSelect = (ids) => {
    setCompareIds(ids);
    setTab("Compare");
  };

  // Hook from HistoryPanel → jump back to Insights with the same params
  const onRerun = (run) => {
    const meta = run?.report?.meta || {};
    setRerunSeed({
      mode: meta.user_mode || run.user_mode,
      query: meta.query_used || run.query || "",
      filepath: run.filepath || null,
      platforms: run.platforms || ["youtube", "reddit"],
      strictness: meta.strictness || "normal",
      time_from: meta.time_from || null,
      time_to: meta.time_to || null,
    });
    setTab("Insights");
  };

  if (!entered) {
    return <LandingPage onLaunch={onLaunch} />;
  }

  return (
    <div className="mx-auto max-w-7xl p-4 text-zinc-100">
      <header className="mb-4 flex items-center justify-between">
        <button
          onClick={() => setEntered(false)}
          className="text-xl font-semibold hover:text-blue-300"
          title="Back to home"
        >
          InsightMesh AI
        </button>
        <div className="text-xs text-zinc-400">v0.2 · React + Tailwind · /api/*</div>
      </header>
      <Tabs
        tabs={["Insights", "History", "Compare", "Scraper", "Analyzer", "Understand", "Forecast"]}
        value={tab}
        onChange={(t) => { setTab(t); if (t !== "Compare") setCompareIds(null); }}
      />
      {tab === "Insights" && <Insights rerunSeed={rerunSeed} onRerunConsumed={() => setRerunSeed(null)} />}
      {tab === "History" && <HistoryPanel onRerun={onRerun} onCompareSelect={onCompareSelect} />}
      {tab === "Compare" && <ComparePanel initialIds={compareIds} />}
      {tab === "Scraper" && <ScraperPanel />}
      {tab === "Analyzer" && <AnalyzerPanel />}
      {tab === "Understand" && <UnderstandPanel />}
      {tab === "Forecast" && <ForecastPanel />}
      <footer className="mt-8 text-center text-xs text-zinc-500">
        Dual-input insights • Run history • Side-by-side compare • Markdown/HTML export
      </footer>
    </div>
  );
}
