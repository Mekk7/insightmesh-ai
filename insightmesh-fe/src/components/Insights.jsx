import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, downloadBlob, exportReportHtml, exportReportMd, streamPipeline, runPaste, fetchRun } from "../lib/api.js";
import ProgressStrip from "./ProgressStrip.jsx";
import AskInsightMesh from "./AskInsightMesh.jsx";
import Watchlist, { WatchlistButton } from "./Watchlist.jsx";
import DashboardSkeleton from "./DashboardSkeleton.jsx";
import ReviewsBrowser from "./ReviewsBrowser.jsx";
import DebatePanel from "./DebatePanel.jsx";

const STORAGE_KEY = "insightmesh_ui_v3";

const SAMPLE_PRODUCTS = [
  { key: "tesla", label: "Tesla Model Y", category: "EV" },
  { key: "sony", label: "Sony WH-1000XM6", category: "Audio" },
  { key: "vision", label: "Apple Vision Pro", category: "XR" },
];

const COLORS = {
  blue: "#3b82f6",
  emerald: "#10b981",
  amber: "#f59e0b",
  rose: "#f43f5e",
  indigo: "#6366f1",
  violet: "#8b5cf6",
  cyan: "#06b6d4",
  slate: "#64748b",
};

const EMOTION_COLORS = {
  // Real classifier labels (after presentation mapping in the backend)
  Delighted: COLORS.emerald,
  Excited: COLORS.cyan,
  Hopeful: COLORS.amber,
  Curious: COLORS.indigo,
  Neutral: COLORS.slate,
  Disappointed: COLORS.violet,
  Worried: COLORS.indigo,
  Frustrated: COLORS.rose,
  Disgusted: COLORS.rose,
};

const clamp = (n, lo, hi) => {
  const v = Number(n);
  if (Number.isNaN(v)) return lo;
  return Math.max(lo, Math.min(hi, v));
};

const nowISOShort = () => new Date().toISOString().replace("T", " ").slice(0, 19);

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

function Card({ title, subtitle, right, children, className = "", padded = true }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/60 ${padded ? "p-5" : ""} shadow-sm ${className}`}>
      {(title || right) && (
        <div className={`mb-3 flex items-center justify-between gap-2 ${padded ? "" : "px-5 pt-5"}`}>
          <div>
            {title && <h3 className="text-base font-medium text-zinc-100">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
          </div>
          {right && <div className="flex items-center gap-2">{right}</div>}
        </div>
      )}
      <div className={`text-sm text-zinc-300 ${padded ? "" : "px-5 pb-5"}`}>{children}</div>
    </div>
  );
}

function Pill({ children, onClick, active = false, danger = false, disabled = false, title }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-50 ${
        danger
          ? "border-rose-800 bg-rose-900/30 text-rose-200 hover:bg-rose-900/50"
          : active
          ? "border-blue-700 bg-blue-900/40 text-blue-100"
          : "border-zinc-700 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
      }`}
    >
      {children}
    </button>
  );
}

