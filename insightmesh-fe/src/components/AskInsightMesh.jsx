// insightmesh-fe/src/components/AskInsightMesh.jsx
// Conversational follow-up panel. Takes the current analysis report as context
// and lets the user ask anything — backed by the unified LLM client (Ollama first,
// OpenAI fallback, heuristic as last resort).
//
// Phase B.1: every answer is now RAG-grounded. The backend retrieves the top-K
// most relevant reviews via embedding similarity and asks the LLM to cite them
// with markers like [#1] or [#3,#5]. We render those markers as clickable
// chips that open a small popover with the source review + provenance.
//
// Phase A.2: every ask() call is now cancellable. Asking a new question while
// one is in flight aborts the previous request (frontend + backend) so the
// user's last action is always the one being processed. Mirrors ChatGPT/Claude
// behavior — send a new message and the old generation stops immediately.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api.js";

const DEFAULT_SUGGESTIONS_CUSTOMER = [
  "Should I buy this?",
  "What's the biggest red flag?",
  "Is it getting better or worse over time?",
];
const DEFAULT_SUGGESTIONS_COMPANY = [
  "What should we fix first?",
  "What feature do customers want most?",
  "Where is sentiment trending?",
];

// Regex matches citations like [#3], [#1, #4], [#2,#5,#7]
const CITATION_RE = /\[#\s*(\d+(?:\s*,\s*#?\s*\d+)*)\s*\]/g;

const STAR_TO_NUM = { "1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5 };

/**
 * Parse an answer string into a sequence of {type: "text"|"cite", ...} segments,
 * preserving the order so the chat bubble can interleave clickable chips with prose.
 */
function parseCitations(answer) {
  if (!answer || typeof answer !== "string") return [{ type: "text", value: answer || "" }];
  const out = [];
  let lastIdx = 0;
  let match;
  CITATION_RE.lastIndex = 0;
  while ((match = CITATION_RE.exec(answer)) !== null) {
    if (match.index > lastIdx) {
      out.push({ type: "text", value: answer.slice(lastIdx, match.index) });
    }
    // Parse the inner number list — accepts "3" / "3,5" / "3, #5"
    const refs = match[1]
      .split(",")
      .map((s) => s.replace(/[^\d]/g, ""))
      .map((s) => parseInt(s, 10))
      .filter((n) => !Number.isNaN(n));
    out.push({ type: "cite", refs });
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < answer.length) {
    out.push({ type: "text", value: answer.slice(lastIdx) });
  }
  return out;
}

function CitationChip({ refs, evidence, onSelect }) {
  // refs is an array of 1-indexed citation numbers; map to evidence items
  if (!refs || refs.length === 0) return null;
  return (
    <span className="inline-flex items-baseline gap-0.5 align-baseline">
      {refs.map((n, i) => {
        const ev = (evidence || []).find((e) => e.rank === n);
        if (!ev) {
          // Unknown reference — render as muted text rather than disappearing
          return (
            <span key={`bad-${n}-${i}`} className="text-[10px] font-mono text-zinc-600 mx-0.5" title="Citation not found in evidence">
              [#{n}]
            </span>
          );
        }
        const tone = ev.category === "Praise" ? "bg-emerald-900/40 text-emerald-200 border-emerald-700/50 hover:bg-emerald-900/60"
                   : ev.category === "Complaint" ? "bg-rose-900/40 text-rose-200 border-rose-700/50 hover:bg-rose-900/60"
                   : ev.category === "Suggestion" ? "bg-violet-900/40 text-violet-200 border-violet-700/50 hover:bg-violet-900/60"
                   : "bg-zinc-800/60 text-zinc-300 border-zinc-700 hover:bg-zinc-800";
        return (
          <button
            key={`cite-${n}-${i}`}
            onClick={() => onSelect(ev)}
            className={`inline-flex items-baseline gap-0.5 mx-0.5 rounded border px-1 py-0 align-baseline font-mono text-[10px] transition ${tone}`}
            title={`Source ${n}: ${ev.text.slice(0, 120)}...`}
          >
            <span>#{n}</span>
          </button>
        );
      })}
    </span>
  );
}

function CitedAnswer({ content, evidence, onSelectEvidence }) {
  const segments = useMemo(() => parseCitations(content), [content]);
  return (
    <p className="text-zinc-200 leading-relaxed whitespace-pre-wrap">
      {segments.map((seg, i) => {
        if (seg.type === "text") return <span key={i}>{seg.value}</span>;
        return <CitationChip key={i} refs={seg.refs} evidence={evidence} onSelect={onSelectEvidence} />;
      })}
    </p>
  );
}

function EvidencePopover({ ev, onClose }) {
  if (!ev) return null;
  const stars = STAR_TO_NUM[ev.sentiment] || 0;
  const tone = ev.category === "Praise" ? "border-emerald-800/50 bg-emerald-950/30"
             : ev.category === "Complaint" ? "border-rose-800/50 bg-rose-950/30"
             : ev.category === "Suggestion" ? "border-violet-800/50 bg-violet-950/30"
             : "border-zinc-700 bg-zinc-900/60";
  return (
    <div className={`mt-2 rounded-lg border ${tone} px-3 py-2.5 text-xs`}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="rounded bg-zinc-800/70 px-1.5 py-0.5 font-mono text-[9px] text-zinc-300">source #{ev.rank}</span>
          {ev.category && <span className="text-[10px] text-zinc-400">{ev.category}</span>}
          {stars > 0 && (
            <span className="text-amber-400 text-[10px]">
              {"★".repeat(stars)}<span className="text-zinc-700">{"★".repeat(5 - stars)}</span>
            </span>
          )}
          {ev.platform && (
            <span className="rounded bg-zinc-800/70 px-1.5 py-0.5 font-mono text-[9px] uppercase text-zinc-400">{ev.platform}</span>
          )}
          {ev.language && ev.language !== "en" && (
            <span className="rounded bg-zinc-800/70 px-1.5 py-0.5 font-mono text-[9px] uppercase text-zinc-500">{ev.language}</span>
          )}
          {ev.similarity != null && (
            <span className="text-[9px] font-mono text-zinc-500" title={`Similarity to question: ${(ev.similarity * 100).toFixed(0)}%`}>
              {(ev.similarity * 100).toFixed(0)}% match
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 text-[11px]" title="Close">×</button>
      </div>
      <blockquote className="border-l-2 border-zinc-700 pl-2 italic text-zinc-200">
        “{ev.text}”
      </blockquote>
      {(ev.author || ev.published_at) && (
        <div className="mt-1 text-[10px] text-zinc-500">
          {ev.author && <span>by {ev.author}</span>}
          {ev.author && ev.published_at && <span> · </span>}
          {ev.published_at && <span>{String(ev.published_at).slice(0, 10)}</span>}
        </div>
      )}
    </div>
  );
}

export default function AskInsightMesh({ report, mode = "customer", productName }) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  // Each message: {role, content, backend?, evidence?, selectedEvidence?}
  const [chat, setChat] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [suggestions, setSuggestions] = useState(
    mode === "company" ? DEFAULT_SUGGESTIONS_COMPANY : DEFAULT_SUGGESTIONS_CUSTOMER
  );
  const scrollRef = useRef(null);
  // Request cancellation: every new ask() aborts the previous in-flight call
  const askAbortRef = useRef(null);

  const isAbortError = (e) =>
    e?.name === "CanceledError" ||
    e?.name === "AbortError" ||
    e?.code === "ERR_CANCELED" ||
    (typeof e?.message === "string" && /aborted|canceled|cancelled/i.test(e.message));

  // Reset conversation when the report changes (different product / new run).
  // Also cancel any in-flight ask() since the underlying data just changed.
  useEffect(() => {
    if (askAbortRef.current) {
      try { askAbortRef.current.abort(); } catch {}
      askAbortRef.current = null;
    }
    setChat([]);
    setErr(null);
    setLoading(false);
  }, [report?.meta?.cache_key, report?.meta?.query_used]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (askAbortRef.current) {
        try { askAbortRef.current.abort(); } catch {}
      }
    };
  }, []);

  // Fetch starter questions for the current mode
  useEffect(() => {
    let cancelled = false;
    api.get(`/insightmesh/ask/suggestions?mode=${mode}`)
      .then(({ data }) => { if (!cancelled && Array.isArray(data?.suggestions)) setSuggestions(data.suggestions); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [mode]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [chat.length, loading]);

  const ask = async (q) => {
    const text = (q || question).trim();
    if (!text) return;
    // Cancel any in-flight question before starting a new one.
    // The user's LAST question is the only one we care about.
    if (askAbortRef.current) {
      try { askAbortRef.current.abort(); } catch {}
      askAbortRef.current = null;
    }
    const controller = new AbortController();
    askAbortRef.current = controller;

    const userMsg = { role: "user", content: text };
    setChat((c) => [...c, userMsg]);
    setQuestion("");
    setLoading(true);
    setErr(null);
    try {
      const history = chat.map((m) => ({ role: m.role, content: m.content }));
      const { data } = await api.post(
        "/insightmesh/ask",
        { question: text, report, history, mode },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      setChat((c) => [...c, {
        role: "assistant",
        content: data?.answer || "(no response)",
        backend: data?.backend,
        evidence: Array.isArray(data?.evidence) ? data.evidence : [],
      }]);
    } catch (e) {
      if (isAbortError(e)) return; // silently superseded by a newer question
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      if (askAbortRef.current === controller) {
        askAbortRef.current = null;
        setLoading(false);
      }
    }
  };

  const stopAsk = () => {
    if (askAbortRef.current) {
      try { askAbortRef.current.abort(); } catch {}
      askAbortRef.current = null;
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  };

  // Set the highlighted evidence for a given assistant message index
  const selectEvidenceFor = (msgIdx, ev) => {
    setChat((c) => c.map((m, i) => (i === msgIdx ? { ...m, selectedEvidence: ev } : m)));
  };

  const heading = useMemo(
    () => (productName ? `Ask about ${productName}` : "Ask InsightMesh"),
    [productName]
  );

  return (
    <div className="rounded-2xl border border-violet-800/40 bg-gradient-to-br from-violet-950/30 to-zinc-900/60 shadow-sm">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left hover:bg-violet-900/10"
      >
        <div className="flex items-center gap-2.5">
          <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-violet-900/40 text-violet-300">
            <span className="text-sm">✦</span>
          </span>
          <div>
            <div className="text-sm font-medium text-zinc-100">{heading}</div>
            <div className="text-[11px] text-zinc-400">
              {open ? "Ask anything — answers are grounded in real reviews" : "Tap to ask questions about this analysis"}
            </div>
          </div>
        </div>
        <span className={`text-xs text-zinc-500 transition-transform ${open ? "rotate-180" : ""}`}>▾</span>
      </button>

      {open && (
        <div className="border-t border-violet-900/30 px-5 py-4">
          {chat.length === 0 && (
            <div className="mb-3">
              <p className="mb-2 text-[11px] uppercase tracking-wider text-zinc-500">Try one of these</p>
              <div className="flex flex-wrap gap-1.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="rounded-full border border-violet-800/40 bg-violet-950/30 px-3 py-1 text-[11px] text-violet-200 hover:bg-violet-900/40"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chat.length > 0 && (
            <div
              ref={scrollRef}
              className="mb-3 max-h-96 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 space-y-3"
            >
              {chat.map((m, i) => (
                <div key={i} className="text-sm">
                  {m.role === "user" ? (
                    <div className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-blue-900/40 px-3 py-2 text-zinc-100">
                        {m.content}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start gap-2">
                      <span className="mt-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-violet-900/40 text-[11px] text-violet-300">✦</span>
                      <div className="flex-1 min-w-0">
                        <CitedAnswer
                          content={m.content}
                          evidence={m.evidence}
                          onSelectEvidence={(ev) => selectEvidenceFor(i, ev)}
                        />
                        {/* Selected evidence popover */}
                        {m.selectedEvidence && (
                          <EvidencePopover
                            ev={m.selectedEvidence}
                            onClose={() => selectEvidenceFor(i, null)}
                          />
                        )}
                        {/* Evidence summary footer */}
                        <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-zinc-600">
                          <div className="flex items-center gap-1.5">
                            {m.backend && (
                              <span className="font-mono" title="LLM backend used">via {m.backend}</span>
                            )}
                            {m.evidence && m.evidence.length > 0 && (
                              <>
                                {m.backend && <span>·</span>}
                                <span title="Number of reviews cited as evidence">{m.evidence.length} sources</span>
                              </>
                            )}
                          </div>
                          {m.evidence && m.evidence.length > 0 && (
                            <button
                              onClick={() => selectEvidenceFor(i, m.selectedEvidence ? null : m.evidence[0])}
                              className="text-violet-400 hover:text-violet-300"
                              title="Browse all source quotes"
                            >
                              {m.selectedEvidence ? "hide sources" : "show sources"}
                            </button>
                          )}
                        </div>
                        {/* Source navigator strip (visible when popover open) */}
                        {m.selectedEvidence && m.evidence && m.evidence.length > 1 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {m.evidence.map((ev) => (
                              <button
                                key={ev.rank}
                                onClick={() => selectEvidenceFor(i, ev)}
                                className={`rounded border px-1.5 py-0.5 font-mono text-[9px] transition ${
                                  m.selectedEvidence?.rank === ev.rank
                                    ? "border-violet-500 bg-violet-900/50 text-violet-100"
                                    : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:bg-zinc-800"
                                }`}
                              >
                                #{ev.rank}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {loading && (
                <div className="flex items-center gap-2 text-xs text-zinc-500">
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-violet-900/40 text-violet-300">✦</span>
                  <span className="flex gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400" style={{ animationDelay: "0ms" }} />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400" style={{ animationDelay: "150ms" }} />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400" style={{ animationDelay: "300ms" }} />
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKey}
              placeholder={chat.length > 0 ? "Follow up…" : "Ask anything about this analysis"}
              disabled={!report}
              className="flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2 text-sm outline-none placeholder:text-zinc-500 focus:border-violet-600 focus:ring-1 focus:ring-violet-700 disabled:opacity-50"
            />
            <button
              onClick={loading ? stopAsk : () => ask()}
              disabled={!loading && (!question.trim() || !report)}
              className={`rounded-xl px-4 py-2 text-sm font-medium text-white transition disabled:opacity-50 ${
                loading ? "bg-rose-600 hover:bg-rose-500" : "bg-violet-600 hover:bg-violet-500"
              }`}
              title={loading ? "Stop the current question" : "Send"}
            >
              {loading ? "■" : "Send"}
            </button>
          </div>

          {err && (
            <div className="mt-2 rounded-lg border border-rose-800/60 bg-rose-950/30 px-3 py-2 text-xs text-rose-200">
              {String(err)}
            </div>
          )}

          {!report && (
            <p className="mt-2 text-[11px] text-zinc-500">Run an analysis first, then ask questions about it.</p>
          )}
        </div>
      )}
    </div>
  );
}
