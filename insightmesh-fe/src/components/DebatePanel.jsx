// insightmesh-fe/src/components/DebatePanel.jsx
//
// Skeptic vs Advocate — the evidence-grounded, retrieval-augmented debate.
//
// THE WOW-FEATURE
// ---------------
// Two AI agents argue about whether to buy/trust the product across multiple
// ROUNDS (Advocate opens → Skeptic rebuts → Advocate answers back). Each claim
// is backed by a clickable [#N] citation that expands to the actual review,
// retrieved per-stance from the FULL analysis (not a flat dump). A neutral Judge
// rules, with confidence HONESTLY calibrated to how much real evidence exists —
// and openly states what it COULD NOT determine.
//
// This is the antidote to a static dashboard: a transparent, auditable argument
// where you can see exactly which real review supports every move.

import React, { useMemo, useRef, useState } from "react";
import { runDebate } from "../lib/api.js";

const STAR = (n) => (n ? "★".repeat(n) + "☆".repeat(Math.max(0, 5 - n)) : "—");

// Renders text with [#3] markers turned into clickable citation chips.
function CitedText({ text, citations, onCite }) {
  const cites = Array.isArray(citations) ? citations : [];
  return (
    <span>
      {text}
      {cites.length > 0 && (
        <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
          {cites.map((n) => (
            <button
              key={n}
              onClick={() => onCite(n)}
              title={`Show review #${n}`}
              className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300 hover:bg-zinc-700 hover:text-white transition"
            >
              #{n}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

// A single bubble in the round-by-round transcript.
function RoundBubble({ round, onCite }) {
  const isAdvocate = round.role === "advocate";
  const accent = isAdvocate
    ? { side: "left", border: "border-emerald-800/50", bg: "bg-emerald-950/15", text: "text-emerald-300", dot: "bg-emerald-500", icon: "⊕", who: "Advocate" }
    : { side: "right", border: "border-rose-800/50", bg: "bg-rose-950/15", text: "text-rose-300", dot: "bg-rose-500", icon: "⊖", who: "Skeptic" };
  const phaseLabel =
    round.phase === "opening" ? "opens" :
    round.phase === "rebuttal" && isAdvocate ? "answers back" :
    round.phase === "rebuttal" ? "rebuts" : round.phase;

  return (
    <div className={`flex ${accent.side === "right" ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[88%] rounded-xl border ${accent.border} ${accent.bg} p-3.5`}>
        <div className="mb-1.5 flex items-center gap-2">
          <span className={`flex h-5 w-5 items-center justify-center rounded-full ${accent.dot} text-[11px] text-white`}>{accent.icon}</span>
          <span className={`text-[10px] uppercase tracking-[0.16em] font-medium ${accent.text}`}>{accent.who}</span>
          <span className="text-[10px] text-zinc-500">{phaseLabel}</span>
        </div>
        {round.summary && <p className="mb-2 text-sm font-medium text-zinc-100">{round.summary}</p>}
        {round.points?.length > 0 && (
          <ul className="space-y-2">
            {round.points.map((p, i) => (
              <li key={i} className="flex items-start gap-2 text-[13px] leading-relaxed text-zinc-200">
                <span className={`mt-1 ${accent.text}`}>•</span>
                <span><CitedText text={p.text} citations={p.citations} onCite={onCite} /></span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default function DebatePanel({ report, productName, featured = false }) {
  const [state, setState] = useState("idle"); // idle | loading | done | error
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [question, setQuestion] = useState("");
  const [highlightN, setHighlightN] = useState(null);
  const evidenceRefs = useRef({});

  const pool = result?.evidence_pool || [];
  const poolByN = useMemo(() => {
    const m = {};
    for (const e of pool) m[e.n] = e;
    return m;
  }, [pool]);

  // Build the ordered transcript. Prefer the new `rounds`; fall back to the
  // legacy advocate/skeptic shape so old backends still render.
  const rounds = useMemo(() => {
    if (Array.isArray(result?.rounds) && result.rounds.length) return result.rounds;
    const legacy = [];
    if (result?.advocate) legacy.push({ role: "advocate", phase: "opening", ...result.advocate });
    if (result?.skeptic) legacy.push({ role: "skeptic", phase: "rebuttal", ...result.skeptic });
    return legacy;
  }, [result]);

  const start = async (q = null) => {
    setState("loading");
    setErr(null);
    try {
      const data = await runDebate(report, q);
      setResult(data);
      setState("done");
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
      setState("error");
    }
  };

  const onCite = (n) => {
    setHighlightN(n);
    const el = evidenceRefs.current[n];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => setHighlightN((cur) => (cur === n ? null : cur)), 2200);
  };

  const judge = result?.judge;
  const leanTone =
    judge?.lean === "buy" ? { text: "text-emerald-300", bg: "bg-emerald-900/30 border-emerald-700", label: "Lean: buy" }
    : judge?.lean === "avoid" ? { text: "text-rose-300", bg: "bg-rose-900/30 border-rose-700", label: "Lean: avoid" }
    : { text: "text-amber-300", bg: "bg-amber-900/30 border-amber-700", label: "It depends" };
  const confTone =
    judge?.confidence === "high" ? "text-emerald-300"
    : judge?.confidence === "medium" ? "text-amber-300"
    : "text-zinc-400";

  const isRag = result?.retrieval === "rag";
  const nReviews = result?.n_relevant || pool.length;

  return (
    <div className={`rounded-2xl border bg-gradient-to-br from-violet-950/20 via-zinc-900/40 to-transparent p-5 shadow-sm ${featured ? "border-violet-600/60 ring-1 ring-violet-700/30" : "border-violet-800/40"}`}>
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-[0.18em] font-medium text-violet-300">Skeptic vs Advocate</span>
            {featured
              ? <span className="rounded-full bg-violet-600 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-white">the verdict, argued</span>
              : <span className="rounded-full bg-violet-900/40 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-violet-200">new</span>}
          </div>
          <h3 className="text-base font-medium text-zinc-100">
            {featured ? "Should you buy this? Two AIs argue it out." : "Let two AIs argue it out — grounded in real reviews"}
          </h3>
          <p className="mt-0.5 text-xs text-zinc-400">
            They argue in rounds, each claim citing a real review. The judge stays honest about how sure it can be — and says what it can't tell.
          </p>
        </div>
      </div>

      {/* Idle / launcher */}
      {state === "idle" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") start(question.trim() || null); }}
              placeholder={`Optional: what do you care about? (e.g. "reliability", "back-seat space")`}
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-violet-600"
            />
            <button
              onClick={() => start(question.trim() || null)}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 transition whitespace-nowrap"
            >
              ⚔ Start the debate
            </button>
          </div>
        </div>
      )}

      {/* Loading */}
      {state === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-6 text-center">
          <div className="mb-2 flex justify-center gap-1.5">
            <span className="h-2 w-2 animate-bounce rounded-full bg-emerald-500" style={{ animationDelay: "0ms" }} />
            <span className="h-2 w-2 animate-bounce rounded-full bg-violet-500" style={{ animationDelay: "150ms" }} />
            <span className="h-2 w-2 animate-bounce rounded-full bg-rose-500" style={{ animationDelay: "300ms" }} />
          </div>
          <p className="text-sm text-zinc-300">The advocate and skeptic are reading the reviews…</p>
          <p className="mt-1 text-[11px] text-zinc-500">Retrieving evidence, arguing in rounds, then a calibrated verdict</p>
        </div>
      )}

      {/* Error */}
      {state === "error" && (
        <div className="rounded-xl border border-rose-800/60 bg-rose-950/30 p-4 text-sm text-rose-200">
          Couldn't run the debate: {String(err)}
          <button onClick={() => start(question.trim() || null)} className="ml-2 underline hover:text-rose-100">retry</button>
        </div>
      )}

      {/* Result */}
      {state === "done" && result && (
        <div className="space-y-4">
          {!result.available || result.note ? (
            <div className="rounded-xl border border-amber-800/50 bg-amber-950/20 p-3 text-sm text-amber-200">
              {result.note || "The debate couldn't be generated."}
            </div>
          ) : null}

          {/* Verdict FIRST when featured — the answer leads, the argument supports it */}
          {judge && featured && (
            <JudgeCard judge={judge} leanTone={leanTone} confTone={confTone} nReviews={nReviews} isRag={isRag} />
          )}

          {/* The round-by-round transcript — the living argument */}
          {rounds.length > 0 && (
            <div>
              <div className="mb-2 flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-[0.16em] font-medium text-zinc-400">The exchange</span>
                {isRag && (
                  <span className="rounded-full border border-zinc-700 bg-zinc-900/60 px-2 py-0.5 text-[9px] text-zinc-400">
                    retrieved from {nReviews} reviews
                  </span>
                )}
              </div>
              <div className="space-y-2.5">
                {rounds.map((rd, i) => (
                  <RoundBubble key={i} round={rd} onCite={onCite} />
                ))}
              </div>
            </div>
          )}

          {/* Verdict AFTER the exchange when not featured (original placement) */}
          {judge && !featured && (
            <JudgeCard judge={judge} leanTone={leanTone} confTone={confTone} nReviews={nReviews} isRag={isRag} />
          )}

          {/* Evidence pool — the reviews agents cited */}
          {pool.length > 0 && (
            <details className="rounded-xl border border-zinc-800 bg-zinc-950/40">
              <summary className="cursor-pointer px-4 py-2.5 text-xs text-zinc-400 hover:text-zinc-200">
                The evidence ({pool.length} reviews the debate drew from) — click any [#N] above to jump here
              </summary>
              <div className="space-y-1.5 p-3">
                {pool.map((e) => (
                  <div
                    key={e.n}
                    ref={(el) => (evidenceRefs.current[e.n] = el)}
                    className={`rounded-lg border p-2.5 text-xs transition ${highlightN === e.n ? "border-violet-500 bg-violet-950/30" : "border-zinc-800 bg-zinc-900/40"}`}
                  >
                    <div className="mb-1 flex items-center gap-2 text-[10px] text-zinc-500">
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-300">#{e.n}</span>
                      <span className="text-amber-400">{STAR(e.stars)}</span>
                      <span>{e.category}</span>
                      {e.is_sarcastic && <span className="rounded bg-violet-900/40 px-1 text-violet-200">sarcastic</span>}
                      {e.platform && <span>· {e.platform}</span>}
                    </div>
                    <p className="text-zinc-300 leading-relaxed">{e.text}</p>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* Steer / re-run */}
          <div className="flex flex-col gap-2 rounded-xl border border-zinc-800 bg-zinc-950/40 p-3 sm:flex-row sm:items-center">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && question.trim()) start(question.trim()); }}
              placeholder="Steer the debate — ask about what YOU care about, then re-run"
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-violet-600"
            />
            <button
              onClick={() => start(question.trim() || null)}
              className="rounded-lg border border-violet-700 bg-violet-900/40 px-4 py-2 text-sm font-medium text-violet-100 hover:bg-violet-900/60 transition whitespace-nowrap"
            >
              ↻ Re-run debate
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// The Judge's verdict card — now includes the "couldn't determine" honesty line.
function JudgeCard({ judge, leanTone, confTone, nReviews, isRag }) {
  return (
    <div className="rounded-xl border border-zinc-700 bg-zinc-900/70 p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-700 text-xs">⚖</span>
        <span className="text-[10px] uppercase tracking-[0.18em] font-medium text-zinc-300">The verdict</span>
        <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${leanTone.bg} ${leanTone.text}`}>{leanTone.label}</span>
        <span className={`text-[10px] uppercase tracking-wider ${confTone}`}>· {judge.confidence} confidence</span>
      </div>
      <p className="text-sm leading-relaxed text-zinc-100">{judge.verdict}</p>

      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
        {judge.strongest_for && (
          <div className="rounded-lg border border-emerald-900/40 bg-emerald-950/10 p-2.5">
            <div className="mb-0.5 text-[10px] uppercase tracking-wider text-emerald-400">Strongest point for</div>
            <p className="text-xs text-zinc-200">{judge.strongest_for}</p>
          </div>
        )}
        {judge.strongest_against && (
          <div className="rounded-lg border border-rose-900/40 bg-rose-950/10 p-2.5">
            <div className="mb-0.5 text-[10px] uppercase tracking-wider text-rose-400">Strongest point against</div>
            <p className="text-xs text-zinc-200">{judge.strongest_against}</p>
          </div>
        )}
      </div>

      {/* HONEST-BY-DEFAULT: what the evidence couldn't settle */}
      {judge.could_not_determine &&
        !/^nothing( major)?\.?$/i.test(String(judge.could_not_determine).trim()) && (
        <div className="mt-2 rounded-lg border border-amber-900/40 bg-amber-950/10 p-2.5">
          <div className="mb-0.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-amber-400">
            <span>⚠</span> Couldn't determine from the reviews
          </div>
          <p className="text-xs text-zinc-300">{judge.could_not_determine}</p>
        </div>
      )}

      {(judge.who_should_buy || judge.who_should_avoid) && (
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2 text-xs">
          {judge.who_should_buy && (
            <div className="text-zinc-400"><span className="text-emerald-400">✓ Right for:</span> {judge.who_should_buy}</div>
          )}
          {judge.who_should_avoid && (
            <div className="text-zinc-400"><span className="text-rose-400">✕ Skip if:</span> {judge.who_should_avoid}</div>
          )}
        </div>
      )}
    </div>
  );
}