function Badge({ children, tone = "zinc" }) {
  const tones = {
    zinc: "bg-zinc-800 text-zinc-200 border-zinc-700",
    green: "bg-emerald-900/30 text-emerald-200 border-emerald-800",
    red: "bg-rose-900/30 text-rose-200 border-rose-800",
    amber: "bg-amber-900/30 text-amber-200 border-amber-800",
    blue: "bg-blue-900/30 text-blue-200 border-blue-800",
    indigo: "bg-indigo-900/30 text-indigo-200 border-indigo-800",
    violet: "bg-violet-900/30 text-violet-200 border-violet-800",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>;
}

function StatTile({ label, value, sub, tone = "zinc", trend = null }) {
  const tones = {
    zinc: "border-zinc-800",
    green: "border-emerald-800/60",
    red: "border-rose-800/60",
    amber: "border-amber-800/60",
    blue: "border-blue-800/60",
  };
  return (
    <div className={`rounded-xl border ${tones[tone] || tones.zinc} bg-zinc-900/40 p-3.5`}>
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-medium text-zinc-100">{value ?? "—"}</span>
        {trend && <span className={`text-xs ${trend.up ? "text-emerald-400" : "text-rose-400"}`}>{trend.up ? "↑" : "↓"} {trend.label}</span>}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-zinc-500">{sub}</div>}
    </div>
  );
}

// Per-insight confidence pill, fed by the backend Evidence Engine's `_evidence`
// block. Green ≥0.7, amber 0.4-0.7, zinc <0.4. Shows a ⚠ when opinions on the
// insight are polarized. Renders nothing when there's no evidence to show.
function ConfidenceBadge({ evidence, className = "" }) {
  if (!evidence || typeof evidence.confidence !== "number") return null;
  const { confidence, confidence_label, review_count, conflict } = evidence;
  const tone =
    confidence >= 0.7
      ? "border-emerald-700/60 bg-emerald-950/40 text-emerald-300"
      : confidence >= 0.4
      ? "border-amber-700/60 bg-amber-950/30 text-amber-300"
      : "border-zinc-700 bg-zinc-900 text-zinc-400";
  const rc = typeof review_count === "number" ? review_count : null;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono ${tone} ${className}`}
      title={`Confidence ${confidence.toFixed(2)}${rc != null ? ` · based on ${rc} review${rc === 1 ? "" : "s"}` : ""}${conflict?.polarized ? " · opinions are polarized" : ""}`}
    >
      {confidence_label && <span className="font-semibold">{confidence_label}</span>}
      <span>{confidence.toFixed(2)}</span>
      {rc != null && <span className="opacity-70">· {rc}</span>}
      {conflict?.polarized && <span className="text-amber-400" aria-label="polarized">⚠</span>}
    </span>
  );
}

// Cross-section insight callout, fed by the backend Intelligence Synthesizer's
// `overview.cross_insights`. These are connections no single section sees alone
// (dominant theme, polarization, confidence/quality mismatch, temporal-cluster
// links, high-quality minority signals). Styled by severity. Renders nothing for
// unknown/empty insights.
const CROSS_INSIGHT_STYLES = {
  warning: { border: "border-amber-700/50", bg: "bg-amber-950/20", text: "text-amber-300", icon: "⚠" },
  insight: { border: "border-violet-700/50", bg: "bg-violet-950/20", text: "text-violet-300", icon: "💡" },
  info:    { border: "border-blue-700/50",  bg: "bg-blue-950/20",  text: "text-blue-300",  icon: "ℹ" },
};

function CrossInsight({ insight, className = "" }) {
  if (!insight || !insight.description) return null;
  const s = CROSS_INSIGHT_STYLES[insight.severity] || CROSS_INSIGHT_STYLES.info;
  return (
    <div className={`flex items-start gap-2.5 rounded-xl border ${s.border} ${s.bg} px-3.5 py-2.5 ${className}`}>
      <span className={`mt-0.5 shrink-0 text-sm ${s.text}`} aria-hidden>{s.icon}</span>
      <div className="min-w-0">
        <p className="text-xs leading-relaxed text-zinc-200">{insight.description}</p>
        {Array.isArray(insight.sections) && insight.sections.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {insight.sections.map((sec) => (
              <span key={sec} className="rounded bg-zinc-800/70 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-zinc-500">
                {String(sec).replace(/_/g, " ")}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Review-quality chip, fed by the Intelligence Synthesizer's per-review
// `_intelligence` block (composite 0-1 + HIGH/MEDIUM/LOW label). Falls back to
// the legacy flat `quality` field when intelligence scoring is absent.
function reviewIntelligence(r) {
  const intel = r && r._intelligence;
  if (intel && typeof intel.composite === "number") return intel;
  if (r && typeof r.quality === "number") {
    const c = r.quality;
    return { composite: c, label: c >= 0.5 ? "HIGH" : c >= 0.3 ? "MEDIUM" : "LOW", _legacy: true };
  }
  return null;
}

function lastNDaysISO(days) {
  const now = new Date();
  const past = new Date(now.getTime() - days * 86400000);
  return { from: past.toISOString().slice(0, 16), to: now.toISOString().slice(0, 16) };
}

const STAR_TO_NUM = { "1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5 };

function buildPlatformSentimentSeries(contributions) {
  const rows = (contributions?.per_platform || []).map((p) => ({
    platform: (p.platform || "?").toUpperCase(),
    avgSent: typeof p.avg_sentiment_score === "number" ? Number(p.avg_sentiment_score.toFixed(3)) : 0,
    used: p.used || 0,
    share: p["share_%"] || 0,
  }));
  return rows;
}

function buildEmotionSeries(emotionMix) {
  if (!emotionMix || typeof emotionMix !== "object") return [];
  return Object.entries(emotionMix)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: k, value: v }));
}

function buildLanguageList(langDist) {
  if (!langDist || typeof langDist !== "object") return [];
  return Object.entries(langDist)
    .sort((a, b) => b[1] - a[1])
    .map(([code, count]) => ({ code: code.toUpperCase(), count }));
}

function pickRepresentativeComments(perReview, limit = 3) {
  if (!Array.isArray(perReview) || perReview.length === 0) return [];
  const want = new Set(["Praise", "Complaint", "Suggestion"]);
  const buckets = { Praise: null, Complaint: null, Suggestion: null };
  // Pre-sort once by intelligence composite (falls back to legacy `quality`)
  // descending so each bucket picks the most informative representative.
  const score = (r) => {
    const i = r && r._intelligence;
    if (i && typeof i.composite === "number") return i.composite;
    return Number(r?.quality) || 0;
  };
  // Secondary sort: within the same intelligence level, the most CREDIBLE
  // reviewer (ownership + detail) wins — so VoC leads with credible AND informative.
  const credScore = (r) => {
    const c = r && r._credibility;
    return c && typeof c.credibility === "number" ? c.credibility : 0;
  };
  const ranked = [...perReview].sort(
    (a, b) => (score(b) - score(a)) || (credScore(b) - credScore(a))
  );
  for (const r of ranked) {
    const cat = r?.review_category;
    if (want.has(cat) && !buckets[cat]) buckets[cat] = r;
    if (Object.values(buckets).every(Boolean)) break;
  }
  const picked = Object.values(buckets).filter(Boolean).slice(0, limit);
  if (picked.length < limit) {
    for (const r of ranked) {
      if (picked.length >= limit) break;
      if (!picked.includes(r)) picked.push(r);
    }
  }
  return picked.slice(0, limit);
}

function buildCustomerVerdict({ overview, n, customerMode }) {
  if (!overview) return null;
  const avg = overview.average_sentiment;
  const mood = overview.mood_index;
  const clusters = overview.canonical_clusters || [];
  const wishes = overview.customer_wishes || [];
  const topConcern = clusters.find((c) => (c.solution?.high_risk || (c["share_%"] || 0) >= 18)) || clusters[0];
  const topWish = wishes[0];

  if (customerMode) {
    if (!topConcern && !topWish) return `Limited signal from ${n} reviews — directional only.`;
    const lead = avg >= 4.2 ? "Strong buy with eyes open." : avg >= 3.5 ? "Cautious buy." : avg >= 3.0 ? "Wait and watch." : "Skip for now.";
    const tail = topConcern ? ` Top concern: ${topConcern.reason}.` : "";
    const wishTail = topWish ? ` Existing owners commonly ask for: "${topWish.wish}".` : "";
    return `${lead}${tail}${wishTail}`;
  }

  if (!topConcern && !topWish) return `Not enough signal yet — collect more reviews to surface patterns.`;
  const moodTxt = typeof mood === "number" ? `mood index ${mood >= 0 ? "+" : ""}${mood.toFixed(2)}` : "mood unmeasured";
  const concernTxt = topConcern ? ` Biggest complaint to address: "${topConcern.reason}" (${topConcern["share_%"] || 0}% of reviews).` : "";
  const wishTxt = topWish ? ` Top feature request: "${topWish.wish}" — ${topWish.count} mentions.` : "";
  return `${n} reviews analyzed, ${moodTxt}.${concernTxt}${wishTxt}`;
}

const tooltipStyle = {
  backgroundColor: "#18181b",
  border: "1px solid #3f3f46",
  borderRadius: "8px",
  color: "#e4e4e7",
  fontSize: "12px",
};

export default function Insights({ rerunSeed = null, onRerunConsumed = () => {} }) {
  const saved = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; }
  }, []);

  const [mode, setMode] = useState(saved.mode || "customer");
  const [inputMode, setInputMode] = useState(saved.inputMode || "search");
  const [query, setQuery] = useState("");
  const [platforms, setPlatforms] = useState(saved.platforms || { youtube: true, reddit: true, appstore: false });
  const [strictness, setStrictness] = useState(saved.strictness || "normal");
  const [analysisDepth, setAnalysisDepth] = useState(saved.analysisDepth || "balanced");
  const [timeFrom, setTimeFrom] = useState(saved.timeFrom || "");
  const [timeTo, setTimeTo] = useState(saved.timeTo || "");
  const [debug, setDebug] = useState(Boolean(saved.debug) || false);
  // Default streaming ON: the stream pipeline renders the dashboard the moment the
  // main analysis is ready (~60-90s) and fills in deep signals in the background,
  // whereas the non-stream endpoint blocks the whole request. Respect a saved choice.
  const [stream, setStream] = useState(saved.stream !== undefined ? Boolean(saved.stream) : true);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [uploadedPath, setUploadedPath] = useState(saved.uploadedPath || "");
  const [csvUploading, setCsvUploading] = useState(false);

  // Paste-to-analyze (the universal, unblockable source)
  const [pasteText, setPasteText] = useState("");
  const [pasteProduct, setPasteProduct] = useState("");

  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [lastRunAt, setLastRunAt] = useState(null);
  const [streamEvents, setStreamEvents] = useState([]);
  const [demoMode, setDemoMode] = useState(false);

  // ---- Request cancellation (Phase A.2) ----
  // Every API call gets an AbortController signal. Before any new request fires,
  // we abort the previous one so the user's LAST action is always the one in flight.
  // Mirrors what Google/ChatGPT/Spotify do: instant cancel + start, no queue, no
  // confirmation. We also signal the backend (via axios + fetch signal) so the
  // server stops doing wasted scraping / LLM work on dropped requests.
  //
  // Mode switch (Customer ↔ Company) is intentionally decoupled — it's a pure
  // view change of the same underlying data, so we do NOT cancel on mode switch.
  // That's industry standard (Notion view types, Google All↔Images, Linear
  // filters). If a request is in flight when the user toggles, its result will
  // simply render in the new mode when it arrives. To explicitly cancel,
  // the user can clear the input (× button), type a new search, or use the
  // visible Stop button surfaced while loading.
  const abortControllerRef = useRef(null);
  const [cancelToast, setCancelToast] = useState(null);
  const cancelToastTimerRef = useRef(null);

  const abortInflight = useCallback(() => {
    const ctl = abortControllerRef.current;
    if (!ctl) return false;
    try { ctl.abort(); } catch {}
    abortControllerRef.current = null;
    // Quick toast so the user knows their previous click was respected
    if (cancelToastTimerRef.current) clearTimeout(cancelToastTimerRef.current);
    setCancelToast({ at: Date.now() });
    cancelToastTimerRef.current = setTimeout(() => setCancelToast(null), 1400);
    return true;
  }, []);

  // Detects axios CanceledError + fetch AbortError + various legacy names
  const isAbortError = (e) =>
    e?.name === "CanceledError" ||
    e?.name === "AbortError" ||
    e?.code === "ERR_CANCELED" ||
    (typeof e?.message === "string" && /aborted|canceled|cancelled/i.test(e.message));

  // Cleanup any inflight request on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        try { abortControllerRef.current.abort(); } catch {}
      }
      if (cancelToastTimerRef.current) clearTimeout(cancelToastTimerRef.current);
    };
  }, []);

  // ---- Stale-result guard ----
  // The query box and the displayed result must stay in sync. If the user
  // changes the query after a result was shown, we mark the result as stale
  // and the dashboard either disables it or shows a clear banner. This prevents
  // the "I searched Tesla and got Sony" class of bugs.
  const resultQuery = res?.meta?.query_used || null;
  const isStale = !!(res && resultQuery && inputMode === "search" && query.trim() && query.trim().toLowerCase() !== resultQuery.trim().toLowerCase());

  useEffect(() => {
    // Persist UI preferences but NOT the query text — keeping `query` in localStorage
    // means the next visit restores a value that may not match anything on screen.
    const out = { mode, inputMode, platforms, strictness, analysisDepth, timeFrom, timeTo, debug, stream, uploadedPath };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(out)); } catch {}
  }, [mode, inputMode, platforms, strictness, timeFrom, timeTo, debug, stream, uploadedPath]);

  // Holds a query string when a seed asks us to auto-run a live analysis once the
  // seed-driven state (query/depth) has actually been applied to the DOM.
  const pendingAutoRunRef = useRef(null);

  useEffect(() => {
    if (!rerunSeed) return;

    // --- Featured product: load the saved STATIC report (no backend call) ---
    // Reads /featured/<slug>.json straight from the site (Vercel static). This is
    // redeploy-proof and never triggers scraping/analysis. Errors surface cleanly.
    if (rerunSeed.loadReportUrl) {
      setInputMode("search");
      if (rerunSeed.query) setQuery(rerunSeed.query);
      if (rerunSeed.analysis_depth) setAnalysisDepth(rerunSeed.analysis_depth);
      setErr(null);
      setDemoMode(false);
      setRes(null);
      setLoading(true);
      const _label = rerunSeed.query || "this product";
      (async () => {
        try {
          const resp = await fetch(rerunSeed.loadReportUrl, { headers: { Accept: "application/json" } });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const report = await resp.json();
          if (!report.meta) report.meta = {};
          if (rerunSeed.query) report.meta.query_used = rerunSeed.query;
          setRes(report);
          setLastRunAt(nowISOShort());
        } catch (e) {
          setErr(`Couldn't load the saved result for "${_label}" (${e?.message || "request failed"}).`);
        } finally {
          setLoading(false);
        }
      })();
      onRerunConsumed();
      return;
    }

    // --- Featured / saved run by id (legacy): load the persisted report INSTANTLY ---
    if (rerunSeed.loadRunId) {
      setInputMode("search");
      if (rerunSeed.query) setQuery(rerunSeed.query);
      if (rerunSeed.analysis_depth) setAnalysisDepth(rerunSeed.analysis_depth);
      setErr(null);
      setDemoMode(false);
      setRes(null);
      setLoading(true);
      const _label = rerunSeed.query || `run #${rerunSeed.loadRunId}`;
      (async () => {
        try {
          const row = await fetchRun(rerunSeed.loadRunId);
          const report = row?.report || row?.final_report || null;
          if (report) {
            if (!report.meta) report.meta = {};
            if (rerunSeed.query) report.meta.query_used = rerunSeed.query;
            setRes(report);
            setLastRunAt(nowISOShort());
          } else {
            // Featured cards load SAVED results only — never silently trigger a
            // live re-analysis. If the saved run is unavailable, surface an error
            // and let the user run a fresh analysis from the search box.
            setErr(`Saved result for "${_label}" is unavailable. Type the product name above to run a fresh analysis.`);
          }
        } catch (e) {
          const code = e?.response?.status;
          setErr(`Couldn't load the saved result for "${_label}"${code ? ` (HTTP ${code})` : ""}. Type the product name above to run a fresh analysis.`);
        } finally {
          setLoading(false);
        }
      })();
      onRerunConsumed();
      return;
    }

    if (rerunSeed.mode === "company" && rerunSeed.filepath) {
      setInputMode("upload");
      setUploadedPath(rerunSeed.filepath);
    } else {
      setInputMode("search");
      if (rerunSeed.query) setQuery(rerunSeed.query);
    }
    if (rerunSeed.strictness) setStrictness(rerunSeed.strictness);
    if (rerunSeed.analysis_depth) setAnalysisDepth(rerunSeed.analysis_depth);
    if (rerunSeed.time_from) setTimeFrom(rerunSeed.time_from);
    if (rerunSeed.time_to) setTimeTo(rerunSeed.time_to);
    if (Array.isArray(rerunSeed.platforms)) {
      setPlatforms({
        youtube: rerunSeed.platforms.includes("youtube"),
        reddit: rerunSeed.platforms.includes("reddit"),
        appstore: rerunSeed.platforms.includes("appstore"),
      });
    }
    // Free-text search from the landing page: kick off a live run once state settles.
    if (rerunSeed.autoRun && rerunSeed.query) {
      pendingAutoRunRef.current = rerunSeed.query;
    }
    onRerunConsumed();
  }, [rerunSeed]);

  // Fire the deferred live run only after the seed's query/depth have been applied,
  // so run() reads the new values instead of stale closure state.
  useEffect(() => {
    const pending = pendingAutoRunRef.current;
    if (pending === null) return;
    if (inputMode !== "search") return;
    if (query.trim() && query.trim() === pending.trim()) {
      pendingAutoRunRef.current = null;
      if (runRef.current) runRef.current();
    }
  }, [query, inputMode, analysisDepth]);

  const selectedPlatforms = useMemo(
    () => Object.entries(platforms).filter(([, v]) => v).map(([k]) => k),
    [platforms]
  );

  const runRef = useRef(null);
  const runPasteRef = useRef(null);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        // Route the shortcut to the handler matching the active input mode.
        if (inputMode === "paste") { if (runPasteRef.current) runPasteRef.current(); }
        else if (runRef.current) runRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inputMode]);

  const buildBody = (over = {}) => ({
    input_mode: inputMode === "upload" ? "company" : "consumer",
    filepath: inputMode === "upload" ? uploadedPath : null,
    platforms: selectedPlatforms,
    mode: "fast",
    query_override: inputMode === "search" ? query : null,
    time_from: timeFrom || null,
    time_to: timeTo || null,
    strictness: strictness || null,
    analysis_depth: analysisDepth || "balanced",
    debug: !!debug,
    platform_settings: null,
    ...over,
  });

  const _looksLikeMissingCreds = (e) => {
    const status = e?.response?.status;
    if (status === 503) return true;
    const blob = JSON.stringify(e?.response?.data || {}) + " " + (e?.message || "");
    return /not configured|client not available|API_KEY|missing_credentials|YouTube client|Reddit client/i.test(blob);
  };

  const _queryToDemoKey = (q) => {
    const t = (q || "").toLowerCase();
    if (t.includes("sony") || t.includes("xm6") || t.includes("headphone")) return "sony";
    if (t.includes("vision") || t.includes("apple")) return "vision";
    return "tesla";
  };

  const loadDemo = async (productKey) => {
    // Cancel any prior inflight request before starting a new one
    abortInflight();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    // Clear stale dashboard immediately so the skeleton shows during the wait
    setRes(null);
    const key = productKey || _queryToDemoKey(query);
    setLoading(true);
    setErr(null);
    try {
      const { data } = await api.get(
        `/demo/report?product=${encodeURIComponent(key)}`,
        { signal: controller.signal }
      );
      // Late-arriving response from a now-superseded controller? Drop it silently.
      if (controller.signal.aborted) return;
      const report = data?.final_report || data;
      setRes(report);
      setDemoMode(true);
      setLastRunAt(nowISOShort());
    } catch (e) {
      if (isAbortError(e)) return; // a newer action superseded this; stay silent
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      // Only release the loading flag if WE are still the active controller.
      // (If another request superseded us, IT now owns the loading state.)
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setLoading(false);
      }
    }
  };

  const run = async () => {
    if (inputMode === "upload" && !uploadedPath) {
      setErr("Upload a CSV first, or switch to search mode.");
      return;
    }
    if (inputMode === "search" && !query.trim()) {
      setErr("Type a product name to analyze.");
      return;
    }
    // Cancel any prior inflight request. This is what makes the app feel like
    // Google: type a new search mid-load, the old one dies, the new one starts.
    abortInflight();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Clear the previous result IMMEDIATELY so the stale dashboard vanishes the
    // instant a new search begins. Without this, old results (e.g. Sony) keep
    // rendering under the search bar for the full 30-80s while the new product
    // (e.g. PS5) loads — which reads as broken. With res=null + loading=true,
    // the <DashboardSkeleton/> takes over and shows a clean modern loading state.
    setRes(null);
    setStreamEvents([]);

    setLoading(true);
    setErr(null);
    setDemoMode(false);
    try {
      const body = buildBody();
      let data;
      if (stream) {
        data = await streamPipeline(
          body,
          (evt) => {
            // Ignore stream events that arrive after we were aborted
            if (controller.signal.aborted) return;
            setStreamEvents((xs) => [...xs, evt]);
          },
          controller.signal,
          (report, isEnriched) => {
            // SPEED: render the dashboard the INSTANT the fast report arrives
            // (`complete`), then seamlessly swap in the deepened version when the
            // background deep-classify pass streams `enriched`. We no longer wait for
            // the whole stream to close before showing anything.
            if (controller.signal.aborted) return;
            if (report && inputMode === "search" && query.trim()) {
              if (!report.meta) report.meta = {};
              report.meta.query_used = query.trim();
            }
            setRes(report);
            if (!isEnriched) {
              setLastRunAt(nowISOShort());
              setLoading(false); // dashboard is interactive; deep signals fill in async
            }
          }
        );
      } else {
        const { data: resp } = await api.post(
          "/insightmesh/run_pipeline",
          body,
          { signal: controller.signal }
        );
        data = resp?.final_report || resp;
      }
      // Late-arriving response from a now-superseded controller? Drop it silently.
      if (controller.signal.aborted) return;
      // SAFETY: ensure the result's query_used matches what the user ACTUALLY typed,
      // not what the backend echoed (which can drift due to caching, race conditions,
      // or the YouTube scraper returning off-topic content). This is the definitive
      // fix for the "searched Sony, got Tesla" class of bugs.
      if (data && inputMode === "search" && query.trim()) {
        if (!data.meta) data.meta = {};
        data.meta.query_used = query.trim();
      }
      setRes(data);
      setLastRunAt(nowISOShort());
    } catch (e) {
      if (isAbortError(e)) return; // user moved on; do not show an error, do not fall back
      if (_looksLikeMissingCreds(e)) {
        // Auto-fall back to demo data so the dashboard still showcases everything.
        // Use a NEW controller for the demo fallback so it can be cancelled too.
        if (controller.signal.aborted) return;
        const demoController = new AbortController();
        abortControllerRef.current = demoController;
        try {
          const { data: demoData } = await api.get(
            `/demo/report?product=${encodeURIComponent(_queryToDemoKey(query))}`,
            { signal: demoController.signal }
          );
          if (demoController.signal.aborted) return;
          const report = demoData?.final_report || demoData;
          setRes(report);
          setDemoMode(true);
          setLastRunAt(nowISOShort());
          if (abortControllerRef.current === demoController) {
            abortControllerRef.current = null;
            setLoading(false);
          }
          return;
        } catch (demoErr) {
          if (isAbortError(demoErr)) return;
          // fall through to original error
        }
      }
      const msg = e?.response?.data?.detail?.message || e?.response?.data?.detail || e?.response?.data?.error || e?.message || String(e);
      setErr(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      // Only release the loading flag if WE are still the active controller
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setLoading(false);
      }
    }
  };
  runRef.current = run;

  // --- Paste-to-analyze --- the universal, unblockable source. Sends pasted
  // review text to /insightmesh/paste, which runs the same analyzer and returns
  // a final_report shaped exactly like a normal run, so the WHOLE dashboard
  // (incl. the debate) renders it with zero special-casing.
  const runPasteFlow = async () => {
    if (!pasteText.trim()) {
      setErr("Paste some reviews first (a few sentences minimum).");
      return;
    }
    if (!pasteProduct.trim()) {
      setErr("Add a product name so the analysis can stay on-topic.");
      return;
    }
    abortInflight();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setRes(null);
    setStreamEvents([]);
    setLoading(true);
    setErr(null);
    setDemoMode(false);
    try {
      const report = await runPaste(
        { text: pasteText, product: pasteProduct.trim(), strictness: strictness || "normal" },
        controller.signal
      );
      if (controller.signal.aborted) return;
      setRes(report);
      setLastRunAt(nowISOShort());
    } catch (e) {
      if (isAbortError(e)) return;
      const msg = e?.response?.data?.detail?.message || e?.response?.data?.detail || e?.message || String(e);
      setErr(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setLoading(false);
      }
    }
  };
  runPasteRef.current = runPasteFlow;

  const trySample = (label) => {
    setInputMode("search");
    setQuery(label);
    setRes(null);            // <-- clear stale result BEFORE the new run begins
    setDemoMode(false);
    setStreamEvents([]);
    setErr(null);
    setTimeout(() => run(), 30);
  };

  const trySampleDemo = (label) => {
    setInputMode("search");
    setQuery(label);
    setRes(null);            // <-- same: never show old data alongside a new search
    setDemoMode(false);
    setStreamEvents([]);
    setErr(null);
    setTimeout(() => loadDemo(_queryToDemoKey(label)), 30);
  };

  const onCSV = async (file) => {
    setCsvUploading(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/understand/upload", fd, { headers: { "Content-Type": "multipart/form-data" } });
      const path = data?.raw_path || data?.rawPath || "";
      if (!path) throw new Error("Upload succeeded but no server path returned.");
      setUploadedPath(path);
      setInputMode("upload");
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setCsvUploading(false);
    }
  };

  const exportMd = async () => { if (!res) return; try { const b = await exportReportMd(res); downloadBlob(b, "insightmesh_report.md"); } catch (e) { setErr(e.message || String(e)); } };
  const exportHtml = async () => { if (!res) return; try { const b = await exportReportHtml(res); downloadBlob(b, "insightmesh_report.html"); } catch (e) { setErr(e.message || String(e)); } };

  const overview = res?.analysis?.overview;
  const metaA = res?.analysis?.meta || {};
  const perReview = res?.analysis?.per_review || [];
  const contributions = res?.contributions;
  const N = perReview.length;

  const customerWishes = overview?.customer_wishes || [];
  const emotionMix = overview?.emotion_mix || {};
  const langDist = overview?.language_distribution || {};
  const canonical = overview?.canonical_clusters || [];
  const sentimentOverTime = overview?.sentiment_over_time || [];
  const astroturf = overview?.astroturf_signals || { flag: false, summary: "", suspicious_clusters: [], repeat_authors: [] };
  const nextVersionRoadmap = overview?.next_version_roadmap || [];
  const whatUsersLove = overview?.what_users_love || [];
  const aspectSentiment = overview?.aspect_sentiment || { domain: null, aspects: [] };
  // Evidence gate: an aspect needs at least 2 mentions before we'll render it —
  // a single mention is anecdote, not a pattern worth a breakdown row.
  const aspectsWithEvidence = (aspectSentiment?.aspects || []).filter((a) => (a?.mentions || 0) >= 2);
  const buyerIntent = overview?.buyer_intent_summary || { distribution: [], compared_products: [], decision_health: {} };
  // Decision-health values (recommend / buy / return / avoid %). Hide the whole
  // section when every one of them is 0.0% — there's no stated-action signal to show.
  const decisionHealthHasSignal = ["recommend_pct", "buy_pct", "return_pct", "avoid_pct"]
    .some((k) => Number(buyerIntent?.decision_health?.[k] || 0) > 0);
  const sarcasmStats = overview?.sarcasm_stats || { flagged_count: 0, total: 0 };
  const trustScore = overview?.trust_score || null;
  const evidence = overview?.evidence || null;
  // Honest gate: fewer than 10 analyzed reviews is too thin to back a confident
  // TrustScore — fold it into the existing insufficient-data state.
  const fewReviews = N < 10;
  const insufficientData = !!(trustScore?.insufficient_data || evidence?.insufficient || fewReviews);
  const sentimentForecast = overview?.sentiment_forecast || null;
  const riskRegister = overview?.risk_register || [];
  const cumulativeImpact = overview?.cumulative_impact || null;
  const personas = overview?.personas || [];
  const customerEffort = overview?.customer_effort || null;
  const marketingAngles = overview?.marketing_angles || [];
  const smartSummary = overview?.smart_summary || null;
  const crossInsights = Array.isArray(overview?.cross_insights) ? overview.cross_insights : [];
  const competitiveIntel = overview?.competitive_intelligence || null;
  const credibility = overview?.credibility_intelligence || null;
  const dealbreakers = overview?.dealbreakers || null;
  const purchaseAdvice = overview?.purchase_advice || null;
  const aspectHierarchy = overview?.aspect_hierarchy || [];
  const aspectTaxonomy = overview?.aspect_taxonomy || null;
  const coverageReport = overview?.coverage_report || null;
  const [expandedAspects, setExpandedAspects] = useState({});
  const [trustExpanded, setTrustExpanded] = useState(false);
  const [personalPriorities, setPersonalPriorities] = useState([]);
  const [personalizedView, setPersonalizedView] = useState(null);
  const [personalizedLoading, setPersonalizedLoading] = useState(false);
  const [voiceListening, setVoiceListening] = useState(false);
  const [copiedAngle, setCopiedAngle] = useState(null);

  // Suggested priorities are derived from the product's ABSA aspects (so they're always relevant)
  const suggestedPriorities = useMemo(() => {
    const aspects = (aspectSentiment?.aspects || []).slice(0, 8).map((a) => a.aspect);
    return aspects;
  }, [aspectSentiment]);

  // Reset personalized view when the product changes
  useEffect(() => {
    setPersonalPriorities([]);
    setPersonalizedView(null);
  }, [res]);

  // Recompute personalized verdict when priorities change
  useEffect(() => {
    if (!overview || personalPriorities.length === 0) {
      setPersonalizedView(null);
      return;
    }
    let cancelled = false;
    setPersonalizedLoading(true);
    api.post("/insightmesh/priorities/reweight", { overview, priorities: personalPriorities })
      .then(({ data }) => { if (!cancelled) setPersonalizedView(data); })
      .catch(() => { if (!cancelled) setPersonalizedView(null); })
      .finally(() => { if (!cancelled) setPersonalizedLoading(false); });
    return () => { cancelled = true; };
  }, [personalPriorities, overview]);

  // Voice search (Web Speech API, graceful fallback when unsupported)
  const startVoiceSearch = useCallback(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setErr("Voice search isn't supported in this browser. Try Chrome or Edge.");
      return;
    }
    const rec = new SpeechRecognition();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    setVoiceListening(true);
    rec.onresult = (e) => {
      const transcript = e.results?.[0]?.[0]?.transcript;
      if (transcript) {
        setQuery(transcript);
        setTimeout(() => run(), 400);
      }
    };
    rec.onerror = () => setVoiceListening(false);
    rec.onend = () => setVoiceListening(false);
    try { rec.start(); } catch { setVoiceListening(false); }
  }, []);

  const [discover, setDiscover] = useState({ trending: [], recent: [], stats: null });
  const [related, setRelated] = useState([]);

  useEffect(() => {
    let cancelled = false;
    api.get("/discover/feed")
      .then(({ data }) => { if (!cancelled) setDiscover(data || { trending: [], recent: [] }); })
      .catch(() => { if (!cancelled) setDiscover({ trending: [], recent: [], stats: null }); });
    return () => { cancelled = true; };
  }, [lastRunAt]);

  useEffect(() => {
    if (!res || demoMode) { setRelated([]); return; }
    const q = res?.meta?.query_used;
    if (!q) { setRelated([]); return; }
    let cancelled = false;
    api.get(`/discover/related?query=${encodeURIComponent(q)}`)
      .then(({ data }) => { if (!cancelled) setRelated(data?.related || []); })
      .catch(() => { if (!cancelled) setRelated([]); });
    return () => { cancelled = true; };
  }, [res, demoMode]);

  const platformSeries = useMemo(() => buildPlatformSentimentSeries(contributions), [contributions]);
  const emotionSeries = useMemo(() => buildEmotionSeries(emotionMix), [emotionMix]);
  const languages = useMemo(() => buildLanguageList(langDist), [langDist]);
  const sampleComments = useMemo(() => pickRepresentativeComments(perReview, 3), [perReview]);
  const verdict = useMemo(() => buildCustomerVerdict({ overview, n: N, customerMode: mode === "customer" }), [overview, N, mode]);

  const topMood = useMemo(() => {
    if (!emotionSeries.length) return { label: "—", pct: 0 };
    const total = emotionSeries.reduce((s, x) => s + x.value, 0) || 1;
    const top = [...emotionSeries].sort((a, b) => b.value - a.value)[0];
    return { label: top.name, pct: Math.round((top.value / total) * 100) };
  }, [emotionSeries]);

  // Classify a cluster as praise (positive-majority) vs complaint (negative-majority)
  // using the backend's praise/complaint category shares. Praise clusters (e.g.
  // "Excitement about product functionality") must NOT show up under "Top complaints" —
  // they belong in "What customers love".
  // NOTE: compare praise vs complaint RELATIVELY (whichever is larger wins). An
  // absolute `praise_share > 0.5` gate was the old bug: a 47% praise / 30% complaint
  // cluster failed the >0.5 test and got dumped into complaints.
  const isPraiseCluster = (c) => {
    const hasShares = typeof c.praise_share === "number" || typeof c.complaint_share === "number";
    if (hasShares) {
      const praise = c.praise_share || 0;
      const complaint = c.complaint_share || 0;
      if (praise !== complaint) return praise > complaint;
      return c.dominant_category === "Praise"; // tie → fall back to dominant category
    }
    if (c.dominant_category) return c.dominant_category === "Praise";
    if (typeof c.avg_sentiment === "number") return c.avg_sentiment >= 3.5;
    return false;
  };

  const topConcerns = useMemo(() => {
    return [...canonical]
      .filter((c) => (c["share_%"] || 0) >= 3 && !isPraiseCluster(c))
      .sort((a, b) => (b["share_%"] || 0) - (a["share_%"] || 0))
      .slice(0, 5);
  }, [canonical]);

  const topPraiseClusters = useMemo(() => {
    return [...canonical]
      .filter((c) => (c["share_%"] || 0) >= 3 && isPraiseCluster(c))
      .sort((a, b) => (b["share_%"] || 0) - (a["share_%"] || 0))
      .slice(0, 5);
  }, [canonical]);

  return (
    <div className="space-y-4">
      <Card padded={false} className="overflow-hidden">
        <div className="px-5 pt-5">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-medium text-zinc-100">
                {isStale ? (
                  <span className="text-zinc-400">
                    {res?.meta?.query_used} <span className="text-[10px] uppercase tracking-wider text-amber-400 ml-1">stale</span>
                  </span>
                ) : (res?.meta?.query_used ? res.meta.query_used : "What do you want to analyze?")}
              </h2>
              <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                live
              </span>
            </div>
            <div className="flex gap-1.5 rounded-full bg-zinc-900 p-1">
              <button
                onClick={() => setMode("customer")}
                className={`rounded-full px-3 py-1 text-xs transition ${mode === "customer" ? "bg-blue-600 text-white" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                Customer
              </button>
              <button
                onClick={() => setMode("company")}
                className={`rounded-full px-3 py-1 text-xs transition ${mode === "company" ? "bg-indigo-600 text-white" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                Company
              </button>
            </div>
          </div>

          <div className="flex gap-1 mb-3 border-b border-zinc-800">
            <button
              onClick={() => setInputMode("search")}
              className={`-mb-px border-b-2 px-3 py-2 text-xs transition ${inputMode === "search" ? "border-blue-500 text-blue-300" : "border-transparent text-zinc-400 hover:text-zinc-200"}`}
            >
              Search by product
            </button>
            <button
              onClick={() => setInputMode("upload")}
              className={`-mb-px border-b-2 px-3 py-2 text-xs transition ${inputMode === "upload" ? "border-blue-500 text-blue-300" : "border-transparent text-zinc-400 hover:text-zinc-200"}`}
            >
              Upload CSV
            </button>
            <button
              onClick={() => setInputMode("paste")}
              className={`-mb-px border-b-2 px-3 py-2 text-xs transition ${inputMode === "paste" ? "border-blue-500 text-blue-300" : "border-transparent text-zinc-400 hover:text-zinc-200"}`}
              title="Paste reviews from anywhere — Amazon, Flipkart, an email, a spreadsheet. Works for any product, no blocking."
            >
              Paste reviews
            </button>
          </div>

          {inputMode === "search" && (
            <div className="mb-3">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    className={`w-full rounded-xl border bg-zinc-950 px-4 py-2.5 pr-16 text-sm outline-none placeholder:text-zinc-500 focus:border-blue-600 focus:ring-1 focus:ring-blue-700 ${isStale ? "border-amber-600/60" : "border-zinc-700"}`}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run(); }}
                    placeholder="e.g., Sony WH-1000XM6"
                  />
                  {query && (
                    <button
                      type="button"
                      onClick={() => { setQuery(""); setRes(null); setDemoMode(false); setStreamEvents([]); setErr(null); }}
                      title="Clear and start over"
                      className="absolute right-10 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded-full text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
                    >
                      ×
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={startVoiceSearch}
                    disabled={voiceListening}
                    title={voiceListening ? "Listening…" : "Voice search"}
                    className={`absolute right-2 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded-full transition ${voiceListening ? "bg-rose-900/60 text-rose-200 animate-pulse" : "text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"}`}
                  >
                    🎙
                  </button>
                </div>
                <button
                  onClick={loading ? abortInflight : run}
                  disabled={!loading && !query.trim()}
                  className={`rounded-xl px-5 py-2.5 text-sm font-medium text-white transition disabled:opacity-50 ${
                    loading
                      ? "bg-rose-600 hover:bg-rose-500"
                      : "bg-blue-600 hover:bg-blue-500"
                  }`}
                  title={loading ? "Stop the current analysis" : "Start a new analysis"}
                >
                  {loading ? "■ Stop" : "Analyze →"}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-zinc-500">Try:</span>
                {SAMPLE_PRODUCTS.map((p) => (
                  <button
                    key={p.key}
                    onClick={() => trySample(p.label)}
                    className="rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-0.5 text-[11px] text-zinc-300 hover:bg-zinc-800"
                  >
                    {p.label} <span className="text-zinc-500">· {p.category}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {inputMode === "upload" && (
            <div className="mb-3">
              <div className="flex gap-2">
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(e) => e.target.files?.[0] && onCSV(e.target.files[0])}
                  className="flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-300"
                />
                <button
                  onClick={run}
                  disabled={!uploadedPath || loading}
                  className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
                >
                  {loading ? "Analyzing…" : "Analyze →"}
                </button>
              </div>
              {csvUploading && <div className="mt-1 text-[11px] text-zinc-500">Uploading…</div>}
              {uploadedPath && <div className="mt-1 truncate text-[11px] text-zinc-500" title={uploadedPath}>Last dataset: {uploadedPath}</div>}
            </div>
          )}

          {inputMode === "paste" && (
            <div className="mb-3 space-y-2">
              <div className="rounded-lg border border-violet-800/40 bg-violet-950/10 px-3 py-2 text-[11px] text-violet-200">
                Paste real reviews from anywhere — Amazon, Flipkart, the App Store, an email, a spreadsheet column.
                Works for <span className="font-medium">any product, any country</span>, with no scraping and no blocking.
                One review per line, or separate them with blank lines.
              </div>
              <input
                value={pasteProduct}
                onChange={(e) => setPasteProduct(e.target.value)}
                placeholder="Product name (e.g., boAt Airdopes 141)"
                className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm text-zinc-200 outline-none placeholder:text-zinc-500 focus:border-violet-600"
              />
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                rows={8}
                placeholder={"Paste reviews here…\n\nGreat battery life, lasts me two full days.\n\nThe app keeps disconnecting from the earbuds, very annoying.\n\nValue for money is unbeatable at this price."}
                className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-violet-600 font-mono leading-relaxed"
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-zinc-500">
                  {pasteText.trim() ? `${pasteText.split(/\n\s*\n|\n/).filter((l) => l.trim().length >= 8).length} review(s) detected` : "No reviews yet"}
                </span>
                <button
                  onClick={loading ? abortInflight : runPasteFlow}
                  disabled={!loading && (!pasteText.trim() || !pasteProduct.trim())}
                  className={`rounded-xl px-5 py-2.5 text-sm font-medium text-white transition disabled:opacity-50 ${loading ? "bg-rose-600 hover:bg-rose-500" : "bg-violet-600 hover:bg-violet-500"}`}
                >
                  {loading ? "■ Stop" : "Analyze pasted reviews →"}
                </button>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-[11px] text-zinc-500">Platforms</span>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 has-[:checked]:border-blue-700 has-[:checked]:bg-blue-900/30">
              <input type="checkbox" className="sr-only" checked={platforms.youtube} onChange={(e) => setPlatforms((p) => ({ ...p, youtube: e.target.checked }))} />
              <span className={platforms.youtube ? "text-blue-200" : "text-zinc-400"}>YouTube</span>
            </label>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 has-[:checked]:border-blue-700 has-[:checked]:bg-blue-900/30">
              <input type="checkbox" className="sr-only" checked={platforms.reddit} onChange={(e) => setPlatforms((p) => ({ ...p, reddit: e.target.checked }))} />
              <span className={platforms.reddit ? "text-blue-200" : "text-zinc-400"}>Reddit</span>
            </label>
            <label
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 has-[:checked]:border-emerald-700 has-[:checked]:bg-emerald-900/30"
              title="Real star-rated reviews from the Apple App Store + Google Play. Best for apps and app-based products."
            >
              <input type="checkbox" className="sr-only" checked={platforms.appstore} onChange={(e) => setPlatforms((p) => ({ ...p, appstore: e.target.checked }))} />
              <span className={platforms.appstore ? "text-emerald-200" : "text-zinc-400"}>App Store + Play</span>
              <span className="rounded bg-emerald-900/40 px-1 py-0 text-[8px] uppercase tracking-wider text-emerald-200">real reviews</span>
            </label>
            <span className="rounded-full border border-zinc-800 bg-zinc-900/60 px-2 py-0.5 text-[11px] text-zinc-600">TikTok soon</span>

            <div className="ml-auto flex items-center gap-3 text-[11px] text-zinc-400">
              <label className="inline-flex cursor-pointer items-center gap-1">
                <input type="checkbox" className="accent-blue-600" checked={stream} onChange={(e) => setStream(e.target.checked)} />
                Live progress
              </label>
              <div className="flex items-center gap-0.5 rounded-lg border border-zinc-700 bg-zinc-900/80 p-0.5">
                {[["quick", "\u26a1 Quick", "bg-yellow-600/80"], ["balanced", "\u2696\ufe0f Balanced", "bg-blue-600/80"], ["deep", "\ud83d\udd2c Deep", "bg-purple-600/80"]].map(([d, label, bg]) => (
                  <button key={d} onClick={() => setAnalysisDepth(d)}
                    className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors ${analysisDepth === d ? `${bg} text-white` : "text-zinc-500 hover:text-zinc-200"}`}>
                    {label}
                  </button>
                ))}
              </div>
              <button onClick={() => setShowAdvanced((v) => !v)} className="text-zinc-400 hover:text-zinc-200">
                {showAdvanced ? "Hide advanced ▴" : "Advanced ▾"}
              </button>
            </div>
          </div>

          {showAdvanced && (
            <div className="mt-3 grid grid-cols-2 gap-2 rounded-xl border border-zinc-800 bg-zinc-950/50 p-3 md:grid-cols-4">
              <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
                Strictness
                <select value={strictness} onChange={(e) => setStrictness(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200">
                  <option value="low">low</option>
                  <option value="normal">normal</option>
                  <option value="high">high</option>
                  <option value="ultra">ultra</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
                Time from
                <input type="datetime-local" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200" />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
                Time to
                <input type="datetime-local" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200" />
              </label>
              <div className="flex items-end gap-1">
                <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] hover:bg-zinc-800" onClick={() => { const w = lastNDaysISO(7); setTimeFrom(w.from); setTimeTo(w.to); }}>7d</button>
                <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] hover:bg-zinc-800" onClick={() => { const w = lastNDaysISO(30); setTimeFrom(w.from); setTimeTo(w.to); }}>30d</button>
                <button className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] hover:bg-zinc-800" onClick={() => { const w = lastNDaysISO(90); setTimeFrom(w.from); setTimeTo(w.to); }}>90d</button>
                <label className="ml-2 inline-flex cursor-pointer items-center gap-1 text-[11px] text-zinc-400">
                  <input type="checkbox" className="accent-blue-600" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> debug
                </label>
              </div>
            </div>
          )}
        </div>

        <div className="mt-2 border-t border-zinc-800 bg-zinc-950/40 px-5 py-2 text-xs text-zinc-500 flex items-center justify-between">
          <span className="flex items-center gap-2">
            {lastRunAt ? <>Last run <span className="text-zinc-300">{lastRunAt}</span></> : "No run yet — press Analyze."}
            {res?.meta?.from_cache && <span className="rounded bg-amber-900/30 px-1.5 py-0.5 text-[10px] text-amber-200">cached</span>}
            {res?.meta?.demo_mode && <span className="rounded bg-amber-900/30 px-1.5 py-0.5 text-[10px] text-amber-200">demo</span>}
            {res?.meta?.source === "pasted" && <span className="rounded bg-violet-900/30 px-1.5 py-0.5 text-[10px] text-violet-200" title="Analyzed from reviews you pasted">pasted reviews</span>}
            {res?.analysis?.llm_backend && res.analysis.llm_backend !== "none" && res.analysis.llm_backend !== "demo" && (
              <span className="rounded bg-blue-900/30 px-1.5 py-0.5 text-[10px] text-blue-200" title="LLM backend used for phrase extraction and action items">
                {res.analysis.llm_backend}
              </span>
            )}
            {res?.analysis?.llm_backend === "none" && (
              <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400" title="No LLM available — heuristic fallbacks used">heuristic</span>
            )}
          </span>
          {res && (
            <div className="flex gap-1.5">
              <button onClick={() => downloadJSON(res, "report.json")} className="text-zinc-400 hover:text-zinc-200">JSON</button>
              <span className="text-zinc-700">·</span>
              <button onClick={exportMd} className="text-zinc-400 hover:text-zinc-200">Markdown</button>
              <span className="text-zinc-700">·</span>
              <button onClick={exportHtml} className="text-zinc-400 hover:text-zinc-200">HTML</button>
            </div>
          )}
        </div>
      </Card>

      {stream && (loading || streamEvents.length > 0) && <ProgressStrip events={streamEvents} error={err} done={!loading} />}

      {/* --- Cancellation toast (Phase A.2) --- fixed bottom-right, auto-dismisses */}
      {cancelToast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full border border-zinc-700 bg-zinc-900/95 px-4 py-2 text-sm text-zinc-200 shadow-2xl backdrop-blur animate-in fade-in slide-in-from-bottom-2">
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-900/60 text-rose-300">■</span>
          <span>Cancelled — starting fresh</span>
        </div>
      )}

      {/* --- Stale-result banner --- prevents showing old product results when user typed a new query --- */}
      {isStale && !loading && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-700/60 bg-amber-950/30 px-4 py-2.5 text-sm">
          <div className="flex items-center gap-2 text-amber-200">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-amber-900/60 text-[11px]">!</span>
            <span>
              <span className="font-medium">Showing results for {resultQuery}.</span>{" "}
              <span className="text-amber-300/80">You typed “{query}” — click <span className="font-medium">Analyze →</span> to refresh.</span>
            </span>
          </div>
          <button onClick={run} className="rounded-md bg-amber-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-amber-500">
            Analyze “{query.length > 28 ? query.slice(0, 28) + "…" : query}”
          </button>
        </div>
      )}

      {demoMode && res && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-blue-800/60 bg-blue-950/30 px-4 py-2.5 text-sm">
          <div className="flex items-center gap-2 text-blue-200">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-blue-900/60 text-[11px]">✨</span>
            <span><span className="font-medium">Showing a curated sample analysis.</span> <span className="text-blue-300/80">Live data sourcing is being optimized — the full app will pull fresh reviews on every search.</span></span>
          </div>
          <button onClick={() => { setRes(null); setDemoMode(false); }} className="text-[11px] text-blue-300 hover:text-blue-100">Back</button>
        </div>
      )}

      {err && (
        <div className="rounded-xl border border-rose-800/60 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {String(err)}
        </div>
      )}

      {/* --- Loading skeleton (Phase: skeleton states) --- shown the moment a new
          search fires and the old result has been cleared. Replaces the stale
          dashboard with modern shimmer placeholders so the user never reads old
          data as current. Renders regardless of the Live-progress toggle; when
          that toggle is on, the ProgressStrip appears above it for stage detail. */}
      {loading && !res && (
        <DashboardSkeleton
          productName={query}
          stage={streamEvents.length ? (streamEvents[streamEvents.length - 1]?.type || streamEvents[streamEvents.length - 1]?.stage || "") : ""}
          platforms={selectedPlatforms}
        />
      )}

      {!res && !loading && (
        <>
          <Card>
            <div className="py-8 text-center">
              <div className="mx-auto mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-blue-900/40 text-blue-300">
                <span className="text-xl">✨</span>
              </div>
              <h3 className="text-base font-medium text-zinc-100">Analyze any product, across the world's comments</h3>
              <p className="mt-1 text-xs text-zinc-400">Type a product above, or jump straight into a trending one below.</p>
            </div>
          </Card>

          <Watchlist onOpen={(item) => { setInputMode("search"); setQuery(item.query); setTimeout(() => run(), 30); }} />

          {(discover.trending?.length > 0 || discover.recent?.length > 0) && (
            <Card title="Discover" subtitle="What's being analyzed across the platform">
              {discover.trending?.length > 0 && (
                <div className="mb-3">
                  <p className="mb-2 text-[11px] uppercase tracking-wider text-zinc-500">Trending · last 30 days</p>
                  <div className="flex flex-wrap gap-2">
                    {discover.trending.slice(0, 8).map((t, i) => {
                      const isDemo = t.source === "demo";
                      const sentColor = (t.avg_sentiment || 0) >= 4 ? "text-emerald-300" : (t.avg_sentiment || 0) >= 3 ? "text-amber-300" : "text-rose-300";
                      return (
                        <button
                          key={`${t.query}-${i}`}
                          onClick={() => { setInputMode("search"); setQuery(t.query); setTimeout(() => (isDemo ? loadDemo(t.demo_key) : run()), 30); }}
                          className="group flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900/60 px-3 py-1.5 text-xs hover:border-zinc-600 hover:bg-zinc-800"
                          title={isDemo ? "Curated sample" : `${t.count || 1} analyses · last seen ${t.last_seen?.slice(0, 10) || "—"}`}
                        >
                          <span className="text-zinc-100">{t.query}</span>
                          {typeof t.avg_sentiment === "number" && (
                            <span className={`font-mono text-[10px] ${sentColor}`}>{t.avg_sentiment.toFixed(1)}★</span>
                          )}
                          {isDemo && <span className="rounded bg-blue-900/40 px-1 py-0 text-[9px] text-blue-200">sample</span>}
                          {!isDemo && t.count > 1 && <span className="text-[10px] text-zinc-500">×{t.count}</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {discover.recent?.length > 0 && (
                <div>
                  <p className="mb-2 text-[11px] uppercase tracking-wider text-zinc-500">Recently analyzed</p>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                    {discover.recent.slice(0, 6).map((r, i) => {
                      const isDemo = r.source === "demo";
                      return (
                        <button
                          key={`${r.query}-${i}`}
                          onClick={() => { setInputMode("search"); setQuery(r.query); setTimeout(() => (isDemo ? loadDemo(r.demo_key) : run()), 30); }}
                          className="flex flex-col items-start gap-1 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5 text-left hover:border-zinc-700 hover:bg-zinc-800"
                        >
                          <span className="truncate w-full text-xs text-zinc-200">{r.query}</span>
                          <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                            {typeof r.avg_sentiment === "number" && <span>{r.avg_sentiment.toFixed(1)}★</span>}
                            {r.n_kept ? <span>{r.n_kept} reviews</span> : null}
                            {isDemo && <span className="rounded bg-blue-900/40 px-1 text-blue-200">sample</span>}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </Card>
          )}
        </>
      )}

      {res && (
        <>
          {/* --- Mode-distinct hero banner --- visually distinct between Customer and Company --- */}
          {(() => {
            const isCustomer = mode === "customer";
            const heroBg = isCustomer
              ? "from-blue-950/60 via-blue-900/30 to-transparent border-blue-800/50"
              : "from-indigo-950/60 via-violet-900/30 to-transparent border-indigo-800/50";
            const accentText = isCustomer ? "text-blue-300" : "text-indigo-300";
            const accentBg = isCustomer ? "bg-blue-500" : "bg-indigo-500";
            const eyebrow = isCustomer ? "For buyers" : "For your product team";
            // If results are stale, prefix the headline with a clear warning so it can never mislead
            const productName = res?.meta?.query_used || "this product";
            const lead = isStale
              ? (isCustomer
                  ? `Old results: ${productName}. Click Analyze → above to refresh.`
                  : `Showing previous results: ${productName} — click Analyze → to refresh`)
              : (isCustomer
                  ? `Should you buy ${productName}?`
                  : `${productName}: health check`);
            const sub = isCustomer
              ? "An honest read from real reviewers across the world."
              : "Where you're winning, where you're losing, what to ship next.";
            return (
              <div className={`relative overflow-hidden rounded-2xl border bg-gradient-to-br ${heroBg}`}>
                <div className="absolute top-0 left-0 right-0 h-1">
                  <div className={`h-full ${accentBg}`} style={{ width: "100%" }} />
                </div>
                <div className="flex items-center justify-between gap-4 p-5 pt-6">
                  <div className="flex-1 min-w-0">
                    <div className={`mb-1 text-[10px] uppercase tracking-[0.18em] font-medium ${accentText}`}>{eyebrow}</div>
                    <h1 className="text-xl md:text-2xl font-medium text-zinc-50 leading-tight">{lead}</h1>
                    <p className="mt-1 text-xs md:text-sm text-zinc-400">{sub}</p>
                  </div>
                  <div className="flex shrink-0 gap-1.5 rounded-full bg-zinc-950/60 p-1 backdrop-blur-sm border border-zinc-800">
                    <button
                      onClick={() => setMode("customer")}
                      className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition ${mode === "customer" ? "bg-blue-600 text-white shadow-lg shadow-blue-600/30" : "text-zinc-400 hover:text-zinc-200"}`}
                    >
                      Customer
                    </button>
                    <button
                      onClick={() => setMode("company")}
                      className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition ${mode === "company" ? "bg-indigo-600 text-white shadow-lg shadow-indigo-600/30" : "text-zinc-400 hover:text-zinc-200"}`}
                    >
                      Company
                    </button>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* --- Smart Summary card (LLM-narrated, mode-aware) --- */}
          {smartSummary && smartSummary[mode] && (() => {
            const s = smartSummary[mode];
            const source = smartSummary._source;
            const isCustomer = mode === "customer";
            const cardAccent = isCustomer ? "border-blue-800/40" : "border-indigo-800/40";
            const dotAccent = isCustomer ? "bg-blue-500" : "bg-indigo-500";
            const textAccent = isCustomer ? "text-blue-300" : "text-indigo-300";
            return (
              <Card className={`border ${cardAccent}`}>
                <div className="flex items-start gap-3">
                  <div className={`mt-1 h-2 w-2 shrink-0 rounded-full ${dotAccent} animate-pulse`} />
                  <div className="flex-1 min-w-0">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <div className={`text-[10px] uppercase tracking-[0.18em] font-medium ${textAccent}`}>
                        {isCustomer ? "The bottom line" : "Executive summary"}
                      </div>
                      {source && (
                        <span className="rounded-full bg-zinc-800/70 px-2 py-0.5 text-[9px] font-mono uppercase tracking-wider text-zinc-500" title={source === "llm" ? "Generated by language model" : source === "heuristic" ? "Generated from structured signals" : "Curated demo content"}>
                          {source === "llm" ? "✨ AI summary" : source === "demo" ? "sample" : "auto"}
                        </span>
                      )}
                    </div>
                    <h2 className="text-base md:text-lg font-medium text-zinc-50 leading-snug">{s.headline}</h2>
                    <p className="mt-2 text-sm leading-relaxed text-zinc-200">{s.summary}</p>
                    {overview?._analysis_confidence?.statement && (
                      <p className="mt-2 text-[11px] italic leading-relaxed text-zinc-500">{overview._analysis_confidence.statement}</p>
                    )}
                    {s.key_takeaways?.length > 0 && (
                      <ul className="mt-3 grid grid-cols-1 gap-1.5 md:grid-cols-2">
                        {s.key_takeaways.map((t, i) => (
                          <li key={i} className="flex items-start gap-2 text-xs text-zinc-300">
                            <span className={`mt-0.5 ${textAccent}`}>•</span>
                            <span>{t}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {(s.recommendation || s.strategic_priority || s.marketing_lead) && (
                      <div className="mt-3 space-y-1.5 border-t border-zinc-800 pt-3">
                        {s.recommendation && (
                          <div className="flex items-start gap-2 text-sm">
                            <span className={`mt-0.5 text-[10px] uppercase tracking-wider ${textAccent} shrink-0 w-28`}>Recommendation</span>
                            <span className="text-zinc-100">{s.recommendation}</span>
                          </div>
                        )}
                        {s.strategic_priority && (
                          <div className="flex items-start gap-2 text-sm">
                            <span className={`mt-0.5 text-[10px] uppercase tracking-wider ${textAccent} shrink-0 w-28`}>Top priority</span>
                            <span className="text-zinc-100">{s.strategic_priority}</span>
                          </div>
                        )}
                        {s.marketing_lead && (
                          <div className="flex items-start gap-2 text-sm">
                            <span className={`mt-0.5 text-[10px] uppercase tracking-wider ${textAccent} shrink-0 w-28`}>Marketing lead</span>
                            <span className="text-zinc-100">{s.marketing_lead}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            );
          })()}

          {/* --- Cross-section intelligence (Intelligence Synthesizer) ---
              Connections no single section sees alone, surfaced right under the
              executive summary so they frame everything below. Embedded in the
              flow (not a new titled section), gated on having any insight. */}
          {crossInsights.length > 0 && (
            <div className="space-y-2">
              {crossInsights.map((ci, i) => (
                <CrossInsight key={`${ci.type || "ci"}-${i}`} insight={ci} />
              ))}
            </div>
          )}

          {/* --- Reviewer Credibility Intelligence --- raw vs credibility-weighted
              vs credible-only sentiment, with a HIGH/MEDIUM/LOW distribution bar.
              Reweights every reviewer by ownership evidence, specificity & depth so
              the true signal separates from casual noise. Shown in both views. */}
          {credibility && (() => {
            const dist = credibility.credibility_distribution || { high: 0, medium: 0, low: 0 };
            const distTotal = (dist.high || 0) + (dist.medium || 0) + (dist.low || 0) || 1;
            const highPct = (dist.high / distTotal) * 100;
            const medPct = (dist.medium / distTotal) * 100;
            const lowPct = (dist.low / distTotal) * 100;
            const fmt = (v) => (typeof v === "number" ? `${v.toFixed(1)}★` : "—");
            return (
              <Card
                title="Reviewer credibility"
                subtitle="Weighted by ownership evidence, specificity, and review depth"
              >
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-zinc-500">Raw average</div>
                    <div className="text-2xl font-bold text-zinc-200">{fmt(credibility.raw_sentiment)}</div>
                    <div className="text-xs text-zinc-500">all {N} reviewers</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-zinc-500">Credibility-weighted</div>
                    <div className="text-2xl font-bold text-violet-400">{fmt(credibility.weighted_sentiment)}</div>
                    <div className="text-xs text-zinc-500">weighted by credibility</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-zinc-500">Credible only</div>
                    <div className="text-2xl font-bold text-emerald-400">{fmt(credibility.credible_avg)}</div>
                    <div className="text-xs text-zinc-500">{credibility.credible_count} verified-depth reviewers</div>
                  </div>
                </div>

                <div className="mt-4 flex h-2 gap-1 overflow-hidden rounded">
                  {highPct > 0 && <div className="bg-emerald-500" style={{ width: `${highPct}%` }} />}
                  {medPct > 0 && <div className="bg-amber-500" style={{ width: `${medPct}%` }} />}
                  {lowPct > 0 && <div className="bg-zinc-600" style={{ width: `${lowPct}%` }} />}
                </div>
                <div className="mt-1 flex justify-between text-xs text-zinc-500">
                  <span><span className="text-emerald-400">HIGH</span> {dist.high}</span>
                  <span><span className="text-amber-400">MEDIUM</span> {dist.medium}</span>
                  <span><span className="text-zinc-400">LOW</span> {dist.low}</span>
                </div>

                {credibility.insight && (
                  <div className="mt-3 rounded-lg border border-violet-900/30 bg-violet-950/20 p-2 text-sm text-violet-300">
                    {credibility.insight}
                  </div>
                )}
              </Card>
            );
          })()}

          {/* --- Competitive intelligence (Advanced Intelligence Pack, Feature 1) ---
              When reviewers compare the product to named rivals, show WHO wins on
              WHAT — a matrix of competitor × dimension plus an overall position
              badge. Shown in both views: buyers want it too. */}
          {competitiveIntel && competitiveIntel.total_comparisons > 0 && (() => {
            const position = competitiveIntel.competitive_position;
            // collect the union of dimensions across all competitors (column order)
            const dimSet = [];
            Object.values(competitiveIntel.matrix || {}).forEach((data) => {
              Object.keys(data.dimensions || {}).forEach((d) => {
                if (!dimSet.includes(d)) dimSet.push(d);
              });
            });
            const dimensions = dimSet.slice(0, 8);
            return (
              <Card
                title="Competitive intelligence"
                subtitle={`${competitiveIntel.total_comparisons} comparisons across ${competitiveIntel.competitors_found.length} competitor${competitiveIntel.competitors_found.length === 1 ? "" : "s"}`}
              >
                <div className="mb-4">
                  <span className={`text-lg font-semibold ${
                    position === "dominant" ? "text-emerald-400" :
                    position === "competitive" ? "text-amber-400" :
                    "text-rose-400"
                  }`}>
                    Competitive position: {position}
                  </span>
                  {competitiveIntel.key_finding && (
                    <p className="text-sm text-zinc-400 mt-1">{competitiveIntel.key_finding}</p>
                  )}
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-zinc-500">
                        <th className="text-left py-2 pr-3">Competitor</th>
                        {dimensions.map((d) => (
                          <th key={d} className="text-center px-2 capitalize whitespace-nowrap">{d}</th>
                        ))}
                        <th className="text-center pl-2">Overall</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(competitiveIntel.matrix).map(([comp, data]) => (
                        <tr key={comp} className="border-t border-zinc-800">
                          <td className="py-2 pr-3 text-zinc-300">
                            {comp}
                            <span className="ml-1 text-[10px] text-zinc-600">×{data.mention_count}</span>
                          </td>
                          {dimensions.map((d) => {
                            const cell = data.dimensions[d];
                            if (!cell) return <td key={d} className="text-center text-zinc-700">—</td>;
                            const icon = cell.verdict === "target_advantage" ? "🟢"
                              : cell.verdict === "competitor_advantage" ? "🔴" : "🟡";
                            const tip = (cell.evidence && cell.evidence[0]) || cell.verdict;
                            return (
                              <td key={d} className="text-center" title={tip}>{icon}</td>
                            );
                          })}
                          <td className="text-center pl-2 whitespace-nowrap">
                            {data.overall_verdict === "target_preferred" ? "✅ We win"
                              : data.overall_verdict === "competitor_preferred" ? "⚠️ They win"
                              : "🤝 Mixed"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-[11px] text-zinc-600">🟢 we win · 🔴 they win · 🟡 tie — hover a cell for the reviewer quote</p>
              </Card>
            );
          })()}

          {/* --- Deal-breaker & switching detector (Feature 2) --- the most
              expensive complaints: returns, switches, warnings, regret. Company
              view only — it's a lost-customer signal for product teams. */}
          {mode === "company" && dealbreakers && dealbreakers.total_dealbreakers > 0 && (
            <Card
              className="border-rose-900/40 bg-rose-950/10"
              title="Deal-breakers & lost customers"
              subtitle={`${(dealbreakers.dealbreaker_rate * 100).toFixed(0)}% of reviewers express deal-breaker sentiment (${dealbreakers.total_dealbreakers} of ${N})`}
            >
              {dealbreakers.top_reasons?.length > 0 && (
                <div className="mb-3">
                  <div className="text-[11px] uppercase tracking-wider text-rose-300/80 mb-1">Top reasons for leaving</div>
                  <div className="flex flex-wrap gap-1.5">
                    {dealbreakers.top_reasons.map((r, i) => (
                      <span key={i} className="rounded-full border border-rose-900/50 bg-rose-900/20 px-2.5 py-0.5 text-xs text-rose-200">
                        {r.reason} <span className="text-rose-400/60">×{r.count}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {Object.keys(dealbreakers.lost_to || {}).length > 0 && (
                <div className="mb-3">
                  <div className="text-[11px] uppercase tracking-wider text-rose-300/80 mb-1">Lost to</div>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(dealbreakers.lost_to).map(([prod, n]) => (
                      <span key={prod} className="rounded-full border border-amber-900/50 bg-amber-900/20 px-2.5 py-0.5 text-xs text-amber-200">
                        {prod} <span className="text-amber-400/60">×{n}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {(() => {
                const quotes = [...(dealbreakers.warnings || []), ...(dealbreakers.switches || []), ...(dealbreakers.returns || [])]
                  .filter((e) => e && e.quote).slice(0, 4);
                if (quotes.length === 0) return null;
                return (
                  <div className="space-y-1.5">
                    {quotes.map((e, i) => (
                      <div key={i} className="text-xs text-zinc-400 border-l-2 border-rose-900/50 pl-2.5">
                        “{e.quote}”
                        {e.switched_to && <span className="ml-1 text-amber-400/70">→ {e.switched_to}</span>}
                      </div>
                    ))}
                  </div>
                );
              })()}
            </Card>
          )}

          <Card padded={false} className="overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-emerald-900/40 bg-emerald-950/30 px-5 py-3 text-sm text-emerald-200">
              <div className="flex items-center gap-2">
                <span>✓</span>
                <span>
                  <span className="font-medium text-emerald-100">{N.toLocaleString()}</span> comments analyzed
                  {languages.length > 0 && <> · <span className="font-medium text-emerald-100">{languages.length}</span> languages</>}
                  {typeof res?.meta?.elapsed_ms === "number" && <> · <span className="text-emerald-300/70">{(res.meta.elapsed_ms / 1000).toFixed(1)}s</span></>}
                </span>
              </div>
              <div className="flex items-center gap-1 text-[11px] text-emerald-300/80">
                {languages.slice(0, 8).map((l) => <span key={l.code} className="rounded border border-emerald-900/50 px-1 py-0.5 font-mono text-emerald-200/80">{l.code}</span>)}
              </div>
            </div>
          </Card>

          {/* --- Honest evidence banner (the truth gate) --- when we don't have
              enough REAL reviews, say so loudly instead of faking a confident
              score. This is the spine of "honest by default." */}
          {insufficientData && evidence && (
            <div className="rounded-2xl border border-amber-700/50 bg-amber-950/20 p-5">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-900/50 text-amber-300">⚖</span>
                <div className="flex-1 min-w-0">
                  <div className="mb-1 text-[10px] uppercase tracking-[0.18em] font-medium text-amber-300">Not enough signal to be sure</div>
                  <h2 className="text-base md:text-lg font-medium text-zinc-50 leading-snug">{evidence.headline}</h2>
                  {evidence.reasons?.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {evidence.reasons.map((r, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-zinc-300">
                          <span className="mt-0.5 text-amber-400">•</span><span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded-full border border-amber-800/50 bg-amber-900/20 px-2 py-0.5 font-mono text-amber-200">
                      {evidence.n_relevant} of {evidence.n_total} are real reviews
                    </span>
                    <span className="rounded-full border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-mono text-zinc-400">
                      evidence: {evidence.level}
                    </span>
                  </div>
                  {evidence.irrelevant_examples?.length > 0 && (
                    <div className="mt-2 text-[11px] text-zinc-500">
                      <span className="text-zinc-600">Filtered out, e.g.:</span>{" "}
                      {evidence.irrelevant_examples.slice(0, 3).map((q, i) => (
                        <span key={i} className="italic">“{q}”{i < Math.min(2, evidence.irrelevant_examples.length - 1) ? "  ·  " : ""}</span>
                      ))}
                    </div>
                  )}
                  <p className="mt-2.5 text-[11px] text-zinc-400">
                    We're showing what little we found below, clearly marked as provisional. We won't pretend to a verdict we can't back up.
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile
              label="Overall sentiment"
              value={overview?.average_sentiment ? `${overview.average_sentiment.toFixed(1)} ★` : "—"}
              sub={typeof overview?.mood_index === "number" ? `mood ${overview.mood_index >= 0 ? "+" : ""}${overview.mood_index.toFixed(2)}` : null}
              tone={overview?.average_sentiment >= 4 ? "green" : overview?.average_sentiment <= 2.5 ? "red" : "amber"}
            />
            <StatTile label="Comments" value={N.toLocaleString()} sub={`${metaA?.kept_count ?? 0} of ${metaA?.input_count ?? 0} kept`} />
            {mode === "company" && (
              <StatTile label="Languages" value={languages.length || 0} sub={languages.slice(0, 4).map((l) => l.code).join(" · ") || "—"} />
            )}
            <StatTile label="Top emotion" value={topMood.label} sub={`${topMood.pct}% of comments`} tone={topMood.label === "Frustrated" ? "red" : topMood.label === "Delighted" ? "green" : "blue"} />
            {mode === "customer" && languages.length > 0 && (
              <StatTile label="Reviewed in" value={languages.length === 1 ? languages[0].code : `${languages.length} languages`} sub={languages.slice(0, 4).map((l) => l.code).join(" · ")} />
            )}
            {overview?._analysis_confidence && (
              <StatTile
                label="Analysis confidence"
                value={overview._analysis_confidence.label}
                sub={`${(overview._analysis_confidence.overall ?? 0).toFixed(2)} · ${overview._analysis_confidence.factors?.sample_size?.value ?? N} reviews`}
                tone={overview._analysis_confidence.overall >= 0.7 ? "green" : overview._analysis_confidence.overall >= 0.4 ? "amber" : "red"}
              />
            )}
            {overview?._self_correction?.quality_grade && (
              <StatTile
                label="Analysis quality"
                value={overview._self_correction.quality_grade}
                sub={overview._self_correction.quality_note
                  || (overview._self_correction.corrections_applied
                    ? `${overview._self_correction.corrections_applied} correction${overview._self_correction.corrections_applied === 1 ? "" : "s"} applied`
                    : "self-reviewed")}
                tone={["A", "B"].includes(overview._self_correction.quality_grade) ? "green"
                  : overview._self_correction.quality_grade === "C" ? "amber" : "red"}
              />
            )}
          </div>

          {/* Self-Correction quality flags (off-topic removals, over-confidence, etc.) */}
          {Array.isArray(overview?._quality_flags) && overview._quality_flags.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-amber-500/80">⚑ Quality flags:</span>
              {overview._quality_flags.map((f, i) => (
                <span key={i} className="rounded-full border border-amber-800/50 bg-amber-950/30 px-2 py-0.5 text-amber-300/90">{f}</span>
              ))}
            </div>
          )}

          {/* Evidence Engine — overall analysis confidence factor breakdown */}
          {overview?._analysis_confidence?.factors && (
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-zinc-500">Confidence factors:</span>
              {Object.entries(overview._analysis_confidence.factors).map(([k, f]) => (
                <span key={k} className="rounded-full border border-zinc-800 bg-zinc-900/60 px-2 py-0.5 font-mono text-zinc-400">
                  {k.replace(/_/g, " ")}: {typeof f?.score === "number" ? f.score.toFixed(2) : "—"}
                  {f?.value != null && <span className="text-zinc-600"> ({f.value})</span>}
                </span>
              ))}
            </div>
          )}

          {/* --- Product Intelligence badge (shows the AI understood the product) --- */}
          {overview?.product_intelligence && overview.product_intelligence.category && (
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-4 py-2.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-violet-400">AI understands</span>
              <span className="rounded-full bg-violet-600/20 px-2.5 py-0.5 text-[11px] font-medium text-violet-300">{overview.product_intelligence.category}</span>
              {overview.product_intelligence.segment && (
                <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">{overview.product_intelligence.segment}</span>
              )}
              {overview.product_intelligence.price_tier && (
                <span className="rounded-full bg-emerald-900/30 px-2 py-0.5 text-[11px] text-emerald-400">{overview.product_intelligence.price_tier}</span>
              )}
              {(overview.product_intelligence.key_competitors || []).length > 0 && (
                <span className="text-[11px] text-zinc-500">
                  vs {overview.product_intelligence.key_competitors.slice(0, 3).join(", ")}
                </span>
              )}
              {overview.product_intelligence.direct_aspects?.length > 0 && (
                <span className="ml-auto text-[10px] text-zinc-600">
                  tracking: {overview.product_intelligence.direct_aspects.slice(0, 5).join(" · ")}
                </span>
              )}
            </div>
          )}

          {/* --- TrustScore (NEW hero metric, both modes) --- */}
          {trustScore && typeof trustScore.score === "number" && (() => {
            const score = trustScore.score;
            const grade = trustScore.grade || "";

            // HONEST STATE: when evidence is insufficient, do NOT show a confident
            // colored score that reads as a product judgment. Show a neutral,
            // clearly-provisional card instead. This is the core of "no fake."
            if (insufficientData) {
              // When the backend flagged thin/junk evidence it supplies an honest
              // verdict; when the trigger is purely "<10 reviews" we write our own so
              // a confident backend verdict never leaks under a "Provisional" badge.
              const backendInsufficient = !!(trustScore?.insufficient_data || evidence?.insufficient);
              const provisionalVerdict = backendInsufficient
                ? trustScore.verdict
                : `Only ${N} review${N === 1 ? "" : "s"} so far — not enough to give this a confident score yet.`;
              return (
                <Card className="border border-zinc-700/60">
                  <div className="flex items-start gap-4">
                    <div className="flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-2xl border border-zinc-700 bg-zinc-800/40 text-zinc-400">
                      <span className="text-2xl font-light">—</span>
                      <span className="text-[8px] uppercase tracking-wider opacity-70">insufficient data</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="mb-0.5 flex items-baseline gap-2">
                        <span className="text-[10px] uppercase tracking-wider text-zinc-500">TrustScore</span>
                        <span className="rounded-full border border-zinc-700 bg-zinc-800/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-300">Insufficient data</span>
                        <span className="text-[10px] text-zinc-500">· low confidence</span>
                      </div>
                      <p className="max-w-xl text-sm leading-relaxed text-zinc-200">{provisionalVerdict}</p>
                      <p className="mt-1.5 text-[11px] text-zinc-500">
                        A score will appear once there are at least 10 real reviews to back it up.
                      </p>
                    </div>
                  </div>
                </Card>
              );
            }

            const gradeTone = score >= 85 ? "text-emerald-300 bg-emerald-900/30 border-emerald-800"
                            : score >= 70 ? "text-blue-300 bg-blue-900/30 border-blue-800"
                            : score >= 55 ? "text-amber-300 bg-amber-900/30 border-amber-800"
                            : score >= 40 ? "text-orange-300 bg-orange-900/30 border-orange-800"
                            :               "text-rose-300 bg-rose-900/30 border-rose-800";
            const barTone = score >= 70 ? "bg-emerald-500" : score >= 55 ? "bg-amber-500" : score >= 40 ? "bg-orange-500" : "bg-rose-500";
            return (
              <Card className={`border ${gradeTone.includes("emerald") ? "border-emerald-800/40" : gradeTone.includes("blue") ? "border-blue-800/40" : gradeTone.includes("amber") ? "border-amber-800/40" : gradeTone.includes("orange") ? "border-orange-800/40" : "border-rose-800/40"}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-4">
                    <div className={`flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-2xl border ${gradeTone}`}>
                      <span className="text-3xl font-light tabular-nums">{Math.round(score)}</span>
                      <span className="text-[9px] uppercase tracking-wider opacity-70">/100</span>
                    </div>
                    <div className="min-w-0">
                      <div className="mb-0.5 flex items-baseline gap-2">
                        <span className="text-[10px] uppercase tracking-wider text-zinc-500">TrustScore</span>
                        <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${gradeTone}`}>{grade}</span>
                        <span className="text-[10px] text-zinc-500">· {trustScore.confidence} confidence</span>
                      </div>
                      <p className="max-w-md text-sm leading-relaxed text-zinc-200">{trustScore.verdict}</p>
                      <div className="mt-2 h-1.5 w-full max-w-md overflow-hidden rounded-full bg-zinc-800">
                        <div className={`h-full rounded-full transition-all ${barTone}`} style={{ width: `${Math.max(2, score)}%` }} />
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => setTrustExpanded((v) => !v)}
                    className="shrink-0 rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-[11px] text-zinc-400 hover:bg-zinc-800"
                  >
                    {trustExpanded ? "Hide breakdown" : "Why this score?"}
                  </button>
                </div>
                {trustExpanded && Array.isArray(trustScore.breakdown) && (
                  <div className="mt-4 border-t border-zinc-800 pt-3">
                    <p className="mb-2 text-[10px] uppercase tracking-wider text-zinc-500">How the score is built</p>
                    <ul className="space-y-1.5">
                      {trustScore.breakdown.map((b, i) => {
                        const isNeg = b.delta < 0;
                        const isMax = b.max > 0;
                        const pct = isMax ? Math.min(100, Math.abs(b.delta) / b.max * 100) : Math.min(100, Math.abs(b.delta) / 10 * 100);
                        const fillTone = isNeg ? "bg-rose-500" : "bg-emerald-500";
                        return (
                          <li key={i} className="grid grid-cols-12 items-center gap-2 text-xs">
                            <span className="col-span-4 truncate text-zinc-300" title={b.label}>{b.label}</span>
                            <div className="col-span-5 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                              <div className={`h-full ${fillTone}`} style={{ width: `${pct}%` }} />
                            </div>
                            <span className={`col-span-1 text-right font-mono text-[11px] ${isNeg ? "text-rose-300" : "text-emerald-300"}`}>
                              {b.delta >= 0 ? "+" : ""}{b.delta.toFixed(1)}
                            </span>
                            <span className="col-span-2 truncate text-[10px] text-zinc-500" title={b.note}>{b.note}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </Card>
            );
          })()}

          {/* --- Skeptic vs Advocate debate (CENTERPIECE, buyer view) ---
              In the customer view the debate IS the verdict: it sits right under
              the headline TrustScore, argued in rounds and grounded in real
              reviews. `featured` puts the judge's verdict first. Hidden when data
              is too thin to argue meaningfully. Company view keeps the debate
              lower down (see the non-featured mount below) so the machinery
              dashboard leads there instead. */}
          {/* --- Purchase Decision Engine (Feature 3, Customer view) --- the
              BUY/WAIT/SKIP verdict with buyer personas, what-to-wait-for, and
              alternatives. Leads the customer view; fail-open (renders nothing
              when the advisor produced no data). */}
          {mode === "customer" && purchaseAdvice && (
            <>
              <div className={`p-6 rounded-xl border-2 ${
                purchaseAdvice.verdict === "BUY" ? "border-emerald-500 bg-emerald-950/20" :
                purchaseAdvice.verdict === "WAIT" ? "border-amber-500 bg-amber-950/20" :
                "border-rose-500 bg-rose-950/20"
              }`}>
                <div className={`text-3xl font-bold ${
                  purchaseAdvice.verdict === "BUY" ? "text-emerald-300" :
                  purchaseAdvice.verdict === "WAIT" ? "text-amber-300" :
                  "text-rose-300"
                }`}>{purchaseAdvice.verdict}</div>
                {purchaseAdvice.one_line && (
                  <p className="text-lg mt-2 text-zinc-100">{purchaseAdvice.one_line}</p>
                )}
                <p className="text-sm text-zinc-500 mt-1">
                  Confidence: {(purchaseAdvice.verdict_confidence * 100).toFixed(0)}%
                </p>
              </div>

              {purchaseAdvice.personas?.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  {purchaseAdvice.personas.map((persona) => (
                    <div key={persona.name} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
                      <div className="flex justify-between items-center">
                        <span className="font-semibold text-zinc-100">{persona.name}</span>
                        <span className={`text-sm font-bold ${
                          persona.recommendation === "BUY" ? "text-emerald-400" :
                          persona.recommendation === "WAIT" ? "text-amber-400" :
                          "text-rose-400"
                        }`}>{persona.recommendation}</span>
                      </div>
                      {persona.reason && <p className="text-sm text-zinc-400 mt-2">{persona.reason}</p>}
                      {(persona.best_aspects?.length > 0 || persona.worst_aspects?.length > 0) && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {persona.best_aspects?.map((a) => (
                            <span key={`b-${a}`} className="rounded bg-emerald-900/30 px-1.5 py-0.5 text-[10px] text-emerald-300">+{a}</span>
                          ))}
                          {persona.worst_aspects?.map((a) => (
                            <span key={`w-${a}`} className="rounded bg-rose-900/30 px-1.5 py-0.5 text-[10px] text-rose-300">−{a}</span>
                          ))}
                        </div>
                      )}
                      {persona.detected_from > 0 && (
                        <div className="mt-2 text-xs text-zinc-500">
                          Based on {persona.detected_from} reviewer{persona.detected_from === 1 ? "" : "s"} with this use case
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {purchaseAdvice.wait_for?.length > 0 && (
                <Card title="If you're waiting" subtitle="What would make this a clear buy">
                  {purchaseAdvice.wait_for.map((item, i) => (
                    <div key={i} className="flex items-center gap-2 py-1">
                      <span className="text-amber-400">◻</span>
                      <span className="text-sm text-zinc-300">{item}</span>
                    </div>
                  ))}
                </Card>
              )}

              {purchaseAdvice.alternatives?.length > 0 && (
                <Card title="Consider instead" subtitle="Based on reviewer comparisons">
                  {purchaseAdvice.alternatives.map((alt, i) => (
                    <div key={i} className="p-3 rounded-lg border border-zinc-800 mb-2 last:mb-0">
                      <div className="font-semibold text-zinc-200">{alt.product}</div>
                      {alt.why && <p className="text-sm text-zinc-400 mt-0.5">{alt.why}</p>}
                      {(alt.when_better || alt.when_worse) && (
                        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px]">
                          {alt.when_better && <span className="text-emerald-400/80">Better for: {alt.when_better}</span>}
                          {alt.when_worse && <span className="text-rose-400/80">Worse for: {alt.when_worse}</span>}
                        </div>
                      )}
                    </div>
                  ))}
                </Card>
              )}
            </>
          )}

          {mode === "customer" && !insufficientData && perReview.length > 0 && (
            <DebatePanel report={res} productName={res?.meta?.query_used} featured />
          )}

          {/* --- Personal Priorities (consumer killer feature) --- */}
          {mode === "customer" && suggestedPriorities.length >= 3 && (
            <Card
              title="What matters most to you?"
              subtitle={personalPriorities.length === 0
                ? "Tap the aspects you care about — we'll personalize the verdict"
                : `Personalized to: ${personalPriorities.join(", ")}`
              }
              className={personalPriorities.length > 0 ? "border-violet-800/40 bg-violet-950/10" : ""}
              right={personalPriorities.length > 0 && (
                <button
                  onClick={() => setPersonalPriorities([])}
                  className="text-[11px] text-zinc-500 hover:text-zinc-300"
                >
                  reset
                </button>
              )}
            >
              <div className="flex flex-wrap gap-1.5">
                {suggestedPriorities.map((aspect) => {
                  const active = personalPriorities.includes(aspect);
                  return (
                    <button
                      key={aspect}
                      onClick={() => {
                        if (active) {
                          setPersonalPriorities(personalPriorities.filter((p) => p !== aspect));
                        } else if (personalPriorities.length < 4) {
                          setPersonalPriorities([...personalPriorities, aspect]);
                        }
                      }}
                      disabled={!active && personalPriorities.length >= 4}
                      className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-40 ${active ? "border-violet-700 bg-violet-900/50 text-violet-100" : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"}`}
                    >
                      {active && <span className="mr-1 text-violet-300">✓</span>}
                      <span className="capitalize">{aspect.replace(/_/g, " ")}</span>
                    </button>
                  );
                })}
              </div>

              {personalizedLoading && (
                <div className="mt-3 text-[11px] text-zinc-500">Reweighing for your priorities…</div>
              )}

              {personalizedView?.personalized_trust_score && (() => {
                const pScore = personalizedView.personalized_trust_score.score;
                const baseline = trustScore?.score;
                const delta = baseline != null ? pScore - baseline : 0;
                const pGrade = personalizedView.personalized_trust_score.grade;
                const ringTone = pScore >= 70 ? "bg-emerald-900/30 border-emerald-700 text-emerald-200"
                              : pScore >= 55 ? "bg-amber-900/30 border-amber-700 text-amber-200"
                              : pScore >= 40 ? "bg-orange-900/30 border-orange-700 text-orange-200"
                              :                "bg-rose-900/30 border-rose-700 text-rose-200";
                const gradeTone = pScore >= 70 ? "bg-emerald-900/30 border-emerald-800 text-emerald-300"
                               : pScore >= 55 ? "bg-amber-900/30 border-amber-800 text-amber-300"
                               : pScore >= 40 ? "bg-orange-900/30 border-orange-800 text-orange-300"
                               :                "bg-rose-900/30 border-rose-800 text-rose-300";
                const flipNarrative = personalizedView.decision_flip;
                return (
                  <div className="mt-4 space-y-3 border-t border-violet-900/30 pt-3">
                    <div className="flex items-center gap-4">
                      <div className={`flex h-16 w-16 shrink-0 flex-col items-center justify-center rounded-2xl border ${ringTone}`}>
                        <span className="text-2xl font-light tabular-nums">{Math.round(pScore)}</span>
                        <span className="text-[9px] uppercase opacity-70">your score</span>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="mb-0.5 flex items-baseline gap-2">
                          <span className="text-[10px] uppercase tracking-wider text-violet-300">Personalized TrustScore</span>
                          <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${gradeTone}`}>{pGrade}</span>
                          {Math.abs(delta) >= 2 && (
                            <span className={`text-[10px] font-mono ${delta > 0 ? "text-emerald-300" : "text-rose-300"}`}>
                              {delta > 0 ? "+" : ""}{delta.toFixed(0)} vs baseline
                            </span>
                          )}
                        </div>
                        <p className="text-sm leading-relaxed text-zinc-100">{personalizedView.personalized_trust_score.verdict}</p>
                      </div>
                    </div>
                    {flipNarrative && (
                      <div className={`rounded-md border px-3 py-2 text-xs ${delta < 0 ? "border-rose-800/50 bg-rose-950/20 text-rose-200" : "border-emerald-800/50 bg-emerald-950/20 text-emerald-200"}`}>
                        <span className="text-[10px] uppercase tracking-wider mr-1.5">{delta < 0 ? "⚠ Heads up" : "✓ Good fit"}</span>
                        {flipNarrative}
                      </div>
                    )}
                    {personalizedView.matters_to_you?.length > 0 && (
                      <div>
                        <div className="mb-1.5 text-[10px] uppercase tracking-wider text-zinc-500">How this product does on what matters to you</div>
                        <div className="space-y-1.5">
                          {personalizedView.matters_to_you.map((a) => {
                            const barTone = a.avg_sentiment_stars >= 4 ? "bg-emerald-500" : a.avg_sentiment_stars >= 3 ? "bg-amber-500" : "bg-rose-500";
                            const txtTone = a.avg_sentiment_stars >= 4 ? "text-emerald-300" : a.avg_sentiment_stars >= 3 ? "text-amber-300" : "text-rose-300";
                            return (
                              <div key={a.aspect} className="flex items-center gap-2 text-xs">
                                <span className="w-28 shrink-0 capitalize text-zinc-300">{a.aspect.replace(/_/g, " ")}</span>
                                <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                                  <div className={`h-full ${barTone}`} style={{ width: `${Math.max(5, (a.avg_sentiment_stars / 5) * 100)}%` }} />
                                </div>
                                <span className={`w-14 shrink-0 text-right font-mono text-[11px] ${txtTone}`}>{a.avg_sentiment_stars.toFixed(1)}★</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                    {personalizedView.missing_priorities?.length > 0 && (
                      <div className="text-[11px] text-zinc-500">
                        Not enough signal yet on: {personalizedView.missing_priorities.join(", ")}.
                      </div>
                    )}
                  </div>
                );
              })()}
            </Card>
          )}

          {mode === "company" && astroturf?.flag && (
            <Card className="border-amber-800/60 bg-amber-950/30">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-900/50 text-amber-300">!</div>
                <div className="flex-1">
                  <div className="mb-1 text-[11px] uppercase tracking-wider text-amber-300">Possible astroturf</div>
                  <p className="text-sm leading-relaxed text-zinc-100">{astroturf.summary}</p>
                  {(astroturf.suspicious_clusters || []).length > 0 && (
                    <ul className="mt-2 space-y-1 text-[11px] text-zinc-400">
                      {astroturf.suspicious_clusters.slice(0, 3).map((c, i) => (
                        <li key={i}>
                          <span className="text-amber-200">×{c.count}</span>{" · "}
                          <span className="text-zinc-300">{c.unique_authors || 0} authors</span>
                          {c.burst_window_hours != null && <> · <span className="text-zinc-400">{c.burst_window_hours}h window</span></>}
                          <span className="ml-2 italic text-zinc-500">“{(c.sample || "").slice(0, 80)}…”</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* --- Risk Register (NEW, company only) --- */}
          {mode === "company" && riskRegister.length > 0 && (
            <Card
              title="Risk register"
              subtitle="Critical and high-severity issues that can't wait for the next version"
              className="border-rose-800/40 bg-rose-950/10"
              right={<Badge tone="red">{riskRegister.length} flagged</Badge>}
            >
              <div className="space-y-2.5">
                {riskRegister.map((r) => {
                  const sevTone = r.severity === "CRITICAL" ? "bg-rose-900/50 text-rose-100 border-rose-700" : r.severity === "HIGH" ? "bg-orange-900/40 text-orange-100 border-orange-700" : "bg-amber-900/40 text-amber-100 border-amber-700";
                  const timelineTone = r.timeline === "immediate" ? "text-rose-300" : r.timeline === "30 days" ? "text-orange-300" : "text-amber-300";
                  return (
                    <div key={r.cluster_id ?? r.rank} className="rounded-lg border border-rose-900/40 bg-zinc-950/40 p-3">
                      <div className="mb-1.5 flex items-start justify-between gap-2">
                        <div className="flex items-start gap-2 flex-1 min-w-0">
                          <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-900/50 font-mono text-[10px] text-rose-200">{r.rank}</span>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-zinc-100">{r.complaint}</div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                              <span className={`rounded border px-1.5 py-0.5 uppercase tracking-wider ${sevTone}`}>{r.severity}</span>
                              <span className="text-zinc-500">·</span>
                              <span className="text-zinc-300">{r.category_label}</span>
                              <span className="text-zinc-500">·</span>
                              <span className={`uppercase tracking-wider ${timelineTone}`}>address {r.timeline}</span>
                              <span className="text-zinc-500">·</span>
                              <span className="text-zinc-400">{r.share_pct}% share · {r.mentions} mentions</span>
                            </div>
                          </div>
                        </div>
                      </div>
                      {r.sample_quote && (
                        <div className="ml-7 border-l-2 border-rose-900/40 pl-2 text-[11px] italic text-zinc-400">
                          “{r.sample_quote.slice(0, 200)}{r.sample_quote.length > 200 ? "…" : ""}”
                        </div>
                      )}
                      {r.matched_cues && r.matched_cues.length > 0 && (
                        <div className="ml-7 mt-1.5 flex flex-wrap gap-1">
                          {r.matched_cues.slice(0, 3).map((cue, i) => (
                            <span key={i} className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[9px] text-zinc-400" title="Matched severity cue">{cue}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* --- Marketing Angles (company only, surfaces strong praise themes as PR copy) --- */}
          {mode === "company" && marketingAngles.length > 0 && (
            <Card
              title="Marketing angles"
              subtitle="Strongest praise themes from real reviewers — ready-to-quote copy"
              className="border-emerald-800/40 bg-emerald-950/10"
              right={<Badge tone="green">PR ready</Badge>}
            >
              <div className="space-y-3">
                {marketingAngles.map((a, i) => (
                  <div key={i} className="rounded-lg border border-emerald-900/30 bg-zinc-950/40 p-3">
                    <div className="mb-1.5 flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-emerald-100">{a.theme}</div>
                        <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-500">
                          <span className="font-mono text-emerald-300">{a.mentions} mentions</span>
                          <span>·</span>
                          <span>{(a.positive_ratio * 100).toFixed(0)}% positive</span>
                          {a.avg_sentiment_stars && <>
                            <span>·</span>
                            <span className="text-amber-300">{a.avg_sentiment_stars.toFixed(1)}★</span>
                          </>}
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          navigator.clipboard?.writeText(a.best_quote);
                          setCopiedAngle(i);
                          setTimeout(() => setCopiedAngle(null), 1500);
                        }}
                        className="shrink-0 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-400 hover:bg-zinc-800"
                      >
                        {copiedAngle === i ? "✓ copied" : "copy quote"}
                      </button>
                    </div>
                    <blockquote className="border-l-2 border-emerald-600 pl-3 text-sm italic text-zinc-100">
                      “{a.best_quote}”
                    </blockquote>
                    {a.supporting_quotes?.length > 0 && (
                      <div className="mt-2 space-y-0.5 text-[11px] italic text-zinc-500">
                        {a.supporting_quotes.slice(0, 2).map((q, j) => (
                          <div key={j}>· “{q}”</div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* --- Customer Effort Score (company only) --- */}
          {mode === "company" && customerEffort && (() => {
            const score = customerEffort.score;
            const ringTone = score < 25 ? "text-emerald-300 border-emerald-700 bg-emerald-900/30"
                          : score < 50 ? "text-amber-300 border-amber-700 bg-amber-900/30"
                          : score < 75 ? "text-orange-300 border-orange-700 bg-orange-900/30"
                          : "text-rose-300 border-rose-700 bg-rose-900/30";
            const badgeTone = score < 25 ? "green" : score < 75 ? "amber" : "red";
            return (
              <Card
                title="Customer effort"
                subtitle="How hard is it to be a customer? Friction around the product, not in it"
                right={<Badge tone={badgeTone}>{customerEffort.label}</Badge>}
              >
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <div className="md:col-span-1 flex flex-col items-center justify-center">
                    <div className={`flex h-24 w-24 flex-col items-center justify-center rounded-full border-4 ${ringTone}`}>
                      <span className="text-3xl font-light tabular-nums">{Math.round(score)}</span>
                      <span className="text-[9px] uppercase tracking-wider opacity-70">CES</span>
                    </div>
                    <div className="mt-2 text-center">
                      <div className="text-xs text-zinc-300">{customerEffort.affected_share_pct}% of buyers</div>
                      <div className="text-[10px] text-zinc-500">report friction</div>
                    </div>
                  </div>
                  <div className="md:col-span-2 space-y-2">
                    {customerEffort.breakdown.map((b) => {
                      const barTone = b.share_pct >= 10 ? "bg-rose-500" : b.share_pct >= 5 ? "bg-amber-500" : "bg-zinc-600";
                      return (
                        <div key={b.category} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5">
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <span className="text-xs text-zinc-200">{b.label}</span>
                            <span className="font-mono text-[10px] text-zinc-500">{b.count} reviews · {b.share_pct}%</span>
                          </div>
                          <div className="mb-1 h-1 overflow-hidden rounded-full bg-zinc-800">
                            <div className={`h-full ${barTone}`} style={{ width: `${Math.min(100, b.share_pct * 5)}%` }} />
                          </div>
                          {b.sample && (
                            <div className="text-[10px] italic text-zinc-500 truncate" title={b.sample}>“{b.sample}”</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                {customerEffort.narrative && (
                  <p className="mt-3 rounded-md border border-zinc-800 bg-zinc-950/50 px-3 py-2 text-xs text-zinc-300">
                    {customerEffort.narrative}
                  </p>
                )}
              </Card>
            );
          })()}

          {mode === "company" && nextVersionRoadmap.length > 0 && (
            <Card
              title="Next-version improvements"
              subtitle={`Ranked engineering plan derived from ${nextVersionRoadmap.length} complaint cluster${nextVersionRoadmap.length === 1 ? "" : "s"}`}
              className="border-indigo-800/40"
              right={<Badge tone="indigo">roadmap</Badge>}
            >
              <div className="space-y-3">
                {nextVersionRoadmap.map((item) => {
                  const impactColor = item.impact === "high" ? "text-rose-300 bg-rose-900/30 border-rose-800/60" : item.impact === "medium" ? "text-amber-300 bg-amber-900/30 border-amber-800/60" : "text-zinc-400 bg-zinc-800/50 border-zinc-700";
                  const effortColor = item.effort === "low" ? "text-emerald-300" : item.effort === "high" ? "text-rose-300" : "text-amber-300";
                  return (
                    <div key={item.cluster_id ?? item.rank} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                      <div className="mb-2 flex items-start justify-between gap-3">
                        <div className="flex items-start gap-2.5 flex-1 min-w-0">
                          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-900/40 font-mono text-[11px] text-indigo-200">
                            {item.rank}
                          </span>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <div className="text-sm font-medium text-zinc-100">{item.complaint}</div>
                              <ConfidenceBadge evidence={item._evidence} className="shrink-0" />
                            </div>
                            <div className="mt-0.5 text-[11px] text-zinc-500">
                              {item.mentions} mentions · {item.share_pct}% of reviews
                              {item.high_risk && <span className="ml-1.5 text-rose-300">· high-risk</span>}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${impactColor}`}>
                            {item.impact} impact
                          </span>
                          <span className="text-[10px] text-zinc-500">·</span>
                          <span className={`text-[10px] uppercase tracking-wide ${effortColor}`}>{item.effort} effort</span>
                        </div>
                      </div>
                      <div className="mb-2 ml-9">
                        <div className="mb-1 text-[10px] uppercase tracking-wider text-indigo-300/80">Ship in next version</div>
                        <ul className="space-y-1">
                          {item.suggested_actions.map((b, i) => (
                            <li key={i} className="flex items-start gap-2 text-xs text-zinc-200">
                              <span className="mt-0.5 text-indigo-400">›</span>
                              <span>{b}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                      {item.counterfactual && (
                        <div className="ml-9 mt-2 rounded-md border border-emerald-900/40 bg-emerald-950/20 px-3 py-1.5 text-[11px] text-emerald-200">
                          <span className="text-[9px] uppercase tracking-wider text-emerald-400/80">Projected if shipped</span>
                          {" "}
                          <span className="font-mono text-emerald-100">+{item.counterfactual.sentiment_delta?.toFixed(2)}★</span>
                          {" "}·{" "}
                          <span className="font-mono text-emerald-100">+{item.counterfactual.trust_delta?.toFixed(1)}</span> TrustScore
                          {item.counterfactual.sentiment_ci && (
                            <span className="ml-2 text-[10px] text-emerald-300/60">
                              (95% CI: +{item.counterfactual.sentiment_ci[0]}★ to +{item.counterfactual.sentiment_ci[1]}★)
                            </span>
                          )}
                        </div>
                      )}
                      {item.sample_quote && (
                        <div className="ml-9 mt-2 border-l-2 border-zinc-700 pl-2 text-[11px] italic text-zinc-500">
                          “{item.sample_quote.slice(0, 160)}{item.sample_quote.length > 160 ? "…" : ""}”
                        </div>
                      )}
                      {(item.backlog_note || item.confidence) && (
                        <div className="ml-9 mt-2 flex items-center justify-between gap-2 text-[10px] text-zinc-600">
                          {item.backlog_note ? <span className="truncate">{item.backlog_note}</span> : <span />}
                          {typeof item.confidence === "number" && (
                            <span title="Solution confidence">conf {(item.confidence * 100).toFixed(0)}%</span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              {cumulativeImpact && cumulativeImpact.k > 0 && cumulativeImpact.sentiment_delta > 0 && (
                <div className="mt-3 rounded-xl border border-emerald-800/40 bg-gradient-to-r from-emerald-950/30 to-zinc-900/40 p-3.5">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-900/40 text-emerald-300">↑</span>
                    <div className="flex-1">
                      <div className="mb-0.5 text-[10px] uppercase tracking-wider text-emerald-300">If you ship the top {cumulativeImpact.k} together</div>
                      <p className="text-sm text-zinc-100">{cumulativeImpact.narrative}</p>
                    </div>
                  </div>
                </div>
              )}
            </Card>
          )}

          {!insufficientData && sentimentOverTime.length > 0 && (() => {
            // Evidence gate: a projection off fewer than 5 daily data points is
            // overfit noise — show the history line, but suppress the forecast overlay.
            const showForecast = sentimentOverTime.length >= 5;
            // Merge actual + forecast for the chart
            const forecastPoints = (showForecast ? (sentimentForecast?.forecast || []) : []).map((f) => ({
              date: f.date,
              n: 0,
              forecast: f.forecast,
              ci_low: f.ci_low,
              ci_high: f.ci_high,
              is_forecast: true,
            }));
            const merged = [...sentimentOverTime.map((d) => ({ ...d, is_forecast: false })), ...forecastPoints];
            // Temporal Anomaly Detection markers (Evidence Engine intelligence).
            const anomalies = overview?.temporal_anomalies || [];
            const sentByDate = Object.fromEntries(sentimentOverTime.map((d) => [d.date, d.avg_sentiment]));
            const anomColor = (sev) => (sev === "high" ? COLORS.rose : sev === "medium" ? COLORS.amber : COLORS.slate);
            const trend = sentimentForecast?.trend || "stable";
            const trendTone = trend === "rising" ? "text-emerald-300" : trend === "falling" ? "text-rose-300" : "text-zinc-400";
            const trendIcon = trend === "rising" ? "↗" : trend === "falling" ? "↘" : "→";
            // Consumer-friendly subtitle when forecast exists; operator-detail subtitle for company mode
            const subtitle = mode === "customer"
              ? (forecastPoints.length > 0
                  ? "How sentiment has moved — and where it's heading next 2 weeks"
                  : "How sentiment has moved over time")
              : `${sentimentOverTime.length} day${sentimentOverTime.length === 1 ? "" : "s"} of signal${forecastPoints.length ? ` + ${forecastPoints.length}-day forecast` : ""}`;
            return (
              <Card
                title="Sentiment over time"
                subtitle={subtitle}
                right={showForecast && sentimentForecast && (
                  <span className={`text-[11px] ${trendTone}`} title={sentimentForecast.narrative || ""}>
                    {trendIcon} {trend}{sentimentForecast.fit?.slope_per_week ? ` ${sentimentForecast.fit.slope_per_week >= 0 ? "+" : ""}${sentimentForecast.fit.slope_per_week.toFixed(2)}★/wk` : ""}
                  </span>
                )}
              >
                <div className="h-56">
                  <ResponsiveContainer>
                    <AreaChart data={merged} margin={{ left: 4, right: 12, top: 8, bottom: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                      <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#a1a1aa" }} tickFormatter={(d) => (d || "").slice(5)} />
                      <YAxis yAxisId="sent" domain={[1, 5]} ticks={[1, 2, 3, 4, 5]} tick={{ fontSize: 11, fill: "#a1a1aa" }} />
                      <YAxis yAxisId="vol" orientation="right" tick={{ fontSize: 11, fill: "#52525b" }} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => (typeof v === "number" ? `${v.toFixed(2)}${name.includes("sentiment") || name === "forecast" ? " ★" : ""}` : v)} />
                      <Area yAxisId="vol" type="monotone" dataKey="n" stroke={COLORS.slate} fill={COLORS.slate} fillOpacity={0.12} strokeOpacity={0.4} name="comments" />
                      <Area yAxisId="sent" type="monotone" dataKey="ci_high" stroke="none" fill={COLORS.indigo} fillOpacity={0.1} name="forecast CI" />
                      <Area yAxisId="sent" type="monotone" dataKey="ci_low" stroke="none" fill={"#18181b"} fillOpacity={1} name="" />
                      <Area yAxisId="sent" type="monotone" dataKey="avg_sentiment" stroke={COLORS.blue} fill={COLORS.blue} fillOpacity={0.28} strokeWidth={2} name="avg sentiment" />
                      <Area yAxisId="sent" type="monotone" dataKey="forecast" stroke={COLORS.indigo} strokeDasharray="4 3" fill={COLORS.indigo} fillOpacity={0.15} strokeWidth={2} name="forecast" />
                      {anomalies.map((a, i) => (
                        <ReferenceDot
                          key={`anom-${i}`}
                          yAxisId="sent"
                          x={a.date}
                          y={typeof sentByDate[a.date] === "number" ? sentByDate[a.date] : 1.5}
                          ifOverflow="extendDomain"
                          shape={(p) => (
                            <g style={{ cursor: "pointer" }}>
                              <circle cx={p.cx} cy={p.cy} r={7} fill={anomColor(a.severity)} stroke="#18181b" strokeWidth={1.5} />
                              <text x={p.cx} y={p.cy + 3.5} textAnchor="middle" fontSize={10} fontWeight="bold" fill="#fff">!</text>
                              <title>{a.explanation}</title>
                            </g>
                          )}
                        />
                      ))}
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
                  <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: COLORS.blue }} />avg sentiment</span>
                  {forecastPoints.length > 0 && <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: COLORS.indigo }} />forecast (next {forecastPoints.length} days)</span>}
                  <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: COLORS.slate }} />comment volume</span>
                  {anomalies.length > 0 && (
                    <span className="inline-flex items-center gap-1" title="Hover the markers for details">
                      <span className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full text-[8px] font-bold text-white" style={{ background: COLORS.rose }}>!</span>
                      {anomalies.length} sentiment drop{anomalies.length === 1 ? "" : "s"} flagged
                    </span>
                  )}
                </div>
                {showForecast && sentimentForecast?.narrative && (
                  <p className="mt-2 rounded-md border border-indigo-900/30 bg-indigo-950/20 px-3 py-1.5 text-[11px] text-indigo-200">
                    <span className="font-medium">Forecast: </span>{sentimentForecast.narrative}
                  </p>
                )}
              </Card>
            );
          })()}

          {mode === "company" && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Card title="Sentiment by platform" subtitle="Avg sentiment score, 0–1 confidence">
              {platformSeries.length === 0 ? (
                <div className="py-8 text-center text-xs text-zinc-500">No per-platform data.</div>
              ) : (
                <div className="h-48">
                  <ResponsiveContainer>
                    <BarChart data={platformSeries} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                      <XAxis type="number" domain={[0, 1]} tick={{ fontSize: 11, fill: "#a1a1aa" }} />
                      <YAxis type="category" dataKey="platform" tick={{ fontSize: 12, fill: "#e4e4e7" }} width={70} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v) => v.toFixed(3)} />
                      <Bar dataKey="avgSent" radius={[0, 6, 6, 0]}>
                        {platformSeries.map((row, i) => (
                          <Cell key={i} fill={row.avgSent >= 0.7 ? COLORS.emerald : row.avgSent >= 0.4 ? COLORS.amber : COLORS.rose} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
              {(overview?.empty_platforms || []).length > 0 && (
                <div className="mt-2 rounded-lg bg-amber-950/20 border border-amber-900/30 px-3 py-2 text-xs text-amber-300">
                  <span className="font-medium">No results from:</span>{" "}
                  {overview.empty_platforms.map(p => p.toUpperCase()).join(", ")}
                  <span className="text-amber-400/60 ml-1">— product may not have listings on {overview.empty_platforms.length === 1 ? "this platform" : "these platforms"}</span>
                </div>
              )}
            </Card>

            <Card title="Emotion mix" subtitle="Share of comments by dominant emotion">
              {emotionSeries.length === 0 ? (
                <div className="py-8 text-center text-xs text-zinc-500">No emotion data.</div>
              ) : (() => {
                // Sorted horizontal bars — the dominant emotion jumps out instantly.
                // Counts → percentages; hide <5% slivers to avoid clutter.
                const total = emotionSeries.reduce((s, x) => s + x.value, 0) || 1;
                const rows = emotionSeries
                  .map((r) => ({ ...r, pct: (r.value / total) * 100 }))
                  .filter((r) => r.pct > 5)
                  .sort((a, b) => b.pct - a.pct);
                if (rows.length === 0) {
                  return <div className="py-8 text-center text-xs text-zinc-500">No dominant emotion (all under 5%).</div>;
                }
                const top = rows[0];
                const topCluster = (canonical || [])
                  .slice()
                  .sort((a, b) => (b.count || b.size || 0) - (a.count || a.size || 0))[0];
                const topClusterName = topCluster?.reason || topCluster?.canonical_reason || null;
                return (
                  <>
                    <div className="space-y-2">
                      {rows.map((r) => (
                        <div key={r.name} className="flex items-center gap-2.5">
                          <span className="w-24 shrink-0 truncate text-xs text-zinc-300" title={r.name}>{r.name}</span>
                          <div className="relative h-5 flex-1 overflow-hidden rounded bg-zinc-800/40">
                            <div
                              className="h-full rounded"
                              style={{ width: `${r.pct}%`, backgroundColor: EMOTION_COLORS[r.name] || COLORS.slate }}
                            />
                          </div>
                          <span className="w-9 shrink-0 text-right font-mono text-xs text-zinc-400">{r.pct.toFixed(0)}%</span>
                        </div>
                      ))}
                    </div>
                    <p className="mt-3 border-t border-zinc-800 pt-3 text-xs text-zinc-400">
                      Dominant emotion:{" "}
                      <span className="font-medium" style={{ color: EMOTION_COLORS[top.name] || COLORS.slate }}>
                        {top.name} ({top.pct.toFixed(0)}%)
                      </span>
                      {topClusterName && (
                        <> — driven by <span className="text-zinc-200">{topClusterName}</span></>
                      )}
                    </p>
                  </>
                );
              })()}
            </Card>
            </div>
          )}

          {mode === "company" && topPraiseClusters.length > 0 && (
            <Card
              title="What customers love"
              subtitle={`${topPraiseClusters.length} positive theme${topPraiseClusters.length === 1 ? "" : "s"} from ${N} reviews`}
              className="border-emerald-800/30"
              right={<Badge tone="green">★ strengths</Badge>}
            >
              <ul className="space-y-3">
                {topPraiseClusters.map((c) => (
                  <li key={c.cluster_id}>
                    <div className="mb-1 flex items-baseline justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-sm text-zinc-100"><span className="text-emerald-400">+</span> {c.reason}</span>
                        <ConfidenceBadge evidence={c._evidence} className="shrink-0" />
                      </span>
                      <span className="font-mono text-xs text-emerald-300/80">{c["share_%"]}%</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                      <div className="h-full rounded-full bg-emerald-500/70 transition-all" style={{ width: `${Math.min(100, (c["share_%"] || 0) * 3)}%` }} />
                    </div>
                    {c.quotes?.[0] && (
                      <div className="mt-1.5 truncate border-l-2 border-emerald-900/40 pl-2 text-[11px] italic text-zinc-500">
                        “{typeof c.quotes[0] === "string" ? c.quotes[0] : c.quotes[0]?.quote || ""}”
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {coverageReport && Array.isArray(coverageReport.categories) && coverageReport.categories.length > 0 && (() => {
            const cats = coverageReport.categories;
            const wellCovered = cats.filter((c) => c.state === "well_covered" || c.state === "saturated");
            const developing = cats.filter((c) => c.state === "developing");
            const thinCats = cats.filter((c) => c.state === "thin");
            const gapNames = Array.isArray(coverageReport.gaps) ? coverageReport.gaps : [];
            const sp = (c) => (Array.isArray(c.sub_problems) ? c.sub_problems.length : 0);
            const rounds = coverageReport.rounds || 1;
            return (
              <Card
                title="Coverage intelligence"
                subtitle="What we investigated and what's still thin"
                right={<Badge tone="violet">{rounds} round{rounds === 1 ? "" : "s"}</Badge>}
              >
                <div className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                  {[...wellCovered, ...developing].map((c) => {
                    const green = c.state === "well_covered" || c.state === "saturated";
                    return (
                      <div key={c.category} className="flex items-center gap-2">
                        <span className={`h-2 w-2 shrink-0 rounded-full ${green ? "bg-emerald-500" : "bg-amber-500"}`} />
                        <span className="truncate text-sm text-zinc-300">{c.category}</span>
                        <span className="ml-auto shrink-0 font-mono text-xs text-zinc-500">{c.mentions}m · {sp(c)}sp</span>
                      </div>
                    );
                  })}
                  {thinCats.map((c) => (
                    <div key={c.category} className="flex items-center gap-2">
                      <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" />
                      <span className="truncate text-sm text-zinc-400">{c.category}</span>
                      <span className="ml-auto shrink-0 font-mono text-xs text-zinc-500">{c.mentions}m — thin</span>
                    </div>
                  ))}
                  {gapNames.map((g) => (
                    <div key={g} className="flex items-center gap-2">
                      <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" />
                      <span className="truncate text-sm text-zinc-500">{g}</span>
                      <span className="ml-auto shrink-0 font-mono text-xs text-zinc-600">no data</span>
                    </div>
                  ))}
                </div>
                {coverageReport.summary && (
                  <p className="mt-3 border-t border-zinc-800 pt-2 text-[11px] leading-relaxed text-zinc-500">{coverageReport.summary}</p>
                )}
              </Card>
            );
          })()}

          {mode === "company" && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Card title="Top complaints to address" subtitle={topConcerns.length ? `${topConcerns.length} themes from ${N} reviews` : "No themes surfaced yet"}>
                {topConcerns.length === 0 ? (
                  <div className="py-6 text-center text-xs text-zinc-500">No significant concern clusters.</div>
                ) : (
                  <ul className="space-y-3">
                    {topConcerns.map((c) => {
                      const ev = c._evidence;
                      const thin = ev && typeof ev.confidence === "number" && ev.confidence < 0.3;
                      return (
                      <li key={c.cluster_id} className={thin ? "rounded-lg border border-dashed border-zinc-700 p-2 opacity-60" : ""}>
                        <div className="mb-1 flex items-baseline justify-between gap-2">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm text-zinc-100">{c.reason}</span>
                            <ConfidenceBadge evidence={ev} className="shrink-0" />
                          </span>
                          <span className="font-mono text-xs text-zinc-500">{c["share_%"]}%</span>
                        </div>
                        {thin && <div className="mb-1 text-[10px] uppercase tracking-wide text-amber-500/80">⚠ Thin evidence — treat with caution</div>}
                        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${Math.min(100, (c["share_%"] || 0) * 3)}%`,
                              background: (c["share_%"] || 0) >= 18 ? COLORS.rose : (c["share_%"] || 0) >= 10 ? COLORS.amber : COLORS.slate,
                            }}
                          />
                        </div>
                        {c.solution?.bullets?.length > 0 && (
                          <ul className="ml-3 mt-1.5 list-disc text-xs text-zinc-400 space-y-0.5">
                            {c.solution.bullets.slice(0, 2).map((b, i) => <li key={i}>{b}</li>)}
                          </ul>
                        )}
                      </li>
                      );
                    })}
                  </ul>
                )}
              </Card>

              <Card
                title="Feature requests to triage"
                subtitle={customerWishes.length ? `${customerWishes.length} surfaced` : "No wishes detected yet"}
                right={customerWishes.length > 0 && <Badge tone="violet">{customerWishes.reduce((s, w) => s + w.count, 0)} total</Badge>}
              >
                {customerWishes.length === 0 ? (
                  <div className="py-6 text-center text-xs text-zinc-500">
                    No feature requests detected.<br />
                    <span className="text-zinc-600">Try a product with more discussion volume.</span>
                  </div>
                ) : (
                  <ul className="space-y-2">
                    {customerWishes.slice(0, 6).map((w, i) => (
                      <li key={i} className="flex items-start justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5 overflow-hidden">
                        <div className="flex-1 min-w-0 text-sm text-zinc-200">
                          <span className="text-violet-400">›</span>{" "}
                          <span className="line-clamp-2">{w.wish}</span>
                          {w.samples?.[0] && (
                            <div className="mt-1 truncate text-[11px] italic text-zinc-500" title={w.samples[0]}>
                              “{w.samples[0]}”
                            </div>
                          )}
                        </div>
                        <span className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] font-mono text-zinc-300">
                          {w.count}×
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}

          {mode === "customer" && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Card
                title="What people love"
                subtitle={whatUsersLove.length ? `${whatUsersLove.length} positive themes` : "Positive themes will appear here"}
                className="border-emerald-800/30"
                right={whatUsersLove.length > 0 && <Badge tone="green">★ strengths</Badge>}
              >
                {whatUsersLove.length === 0 ? (
                  <div className="py-6 text-center text-xs text-zinc-500">No standout praise themes surfaced.</div>
                ) : (
                  <ul className="space-y-2.5">
                    {whatUsersLove.slice(0, 4).map((t, i) => (
                      <li key={i} className="rounded-lg border border-emerald-900/30 bg-emerald-950/10 p-2.5">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-sm text-zinc-100"><span className="text-emerald-400">+</span> {t.theme}</span>
                          <span className="shrink-0 rounded-full bg-emerald-900/30 px-2 py-0.5 text-[11px] font-mono text-emerald-200">{t.count}×</span>
                        </div>
                        {t.quotes?.[0] && (
                          <div className="mt-1.5 truncate border-l-2 border-emerald-900/40 pl-2 text-[11px] italic text-zinc-500" title={t.quotes[0]}>
                            “{t.quotes[0]}”
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              <Card
                title="Things to watch out for"
                subtitle={topConcerns.length ? `What buyers commonly mention` : "No common concerns surfaced"}
                className="border-rose-800/30"
                right={topConcerns.length > 0 && <Badge tone="red">⚠ heads up</Badge>}
              >
                {topConcerns.length === 0 ? (
                  <div className="py-6 text-center text-xs text-zinc-500">Nothing major to flag.</div>
                ) : (
                  <ul className="space-y-2.5">
                    {topConcerns.slice(0, 4).map((c) => (
                      <li key={c.cluster_id} className="rounded-lg border border-rose-900/30 bg-rose-950/10 p-2.5">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-sm text-zinc-100"><span className="text-rose-400">−</span> {c.reason}</span>
                          <span className="shrink-0 rounded-full bg-rose-900/30 px-2 py-0.5 text-[11px] font-mono text-rose-200">{c["share_%"]}%</span>
                        </div>
                        {c.quotes?.[0] && (
                          <div className="mt-1.5 truncate border-l-2 border-rose-900/40 pl-2 text-[11px] italic text-zinc-500">
                            “{typeof c.quotes[0] === "string" ? c.quotes[0] : c.quotes[0]?.quote || ""}”
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}

          {/* --- Aspect-Based Sentiment Analysis (NEW) --- */}
          {/* A real breakdown needs at least 2 aspects each backed by >=2 mentions.
              With fewer (e.g. one aspect from two mentions) we don't fake a chart —
              we say so. With no aspect data at all, we render nothing. */}
          {aspectsWithEvidence.length < 2 && (aspectSentiment?.aspects?.length || 0) > 0 && (
            <Card title="Aspect breakdown" right={<Badge tone="zinc">ABSA</Badge>}>
              <p className="text-sm text-zinc-400">Not enough data for detailed aspect analysis.</p>
            </Card>
          )}
          {aspectsWithEvidence.length >= 2 && (
            <Card
              title="Aspect breakdown"
              subtitle={mode === "customer"
                ? "What’s genuinely good and what’s not, broken down by product aspect"
                : `ABSA across ${aspectsWithEvidence.length} aspects · domain: ${aspectSentiment.domain || "general"}`
              }
              right={<Badge tone="blue">ABSA</Badge>}
            >
              <div className="space-y-3">
                {aspectsWithEvidence.slice(0, mode === "customer" ? 6 : 10).map((a) => {
                  const sentiTone = a.avg_sentiment_stars >= 4 ? "text-emerald-300" : a.avg_sentiment_stars >= 3 ? "text-amber-300" : "text-rose-300";
                  const verdict = a.pct_positive >= 60 ? "loved" : a.pct_negative >= 60 ? "struggling" : "mixed";
                  const verdictTone = verdict === "loved" ? "text-emerald-400" : verdict === "struggling" ? "text-rose-400" : "text-amber-400";
                  return (
                    <div key={a.aspect} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                      <div className="mb-1.5 flex items-baseline justify-between gap-2">
                        <div className="flex items-baseline gap-2">
                          <span className="text-sm font-medium text-zinc-100 capitalize">{a.aspect.replace(/_/g, " ")}</span>
                          <span className={`font-mono text-xs ${sentiTone}`}>{a.avg_sentiment_stars.toFixed(1)}★</span>
                          <span className={`text-[10px] uppercase tracking-wider ${verdictTone}`}>· {verdict}</span>
                          <ConfidenceBadge evidence={a._evidence} className="shrink-0" />
                        </div>
                        <span className="font-mono text-[10px] text-zinc-500">{a.mentions} mentions</span>
                      </div>
                      {/* Stacked bar: positive / neutral / negative */}
                      <div className="mb-2 flex h-1.5 overflow-hidden rounded-full bg-zinc-800">
                        <div className="bg-emerald-500" style={{ width: `${a.pct_positive}%` }} title={`${a.pct_positive}% positive`} />
                        <div className="bg-zinc-600" style={{ width: `${a.pct_neutral}%` }} title={`${a.pct_neutral}% neutral`} />
                        <div className="bg-rose-500" style={{ width: `${a.pct_negative}%` }} title={`${a.pct_negative}% negative`} />
                      </div>
                      <div className="flex justify-between text-[10px] text-zinc-500">
                        <span><span className="text-emerald-400">{a.pct_positive}%</span> positive</span>
                        <span><span className="text-zinc-400">{a.pct_neutral}%</span> neutral</span>
                        <span><span className="text-rose-400">{a.pct_negative}%</span> negative</span>
                      </div>
                      {mode === "company" && (a.sample_positive || a.sample_negative) && (
                        <div className="mt-2 grid grid-cols-1 gap-1.5 md:grid-cols-2 text-[11px]">
                          {a.sample_positive && (
                            <div className="rounded border border-emerald-900/30 bg-emerald-950/10 p-2 text-zinc-400 italic">
                              <span className="not-italic text-emerald-400 text-[10px] uppercase tracking-wider">+ Praise</span><br />
                              “{a.sample_positive.slice(0, 140)}{a.sample_positive.length > 140 ? "…" : ""}”
                            </div>
                          )}
                          {a.sample_negative && (
                            <div className="rounded border border-rose-900/30 bg-rose-950/10 p-2 text-zinc-400 italic">
                              <span className="not-italic text-rose-400 text-[10px] uppercase tracking-wider">− Complaint</span><br />
                              “{a.sample_negative.slice(0, 140)}{a.sample_negative.length > 140 ? "…" : ""}”
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* --- Aspect Hierarchy: the depth fix (Phase A) --- */}
          {aspectHierarchy.length > 0 && (
            <Card
              title={mode === "customer" ? "What's actually going on, in detail" : "Detailed breakdown by aspect"}
              subtitle={mode === "customer"
                ? "Tap any aspect to see the specific reasons behind it"
                : `${aspectHierarchy.length} aspects with ${aspectHierarchy.reduce((s, a) => s + (a.sub_issues?.length || 0), 0)} specific sub-issues mapped from real reviews`
              }
              right={aspectTaxonomy?.source && (
                <span className="rounded-full bg-zinc-800/70 px-2 py-0.5 text-[9px] font-mono uppercase tracking-wider text-zinc-500" title={`Taxonomy source: ${aspectTaxonomy.source}`}>
                  {aspectTaxonomy.source === "llm" ? "✨ AI taxonomy" : aspectTaxonomy.source === "demo" ? "sample" : "auto"}
                </span>
              )}
            >
              <div className="space-y-3">
                {aspectHierarchy.map((aspect) => {
                  const expanded = expandedAspects[aspect.aspect] ?? false;
                  const verdictTone = aspect.verdict === "loved"
                    ? { text: "text-emerald-300", bg: "bg-emerald-900/20", border: "border-emerald-800/40", bar: "bg-emerald-500" }
                    : aspect.verdict === "struggling"
                    ? { text: "text-rose-300", bg: "bg-rose-900/20", border: "border-rose-800/40", bar: "bg-rose-500" }
                    : { text: "text-amber-300", bg: "bg-amber-900/20", border: "border-amber-800/40", bar: "bg-amber-500" };
                  const subIssues = aspect.sub_issues || [];
                  const sharePct = aspect.share_of_complaints_pct || aspect.share_of_all_pct || 0;

                  return (
                    <div key={aspect.aspect} className={`rounded-xl border ${verdictTone.border} ${verdictTone.bg} overflow-hidden transition-all`}>
                      <button
                        onClick={() => setExpandedAspects((s) => ({ ...s, [aspect.aspect]: !expanded }))}
                        className="w-full p-3.5 text-left hover:bg-white/[0.02] transition"
                      >
                        <div className="flex items-center gap-3">
                          <span className={`text-zinc-500 transition-transform ${expanded ? "rotate-90" : ""}`}>›</span>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-baseline gap-2 flex-wrap">
                              <span className="text-sm font-medium text-zinc-100">{aspect.label || aspect.aspect}</span>
                              {aspect.avg_sentiment_stars != null && (
                                <span className={`font-mono text-xs ${verdictTone.text}`}>{aspect.avg_sentiment_stars.toFixed(1)}★</span>
                              )}
                              <span className={`rounded-full px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${verdictTone.text}`}>{aspect.verdict}</span>
                              <span className="ml-auto font-mono text-[10px] text-zinc-500">{aspect.total_mentions} mentions · {sharePct.toFixed(1)}% of {mode === "company" ? "complaints" : "reviews"}</span>
                            </div>
                            {!expanded && subIssues.length > 0 && (
                              <div className="mt-1.5 text-[11px] text-zinc-400 truncate">
                                {subIssues.length} sub-issue{subIssues.length === 1 ? "" : "s"}: {subIssues.slice(0, 3).map((s) => s.name).join(" · ")}
                                {subIssues.length > 3 ? "…" : ""}
                              </div>
                            )}
                          </div>
                        </div>
                      </button>

                      {expanded && subIssues.length > 0 && (
                        <div className="border-t border-zinc-800/50 bg-zinc-950/40 p-3 space-y-2">
                          {subIssues.map((sub, i) => {
                            const subTone = sub.severity === "CRITICAL" ? "bg-rose-900/40 text-rose-200 border-rose-700"
                                          : sub.severity === "HIGH" ? "bg-orange-900/40 text-orange-200 border-orange-700"
                                          : sub.severity === "MEDIUM" ? "bg-amber-900/30 text-amber-200 border-amber-800"
                                          : "bg-zinc-800/40 text-zinc-400 border-zinc-700";
                            const subStarTone = (sub.avg_sentiment_stars ?? 5) >= 4 ? "text-emerald-300"
                                             : (sub.avg_sentiment_stars ?? 5) >= 3 ? "text-amber-300"
                                             : "text-rose-300";
                            const effortColor = sub.fix_difficulty?.effort === "low" ? "text-emerald-400"
                                            : sub.fix_difficulty?.effort === "medium" ? "text-amber-400"
                                            : "text-rose-400";
                            return (
                              <div key={i} className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3">
                                <div className="mb-1.5 flex items-start justify-between gap-2 flex-wrap">
                                  <div className="flex items-baseline gap-2 flex-wrap min-w-0 flex-1">
                                    <span className="text-sm font-medium text-zinc-100">{sub.name}</span>
                                    {sub.is_safety && (
                                      <span className="rounded bg-rose-900/60 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-rose-100">⚠ safety</span>
                                    )}
                                    {sub.is_accessibility && (
                                      <span className="rounded bg-violet-900/40 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-violet-200">accessibility</span>
                                    )}
                                  </div>
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${subTone}`}>{sub.severity}</span>
                                    {sub.avg_sentiment_stars != null && (
                                      <span className={`font-mono text-[10px] ${subStarTone}`}>{sub.avg_sentiment_stars.toFixed(1)}★</span>
                                    )}
                                  </div>
                                </div>
                                <div className="mb-2 flex items-center gap-2 text-[10px] text-zinc-500">
                                  <span className="font-mono">{sub.mentions} mentions</span>
                                  <span>·</span>
                                  <span>{sub.share_of_aspect_pct?.toFixed?.(1) ?? sub.share_of_aspect_pct}% of {aspect.label?.toLowerCase() || aspect.aspect}</span>
                                  <span>·</span>
                                  <span>{sub.share_of_all_pct?.toFixed?.(1) ?? sub.share_of_all_pct}% of all reviews</span>
                                  {mode === "company" && sub.fix_difficulty && (
                                    <>
                                      <span>·</span>
                                      <span title={`Fix category: ${sub.fix_difficulty.category}`}>
                                        fix: <span className={effortColor}>{sub.fix_difficulty.effort}</span> ({sub.fix_difficulty.category})
                                      </span>
                                    </>
                                  )}
                                </div>
                                {/* Mention share bar (visual weight indicator) */}
                                <div className="mb-2 h-1 overflow-hidden rounded-full bg-zinc-800">
                                  <div className={`h-full ${verdictTone.bar}`} style={{ width: `${Math.min(100, (sub.share_of_aspect_pct || 0))}%` }} />
                                </div>
                                {/* Why-narrative */}
                                {sub.narrative && (
                                  <p className="mb-2 text-[12px] text-zinc-300 leading-relaxed">
                                    <span className="text-zinc-500">Why → </span>{sub.narrative}
                                  </p>
                                )}
                                {/* Signal chips */}
                                {sub.signals && (sub.signals.geographic_hints?.length > 0 || sub.signals.temporal_hint || sub.signals.version_hints?.length > 0) && (
                                  <div className="mb-2 flex flex-wrap gap-1">
                                    {sub.signals.geographic_hints?.map((g, j) => (
                                      <span key={`g${j}`} className="rounded bg-blue-900/30 px-1.5 py-0.5 text-[9px] font-mono text-blue-200">⚑ {g}</span>
                                    ))}
                                    {sub.signals.temporal_hint && (
                                      <span className="rounded bg-amber-900/30 px-1.5 py-0.5 text-[9px] font-mono text-amber-200">▫ {sub.signals.temporal_hint}</span>
                                    )}
                                    {sub.signals.version_hints?.map((v, j) => (
                                      <span key={`v${j}`} className="rounded bg-violet-900/30 px-1.5 py-0.5 text-[9px] font-mono text-violet-200">v {v}</span>
                                    ))}
                                    {sub.personas_most_affected?.length > 0 && (
                                      <span className="rounded bg-indigo-900/30 px-1.5 py-0.5 text-[9px] font-mono text-indigo-200">
                                        affects: {sub.personas_most_affected.slice(0, 2).join(", ").replace(/_/g, " ")}
                                      </span>
                                    )}
                                  </div>
                                )}
                                {/* Sample quotes */}
                                {sub.sample_quotes?.length > 0 && (
                                  <div className="space-y-1 text-[11px] italic text-zinc-400 border-l-2 border-zinc-700 pl-2 mt-1.5">
                                    {sub.sample_quotes.slice(0, mode === "company" ? 3 : 2).map((q, j) => (
                                      <div key={j} className="truncate" title={q}>“{q}”</div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* --- Buyer Intent Distribution (NEW) --- */}
          {buyerIntent?.distribution && buyerIntent.distribution.some((d) => d.count > 0) && decisionHealthHasSignal && (
            <Card
              title={mode === "customer" ? "What other buyers are doing" : "Decision health"}
              subtitle={mode === "customer"
                ? "Stated buying actions across reviewers — strong signal vs sentiment alone"
                : "Actionable intent breakdown across the reviewer base"
              }
              right={(() => {
                const dh = buyerIntent.decision_health || {};
                const net = dh.net_intent;
                if (typeof net !== "number") return null;
                const tone = net >= 0.15 ? "green" : net <= -0.15 ? "red" : "amber";
                return <Badge tone={tone}>net intent {net >= 0 ? "+" : ""}{net.toFixed(2)}</Badge>;
              })()}
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  {buyerIntent.distribution.filter((d) => d.count > 0 && d.label !== "UNKNOWN").map((d) => {
                    const barTone = d.tone === "green" ? "bg-emerald-500" : d.tone === "red" ? "bg-rose-500" : d.tone === "amber" ? "bg-amber-500" : d.tone === "blue" ? "bg-blue-500" : d.tone === "indigo" ? "bg-indigo-500" : "bg-zinc-600";
                    return (
                      <div key={d.label} className="flex items-center gap-2 text-xs">
                        <span className="w-44 shrink-0 text-zinc-300">{d.pretty}</span>
                        <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                          <div className={`h-full rounded-full ${barTone}`} style={{ width: `${Math.min(100, d.pct * 2)}%` }} />
                        </div>
                        <span className="w-12 shrink-0 text-right font-mono text-[11px] text-zinc-400">{d.pct}%</span>
                      </div>
                    );
                  })}
                </div>
                <div>
                  {(() => {
                    const dh = buyerIntent.decision_health || {};
                    return (
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="rounded-lg border border-emerald-900/30 bg-emerald-950/10 p-2.5">
                          <div className="text-[10px] uppercase tracking-wide text-emerald-300">Recommending</div>
                          <div className="text-lg font-medium text-zinc-100">{(dh.recommend_pct ?? 0).toFixed(1)}%</div>
                        </div>
                        <div className="rounded-lg border border-blue-900/30 bg-blue-950/10 p-2.5">
                          <div className="text-[10px] uppercase tracking-wide text-blue-300">Buying</div>
                          <div className="text-lg font-medium text-zinc-100">{(dh.buy_pct ?? 0).toFixed(1)}%</div>
                        </div>
                        <div className="rounded-lg border border-rose-900/30 bg-rose-950/10 p-2.5">
                          <div className="text-[10px] uppercase tracking-wide text-rose-300">Returning</div>
                          <div className="text-lg font-medium text-zinc-100">{(dh.return_pct ?? 0).toFixed(1)}%</div>
                        </div>
                        <div className="rounded-lg border border-rose-900/30 bg-rose-950/10 p-2.5">
                          <div className="text-[10px] uppercase tracking-wide text-rose-300">Warning others off</div>
                          <div className="text-lg font-medium text-zinc-100">{(dh.avoid_pct ?? 0).toFixed(1)}%</div>
                        </div>
                      </div>
                    );
                  })()}
                  {buyerIntent.compared_products?.length > 0 && (
                    <div className="mt-3 rounded-lg border border-indigo-900/30 bg-indigo-950/10 p-2.5">
                      <div className="mb-1.5 text-[10px] uppercase tracking-wide text-indigo-300">Compared with</div>
                      <div className="flex flex-wrap gap-1.5">
                        {buyerIntent.compared_products.slice(0, 5).map((cp, i) => (
                          <button
                            key={i}
                            onClick={() => { setInputMode("search"); setQuery(cp.name); setTimeout(() => run(), 30); }}
                            className="inline-flex items-center gap-1 rounded-full border border-indigo-800/50 bg-indigo-950/30 px-2 py-0.5 text-[11px] text-indigo-200 hover:bg-indigo-900/40"
                          >
                            {cp.name}
                            <span className="text-indigo-400">×{cp.count}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* --- Personas ("people like me" / customer segments) — both modes --- */}
          {personas.length >= 2 && (
            <Card
              title={mode === "customer" ? "People like you" : "Customer segments"}
              subtitle={mode === "customer"
                ? "How different types of reviewers feel about this product"
                : `${personas.length} reviewer segments detected from writing style and behavior`
              }
              right={<Badge tone="violet">{personas.length} segments</Badge>}
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {personas.map((p) => {
                  const tones = {
                    indigo: { border: "border-indigo-800/40", bg: "bg-indigo-950/15", text: "text-indigo-300", chip: "bg-indigo-900/40 text-indigo-200" },
                    amber:  { border: "border-amber-800/40",  bg: "bg-amber-950/15",  text: "text-amber-300",  chip: "bg-amber-900/40 text-amber-200" },
                    blue:   { border: "border-blue-800/40",   bg: "bg-blue-950/15",   text: "text-blue-300",   chip: "bg-blue-900/40 text-blue-200" },
                    violet: { border: "border-violet-800/40", bg: "bg-violet-950/15", text: "text-violet-300", chip: "bg-violet-900/40 text-violet-200" },
                    zinc:   { border: "border-zinc-800",      bg: "bg-zinc-900/40",   text: "text-zinc-300",   chip: "bg-zinc-800 text-zinc-300" },
                  };
                  const t = tones[p.tone] || tones.zinc;
                  const verdictTone = p.verdict === "loved" ? "text-emerald-300" : p.verdict === "struggling" ? "text-rose-300" : "text-amber-300";
                  const verdictIcon = p.verdict === "loved" ? "♡" : p.verdict === "struggling" ? "✕" : "▪";
                  return (
                    <div key={p.key} className={`rounded-xl border ${t.border} ${t.bg} p-3.5`}>
                      <div className="mb-2 flex items-start gap-3">
                        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${t.chip} text-base`}>
                          {p.icon}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-baseline gap-2">
                            <span className="text-sm font-medium text-zinc-100">{p.label}</span>
                            <span className="font-mono text-[10px] text-zinc-500">{p.pct}%</span>
                          </div>
                          <div className="text-[11px] text-zinc-500">{p.desc}</div>
                        </div>
                      </div>
                      <div className="mb-2 flex items-center gap-2 text-xs">
                        {p.avg_sentiment_stars != null && (
                          <span className="font-mono text-amber-300">{p.avg_sentiment_stars.toFixed(1)}★</span>
                        )}
                        <span className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wider ${verdictTone}`}>
                          <span>{verdictIcon}</span> {p.verdict}
                        </span>
                        <span className="ml-auto text-[10px] text-zinc-500">{p.count} reviewers</span>
                      </div>
                      {p.top_concern && (
                        <div className="mb-1.5 text-[11px] text-zinc-400">
                          <span className="text-zinc-500">top concern:</span> {p.top_concern}
                        </div>
                      )}
                      {p.sample_quote && (
                        <blockquote className={`border-l-2 ${t.border} pl-2 text-[11px] italic text-zinc-400`}>
                          “{p.sample_quote.slice(0, 180)}{p.sample_quote.length > 180 ? "…" : ""}”
                        </blockquote>
                      )}
                      {p.buyer_intent_mix && Object.keys(p.buyer_intent_mix).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.entries(p.buyer_intent_mix).slice(0, 4).map(([intent, count]) => (
                            <span key={intent} className="rounded-full bg-zinc-800/70 px-1.5 py-0.5 font-mono text-[9px] text-zinc-400">
                              {intent.toLowerCase()} · {count}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* --- Causal Intelligence (confirmed cause→effect paths across reviewers) --- */}
          {(() => {
            const causal = (overview?.causal_findings || []).filter((f) => (f?.confirmations || 0) >= 2);
            if (causal.length === 0) return null;
            return (
              <Card
                title="Causal Intelligence"
                subtitle="Cause → effect paths confirmed by multiple independent reviewers"
                right={<Badge tone="violet">deep classify</Badge>}
              >
                <ul className="space-y-2.5">
                  {causal.map((f, i) => (
                    <li key={i} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5 text-sm">
                            {f.chain.map((step, si) => (
                              <React.Fragment key={si}>
                                <span className="rounded bg-zinc-800/80 px-1.5 py-0.5 text-[12px] text-zinc-100">{step}</span>
                                {si < f.chain.length - 1 && <span className="text-zinc-500">→</span>}
                              </React.Fragment>
                            ))}
                          </div>
                          <div className="mt-1 text-[11px] text-emerald-300">
                            confirmed by {f.confirmations} independent reviewer{f.confirmations === 1 ? "" : "s"}
                          </div>
                        </div>
                        <span className="shrink-0 rounded-full bg-violet-600/20 px-2 py-0.5 font-mono text-[11px] text-violet-300">×{f.confirmations}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            );
          })()}

          {/* --- Smart Signals (deep classification aggregate) --- */}
          {overview?.deep_signals && (overview.deep_signals.verified_claims?.length > 0 || overview.deep_signals.n_switching > 0 || overview.deep_signals.n_causal_chains > 0 || overview.deep_signals.expectation_summary) && (
            <Card title="Smart Signals" subtitle="AI-extracted intelligence from deep review analysis" right={<Badge tone="violet">deep classify</Badge>}>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">

                {/* Verified Claims */}
                {overview.deep_signals.verified_claims?.length > 0 && (
                  <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-400">✓ Verified by multiple reviewers</h4>
                    <ul className="space-y-1.5">
                      {overview.deep_signals.verified_claims.slice(0, 5).map((vc, i) => (
                        <li key={i} className="text-[12px] text-zinc-300">
                          <span className="text-emerald-400">{vc.count}×</span>{" "}
                          <span className="text-zinc-400">[{vc.aspect}]</span> {vc.text}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Competitive Switching */}
                {(Object.keys(overview.deep_signals.switching_from || {}).length > 0 || Object.keys(overview.deep_signals.switching_to || {}).length > 0) && (
                  <div className="rounded-lg border border-blue-800/40 bg-blue-950/20 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-blue-400">↔ Competitive switching</h4>
                    {Object.entries(overview.deep_signals.switching_from || {}).map(([prod, n]) => (
                      <div key={`from-${prod}`} className="text-[12px] text-zinc-300">
                        <span className="text-emerald-400">→ Switching FROM</span> {prod} <span className="text-zinc-500">({n}×)</span>
                      </div>
                    ))}
                    {Object.entries(overview.deep_signals.switching_to || {}).map(([prod, n]) => (
                      <div key={`to-${prod}`} className="text-[12px] text-zinc-300">
                        <span className="text-rose-400">← Switching TO</span> {prod} <span className="text-zinc-500">({n}×)</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Experience Stages */}
                {overview.deep_signals.experience_stages && Object.values(overview.deep_signals.experience_stages).some(v => v > 0) && (
                  <div className="rounded-lg border border-amber-800/40 bg-amber-950/20 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-amber-400">👤 Reviewer experience</h4>
                    <div className="space-y-1">
                      {Object.entries(overview.deep_signals.experience_stages).filter(([,v]) => v > 0).map(([stage, count]) => (
                        <div key={stage} className="flex items-center justify-between text-[12px]">
                          <span className="text-zinc-300">{stage.replace("_", " ")}</span>
                          <span className="font-mono text-zinc-400">{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Expectation Gaps */}
                {overview.deep_signals.expectation_summary && Object.values(overview.deep_signals.expectation_summary).some(v => v > 0) && (
                  <div className="rounded-lg border border-rose-800/40 bg-rose-950/20 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-rose-400">📊 Expectation vs Reality</h4>
                    <div className="flex gap-3">
                      {overview.deep_signals.expectation_summary.exceeded > 0 && (
                        <div className="text-center">
                          <div className="text-lg font-bold text-emerald-400">{overview.deep_signals.expectation_summary.exceeded}</div>
                          <div className="text-[10px] text-zinc-500">exceeded</div>
                        </div>
                      )}
                      {overview.deep_signals.expectation_summary.met > 0 && (
                        <div className="text-center">
                          <div className="text-lg font-bold text-zinc-300">{overview.deep_signals.expectation_summary.met}</div>
                          <div className="text-[10px] text-zinc-500">met</div>
                        </div>
                      )}
                      {overview.deep_signals.expectation_summary.fell_short > 0 && (
                        <div className="text-center">
                          <div className="text-lg font-bold text-rose-400">{overview.deep_signals.expectation_summary.fell_short}</div>
                          <div className="text-[10px] text-zinc-500">fell short</div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Causal Chains */}
                {overview.deep_signals.causal_chains?.length > 0 && (
                  <div className="rounded-lg border border-purple-800/40 bg-purple-950/20 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-purple-400">🔗 Root cause chains</h4>
                    {overview.deep_signals.causal_chains.slice(0, 3).map((chain, i) => (
                      <div key={i} className="mb-1 text-[12px] text-zinc-300">
                        {chain.map((step, j) => (
                          <span key={j}>
                            {j > 0 && <span className="mx-1 text-purple-500">→</span>}
                            {step}
                          </span>
                        ))}
                      </div>
                    ))}
                  </div>
                )}

                {/* Version Mentions */}
                {overview.deep_signals.version_mentions && Object.keys(overview.deep_signals.version_mentions).length > 0 && (
                  <div className="rounded-lg border border-zinc-700/40 bg-zinc-900/40 p-3">
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">📱 Versions mentioned</h4>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(overview.deep_signals.version_mentions).map(([ver, n]) => (
                        <span key={ver} className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-300">
                          {ver} <span className="text-zinc-500">×{n}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          )}

          {sampleComments.length > 0 && (
            <Card title="Voice of the customer" subtitle="Most informative samples — ranked by review intelligence">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {sampleComments.map((r, i) => {
                  const n = STAR_TO_NUM[r.sentiment] || 3;
                  const cat = r.review_category || "Neutral";
                  const catTone = cat === "Praise" ? "green" : cat === "Complaint" ? "red" : cat === "Suggestion" ? "violet" : "zinc";
                  const lang = (r.language || "?").toUpperCase();
                  const isForeign = lang && lang !== "EN" && lang !== "UNKNOWN" && lang !== "?";
                  const translation = r.translated_text;
                  const intel = reviewIntelligence(r);
                  const isHigh = intel && intel.label === "HIGH";
                  const isLow = intel && intel.label === "LOW";
                  const credHigh = (r._credibility || {}).credibility_label === "HIGH";
                  return (
                    <div
                      key={i}
                      className={`rounded-xl border bg-zinc-900/40 p-3 ${isHigh ? "border-l-2 border-l-emerald-500/70 border-zinc-800" : "border-zinc-800"} ${isLow ? "opacity-80" : ""}`}
                    >
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-[10px] uppercase text-zinc-500">{lang}</span>
                          {intel && typeof intel.composite === "number" && (
                            <span
                              className={`font-mono text-[10px] ${isHigh ? "text-emerald-400" : "text-zinc-600"}`}
                              title={`Review intelligence ${intel.composite.toFixed(2)} (${intel.label})${intel.specificity != null ? ` · specificity ${intel.specificity}, depth ${intel.depth}, actionability ${intel.actionability}, uniqueness ${intel.uniqueness}` : ""}`}
                            >
                              q{intel.composite.toFixed(2)}
                            </span>
                          )}
                          {isHigh && (
                            <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-emerald-300">detailed</span>
                          )}
                          {credHigh && (
                            <span className="rounded border border-emerald-800 bg-emerald-900/40 px-1.5 py-0.5 text-[9px] font-medium text-emerald-400" title="High reviewer credibility — ownership + specificity + depth">🛡 verified depth</span>
                          )}
                        </div>
                        <Badge tone={catTone}>{cat}</Badge>
                      </div>
                      <div className="mb-1 text-amber-400 text-xs">
                        {"★".repeat(n)}<span className="text-zinc-700">{"★".repeat(5 - n)}</span>
                      </div>
                      <p className="text-xs leading-relaxed text-zinc-300" title={r.original}>
                        {(r.original || "").slice(0, 220)}{(r.original || "").length > 220 ? "…" : ""}
                      </p>
                      {isForeign && translation && (
                        <p className="mt-1.5 border-l-2 border-zinc-700 pl-2 text-[11px] italic text-zinc-500" title={translation}>
                          “{translation.slice(0, 200)}{translation.length > 200 ? "…" : ""}”
                        </p>
                      )}
                      <div className="mt-2 flex items-center justify-between border-t border-zinc-800 pt-2 text-[11px]">
                        {r.canonical_reason ? (
                          <span className="text-zinc-500"><span className="text-zinc-600">theme:</span> {r.canonical_reason}</span>
                        ) : <span />}
                        <div className="flex items-center gap-1.5">
                          {(r.sarcasm || {}).is_sarcastic && (
                            <span className="rounded bg-violet-900/40 px-1.5 py-0.5 font-mono text-[9px] text-violet-200" title="Sarcasm detected— sentiment may be flipped">
                              ↝ sarcasm
                            </span>
                          )}
                          {r.buyer_intent && r.buyer_intent !== "UNKNOWN" && (
                            <span className="rounded bg-blue-900/30 px-1.5 py-0.5 font-mono text-[9px] text-blue-200" title="Buyer intent detected">
                              {r.buyer_intent.toLowerCase()}
                            </span>
                          )}
                          {r.emotion && r.emotion !== "neutral" && (
                            <span className="font-mono text-[10px] text-zinc-500">
                              {r.emotion}{typeof r.emotion_score === "number" && r.emotion_score >= 0.4 ? ` · ${(r.emotion_score * 100).toFixed(0)}%` : ""}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* --- Skeptic vs Advocate debate (Company view placement) ---
              In the customer view this lives up top as the featured centerpiece
              (see above). In the company/operator view the machinery dashboard
              leads, so the debate sits here, lower down, non-featured. Gated to
              avoid a duplicate render in customer mode. */}
          {mode !== "customer" && !insufficientData && perReview.length > 0 && (
            <DebatePanel report={res} productName={res?.meta?.query_used} />
          )}

          {/* --- All reviews browser --- the core 'show me what people actually said'
              experience. Surfaces every collected review (not just 3 samples),
              with original + translation, filterable by category/sentiment/language,
              searchable, sortable. This is what a buyer actually wants to read. */}
          {perReview.length > 0 && (
            <ReviewsBrowser reviews={perReview} productName={res?.meta?.query_used} />
          )}

          {verdict && (
            <Card className="border-blue-800/60 bg-blue-950/20">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-900/50 text-blue-300">
                  ✦
                </div>
                <div className="flex-1">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="text-[11px] uppercase tracking-wider text-blue-300">
                      {mode === "customer" ? "Verdict for buyers" : "Verdict for your team"}
                    </span>
                    {res?.meta?.query_used && (
                      <WatchlistButton
                        query={res.meta.query_used}
                        snapshot={{
                          avg_sentiment: overview?.average_sentiment,
                          n_kept: N,
                          platforms: Object.keys(res?.platforms || {}),
                        }}
                      />
                    )}
                  </div>
                  <p className="text-sm leading-relaxed text-zinc-100">{verdict}</p>
                </div>
              </div>
            </Card>
          )}

          <AskInsightMesh report={res} mode={mode} productName={res?.meta?.query_used} />

          {related.length > 0 && (
            <Card title="Related products" subtitle="Others you might want to compare">
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                {related.slice(0, 6).map((r, i) => {
                  const isDemo = r.source === "demo";
                  return (
                    <button
                      key={`${r.query}-${i}`}
                      onClick={() => { setInputMode("search"); setQuery(r.query); setTimeout(() => (isDemo ? loadDemo(r.demo_key) : run()), 30); }}
                      className="flex flex-col items-start gap-1 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5 text-left hover:border-zinc-700 hover:bg-zinc-800"
                    >
                      <span className="truncate w-full text-xs text-zinc-200">{r.query}</span>
                      <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                        {typeof r.avg_sentiment === "number" && <span>{r.avg_sentiment.toFixed(1)}★</span>}
                        {r.similarity > 0 && <span>· {(r.similarity * 100).toFixed(0)}% match</span>}
                        {isDemo && <span className="rounded bg-blue-900/40 px-1 text-blue-200">sample</span>}
                      </div>
                    </button>
                  );
                })}
              </div>
            </Card>
          )}

          {mode === "company" && (
            <details className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-1">
            <summary className="cursor-pointer rounded-xl px-4 py-2.5 text-sm text-zinc-300 hover:bg-zinc-800/60">
              Advanced — clusters, per-review table, debug
            </summary>
            <div className="space-y-3 p-3">
              {canonical.length > 0 && (
                <Card title="All canonical clusters">
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {canonical.map((c) => (
                      <div key={c.cluster_id} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                        <div className="mb-1 flex items-baseline justify-between gap-2">
                          <span className="text-sm font-medium text-zinc-100">{c.reason}</span>
                          <Badge>{c["share_%"]}%</Badge>
                        </div>
                        <div className="text-[11px] text-zinc-500">
                          {c.count} reviews · cohesion {(c.centroid_sim_mean || 0).toFixed(2)}
                        </div>
                        {c.solution?.bullets?.length > 0 && (
                          <ul className="ml-4 mt-2 list-disc text-xs space-y-1 text-zinc-300">
                            {c.solution.bullets.slice(0, 3).map((b, i) => <li key={i}>{b}</li>)}
                          </ul>
                        )}
                        {c.quotes?.length > 0 && (
                          <blockquote className="mt-2 border-l-2 border-zinc-700 pl-2 text-[11px] italic text-zinc-400">
                            “{typeof c.quotes[0] === "string" ? c.quotes[0] : c.quotes[0]?.quote || ""}”
                          </blockquote>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {perReview.length > 0 && (
                <Card title={`Per-review (${perReview.length})`}>
                  <div className="max-h-96 overflow-auto rounded-lg border border-zinc-800">
                    <table className="min-w-full text-left text-xs">
                      <thead className="sticky top-0 bg-zinc-900/90 text-zinc-500 backdrop-blur">
                        <tr>
                          <th className="px-2 py-1.5">Lang</th>
                          <th className="px-2 py-1.5">Sentiment</th>
                          <th className="px-2 py-1.5">Category</th>
                          <th className="px-2 py-1.5">Reason</th>
                          <th className="px-2 py-1.5">Text</th>
                        </tr>
                      </thead>
                      <tbody>
                        {perReview.map((r, i) => (
                          <tr key={i} className="border-t border-zinc-800/60">
                            <td className="px-2 py-1.5 font-mono text-zinc-400">{(r.language || "?").toUpperCase()}</td>
                            <td className="px-2 py-1.5">{r.sentiment || "—"}</td>
                            <td className="px-2 py-1.5">{r.review_category || "—"}</td>
                            <td className="max-w-xs truncate px-2 py-1.5 text-zinc-400" title={r.canonical_reason || ""}>
                              {r.canonical_reason || "—"}
                            </td>
                            <td className="max-w-md truncate px-2 py-1.5 text-zinc-300" title={r.original}>
                              {r.original}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}

              {res?.debug && (
                <Card title="Raw debug payload">
                  <pre className="max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[10px] text-zinc-400">
                    {JSON.stringify(res.debug, null, 2)}
                  </pre>
                </Card>
              )}
            </div>
          </details>
          )}
        </>
      )}
    </div>
  );
}
