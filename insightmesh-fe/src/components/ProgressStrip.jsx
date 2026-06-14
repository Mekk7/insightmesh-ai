// src/components/ProgressStrip.jsx
// Visual progress strip driven by SSE pipeline events.
import React from "react";

// Stages we show in the strip (order = display order). The "stage" field
// from the backend's progress events maps onto these.
const STAGES = [
  { key: "pipeline_started",       label: "Start",     icon: "▶" },
  { key: "mode_determined",        label: "Mode",      icon: "◎" },
  { key: "scrape_started",         label: "Scrape",    icon: "⤓" },
  { key: "scrape_platform_done",   label: "Fetched",   icon: "✓" },
  { key: "clean_dedupe",           label: "Clean",     icon: "✂" },
  { key: "analyze_started",        label: "Analyze",   icon: "⚙" },
  { key: "analyze_done",           label: "Cluster",   icon: "◧" },
  { key: "complete",               label: "Done",      icon: "✓" },
];

// Map a flat event list to per-stage state: not-yet | active | done | error
// When `done=true` is passed, force every stage that has any event to "done"
// AND force the final "complete" stage to "done" so the "Done" pill stops blinking.
function stagesFromEvents(events, done) {
  if (!events?.length) return new Map();
  const map = new Map();
  let lastIdx = -1;
  for (const e of events) {
    const idx = STAGES.findIndex((s) => s.key === e.type || s.key === e.stage);
    if (idx >= 0) {
      map.set(STAGES[idx].key, { ...e, status: "done" });
      lastIdx = Math.max(lastIdx, idx);
    }
  }
  if (done) {
    // Run finished — force complete to done regardless of whether the SSE
    // "complete" event fired (cached runs short-circuit and may skip it).
    map.set("complete", { ...(map.get("complete") || {}), status: "done" });
  } else if (lastIdx >= 0 && lastIdx < STAGES.length - 1) {
    // Mark next as active only if we're still running
    const next = STAGES[lastIdx + 1].key;
    if (!map.has(next)) map.set(next, { status: "active" });
  }
  return map;
}

export default function ProgressStrip({ events = [], error = null, done = false }) {
  const state = stagesFromEvents(events, done);
  const last = events[events.length - 1];

  return (
    <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1">
        {STAGES.map((s, i) => {
          const st = state.get(s.key);
          const status = error ? "error" : (st?.status || "idle");
          const tone =
            status === "done"   ? "border-emerald-700 bg-emerald-900/30 text-emerald-200" :
            status === "active" ? "border-blue-700 bg-blue-900/30 text-blue-200 animate-pulse" :
            status === "error"  ? "border-rose-700 bg-rose-900/30 text-rose-200" :
                                  "border-zinc-700 bg-zinc-900 text-zinc-500";
          return (
            <React.Fragment key={s.key}>
              <div className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${tone}`} title={s.key}>
                <span className="font-mono">{s.icon}</span>
                <span>{s.label}</span>
              </div>
              {i < STAGES.length - 1 && (
                <div className={`h-px w-3 ${status === "done" ? "bg-emerald-700" : "bg-zinc-700"}`} />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Last event details */}
      {last && (
        <div className="text-[11px] text-zinc-400">
          <span className="font-mono">{last.type || last.stage || "event"}</span>
          {typeof last.ts_ms === "number" && <span> · {last.ts_ms}ms</span>}
          {detailFor(last)}
        </div>
      )}
      {error && (
        <div className="mt-1 rounded border border-rose-800/60 bg-rose-950/40 px-2 py-1 text-xs text-rose-200">
          {String(error)}
        </div>
      )}
    </div>
  );
}

function detailFor(e) {
  if (!e) return null;
  const t = e.type || e.stage;
  if (t === "scrape_platform_done") return <> · {e.platform}: {e.fetched} fetched</>;
  if (t === "scrape_platform_error") return <> · {e.platform}: failed — {e.error}</>;
  if (t === "clean_dedupe") return <> · kept {e.kept}</>;
  if (t === "analyze_done") return <> · {e.n_clusters} clusters</>;
  if (t === "analyze_started") return <> · n={e.n}</>;
  if (t === "mode_determined") return <> · {e.user_mode} · "{e.query}"</>;
  if (t === "cache_hit") return <> · cache HIT ({e.key})</>;
  return null;
}
