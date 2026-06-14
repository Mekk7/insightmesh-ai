# backend/api/insightmesh/ask.py
"""
Conversational assistant — "Ask InsightMesh".

The end-user reads their dashboard, has a question ("why is sentiment dropping?",
"what should I prioritize first?", "how does this compare to the category?"),
and types it into a chat box. The assistant gets a tight summary of the current
report plus the conversation history, calls the unified LLM client (Ollama → OpenAI
→ heuristic fallback), and returns a grounded answer.

Why this matters for the product:
- Consumer mode: makes the report self-explanatory. The user doesn't need to be a
  product analyst to extract value — they ask, the assistant answers.
- Company mode: turns the dashboard into a working surface. Internal teams can
  interrogate their customer feedback conversationally.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.utils import llm as llm_client

# RAG retriever — grounds answers in real reviews with citation markers
try:
    from backend.insight.rag.retriever import retrieve_evidence, build_evidence_block
except Exception:
    retrieve_evidence = None
    build_evidence_block = None


def _get_embedder():
    """Lazy import of the shared sentence-transformer used at analyze time.
    Returns None if not yet loaded — the retriever has TF-IDF fallback so it's safe."""
    try:
        from backend.api.endpoints.analyze_reviews import embedder
        return embedder
    except Exception:
        return None


router = APIRouter()


# --- Schemas ----------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' | 'assistant'")
    content: str


class AskInput(BaseModel):
    question: str
    report: Optional[Dict[str, Any]] = None  # the current final_report
    history: Optional[List[ChatMessage]] = None  # prior turns in this conversation
    mode: Optional[str] = "customer"  # 'customer' | 'company' — shifts the assistant tone


# --- Context-shrinking helpers ---------------------------------------------

def _fmt_num(n, suffix="") -> str:
    if n is None:
        return "n/a"
    try:
        f = float(n)
        return f"{f:.2f}{suffix}" if f != int(f) else f"{int(f)}{suffix}"
    except Exception:
        return str(n)


def _summarize_report_for_llm(report: Optional[Dict[str, Any]], max_chars: int = 2400) -> str:
    """Reduce a full final_report to a compact context block (~500 tokens worst case)."""
    if not isinstance(report, dict):
        return "(no analysis available)"
    meta = report.get("meta", {}) or {}
    analysis = report.get("analysis", {}) or {}
    overview = analysis.get("overview", {}) or {}
    per_review = analysis.get("per_review", []) or []

    lines: List[str] = []
    product = meta.get("query_used") or "(unknown product)"
    lines.append(f"PRODUCT: {product}")

    lines.append(
        f"VOLUME: {len(per_review)} comments analyzed across "
        f"{len(overview.get('language_distribution', {}) or {})} languages"
    )

    avg = overview.get("average_sentiment")
    mood = overview.get("mood_index")
    if avg is not None or mood is not None:
        lines.append(
            f"SENTIMENT: avg {_fmt_num(avg, '★')}, mood index {_fmt_num(mood)} (-1 negative to +1 positive)"
        )

    # Top concerns
    clusters = overview.get("canonical_clusters") or []
    if clusters:
        lines.append("TOP CONCERNS:")
        for c in clusters[:5]:
            lines.append(
                f"  - {c.get('reason')} — {c.get('share_%', 0)}% of reviews "
                f"({c.get('count', 0)} mentions)"
            )

    # Customer wishes
    wishes = overview.get("customer_wishes") or []
    if wishes:
        lines.append("CUSTOMER WISHES:")
        for w in wishes[:5]:
            lines.append(f"  - {w.get('wish')} ({w.get('count', 0)} mentions)")

    # Emotion mix
    emotion_mix = overview.get("emotion_mix") or {}
    if emotion_mix:
        top_emos = sorted(emotion_mix.items(), key=lambda x: -x[1])[:4]
        lines.append("EMOTIONS: " + ", ".join(f"{k} ({v})" for k, v in top_emos))

    # Trend
    series = overview.get("sentiment_over_time") or []
    if len(series) >= 2:
        first, last = series[0], series[-1]
        try:
            delta = float(last.get("avg_sentiment", 0) or 0) - float(first.get("avg_sentiment", 0) or 0)
            lines.append(
                f"TREND: {delta:+.2f}★ over {len(series)} days "
                f"(from {first.get('date')} to {last.get('date')})"
            )
        except Exception:
            pass

    # Astroturf
    astroturf = overview.get("astroturf_signals") or {}
    if astroturf.get("flag"):
        lines.append(f"ASTROTURF: {astroturf.get('summary', 'suspicious patterns detected')}")

    # Deep classification signals (if available)
    deep = overview.get("deep_signals") or {}
    if deep:
        # Product intelligence — what comments reveal about the product, INCLUDING
        # intel mined from ecosystem (e.g. game) comments a filter would discard.
        insights_by_aspect = deep.get("product_insights_by_aspect") or {}
        if insights_by_aspect:
            n_eco = deep.get("n_ecosystem_intelligence") or 0
            header = "WHAT REVIEWS REVEAL ABOUT THE PRODUCT (by aspect"
            if n_eco:
                header += f"; {n_eco} of these were mined from ecosystem comments)"
            lines.append(header + "):")
            for asp, b in list(insights_by_aspect.items())[:8]:
                pol = f"+{b.get('positive', 0)}/-{b.get('negative', 0)}"
                lines.append(f"  - {asp} ({pol}):")
                for pi in (b.get("insights") or [])[:2]:
                    tag = "[from ecosystem] " if pi.get("evidence_type") in ("ecosystem", "peripheral") else ""
                    lines.append(f"      • {tag}{pi.get('insight')}")
        if deep.get("intent_by_aspect"):
            lines.append("INTENT BY ASPECT (multi-intent extraction):")
            for asp, counts in list(deep["intent_by_aspect"].items())[:6]:
                parts = [f"{k}={v}" for k, v in counts.items() if v > 0]
                lines.append(f"  - {asp}: {', '.join(parts)}")
        if deep.get("verified_claims"):
            lines.append("VERIFIED CLAIMS (confirmed by 2+ reviewers):")
            for vc in deep["verified_claims"][:5]:
                lines.append(f"  - [{vc['aspect']}] {vc['text']} ({vc['count']}x confirmed)")
        if deep.get("conditions"):
            lines.append("CONDITIONAL SENTIMENTS:")
            for cond in deep["conditions"][:3]:
                lines.append(f"  - {cond['condition']} (flips sentiment: {cond['sentiment_flips']})")
        if deep.get("switching_from") or deep.get("switching_to"):
            lines.append("COMPETITIVE SWITCHING:")
            for prod, n in (deep.get("switching_from") or {}).items():
                lines.append(f"  - Switching FROM {prod} ({n}x)")
            for prod, n in (deep.get("switching_to") or {}).items():
                lines.append(f"  - Switching TO {prod} ({n}x)")
        stages = deep.get("experience_stages") or {}
        active = {k: v for k, v in stages.items() if v > 0}
        if active:
            lines.append(f"REVIEWER EXPERIENCE: {', '.join(f'{k}: {v}' for k, v in active.items())}")

    # Per-platform sentiment
    contributions = (report.get("contributions") or {}).get("per_platform") or []
    if contributions:
        bits = [f"{c.get('platform')}: {_fmt_num(c.get('avg_sentiment_score'))}" for c in contributions if c.get("avg_sentiment_score") is not None]
        if bits:
            lines.append("PER-PLATFORM SENTIMENT: " + ", ".join(bits))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


# --- Heuristic fallback when no LLM available ------------------------------

def _heuristic_answer(question: str, report: Optional[Dict[str, Any]], mode: str) -> str:
    """Cheap rule-based answer when no LLM is available — at least return *something* useful."""
    if not report:
        return "I don't have an analysis to look at yet. Run one first, then ask me about it."

    q = (question or "").lower()
    overview = (report.get("analysis") or {}).get("overview") or {}
    avg = overview.get("average_sentiment")
    clusters = overview.get("canonical_clusters") or []
    wishes = overview.get("customer_wishes") or []
    series = overview.get("sentiment_over_time") or []

    if any(w in q for w in ["should i buy", "buy this", "worth", "recommend"]):
        if avg is None:
            return "Not enough sentiment signal to give you a clean buy/wait answer."
        tag = "strong buy" if avg >= 4.3 else "cautious buy" if avg >= 3.7 else "wait" if avg >= 3.0 else "skip for now"
        top = clusters[0]["reason"] if clusters else "no specific complaint"
        return f"{tag.capitalize()}. Average sentiment is {avg:.1f}★. Biggest concern: {top}."

    if any(w in q for w in ["fix", "priority", "first", "address"]):
        if not clusters:
            return "No clear themes have surfaced yet."
        top = clusters[0]
        return f"Start with: {top['reason']} — {top.get('share_%', 0)}% of reviews mention this ({top.get('count', 0)} comments)."

    if any(w in q for w in ["wish", "request", "feature", "want"]):
        if not wishes:
            return "No feature requests detected in this batch."
        top = wishes[0]
        return f"Top request: \"{top['wish']}\" — {top['count']} mentions."

    if any(w in q for w in ["trend", "dropping", "improving", "over time"]):
        if len(series) < 2:
            return "Not enough timestamped data to assess a trend."
        delta = float(series[-1].get("avg_sentiment", 0) or 0) - float(series[0].get("avg_sentiment", 0) or 0)
        direction = "improving" if delta > 0.05 else "declining" if delta < -0.05 else "stable"
        return f"Sentiment is {direction}: {delta:+.2f}★ across {len(series)} days."

    # Default
    return (
        f"Average sentiment is {avg:.1f}★ from {len((report.get('analysis') or {}).get('per_review', []))} reviews. "
        f"Ask me about top complaints, what to fix first, feature requests, or how sentiment is trending."
    )


# --- Route -----------------------------------------------------------------

@router.post(
    "/ask",
    summary="Conversational follow-up on a product analysis report (RAG-grounded)",
)
def ask(payload: AskInput) -> Dict[str, Any]:
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(400, "Question is empty.")
    if len(q) > 1000:
        raise HTTPException(400, "Question is too long (max 1000 chars).")

    # ---- RAG step: pull the top-K reviews most relevant to the question ----
    # When per_review is available we ground the answer in real evidence with
    # citation markers like [#1], [#3]. The frontend renders those as clickable
    # links to the underlying review.
    per_review: List[Dict[str, Any]] = []
    product_name = ""
    if isinstance(payload.report, dict):
        analysis = payload.report.get("analysis") or {}
        per_review = analysis.get("per_review") or []
        product_name = (payload.report.get("meta") or {}).get("query_used") or ""

    evidence: List[Dict[str, Any]] = []
    if retrieve_evidence and per_review:
        try:
            evidence = retrieve_evidence(
                question=q,
                per_review=per_review,
                product=product_name,
                embedder=_get_embedder(),
                top_k=6,
                min_similarity=0.08,
            )
        except Exception:
            evidence = []

    context = _summarize_report_for_llm(payload.report)
    backend = llm_client.available_backend()

    if backend == "none":
        return {
            "answer": _heuristic_answer(q, payload.report, payload.mode or "customer"),
            "backend": "heuristic",
            "grounded_in_report": payload.report is not None,
            "evidence": evidence,
        }

    mode_blurb = (
        "The user is a consumer trying to decide whether to buy this product. Be direct and useful."
        if (payload.mode or "customer") == "customer"
        else "The user is on the product team. Be specific and prioritize what they should act on."
    )

    evidence_block = build_evidence_block(evidence) if build_evidence_block else "(no evidence retrieved)"
    has_evidence = bool(evidence)

    citation_rule = (
        "You have NUMBERED EVIDENCE QUOTES below from real reviewers. "
        "When you make a specific claim, cite the supporting review(s) with markers like [#1] or [#3,#5] "
        "placed at the end of the relevant sentence. "
        "Only cite numbers that exist in the evidence list. "
        "Don't invent claims that aren't supported by the evidence or the summary."
        if has_evidence
        else "You don't have specific review quotes to cite for this question. Answer from the structured summary only, and don't fabricate citations."
    )

    system_prompt = (
        "You are InsightMesh, a sharp product analysis assistant. "
        "Answer the user's question using ONLY the analysis summary and evidence below. "
        "Be concise (2-4 sentences). Reference specific numbers when relevant. "
        "If the data doesn't support an answer, say so plainly.\n\n"
        f"{mode_blurb}\n\n"
        f"{citation_rule}\n\n"
        f"=== ANALYSIS SUMMARY ===\n{context}\n=== END SUMMARY ===\n\n"
        f"=== EVIDENCE QUOTES ===\n{evidence_block}\n=== END EVIDENCE ==="
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for h in (payload.history or []):
        if h.role in ("user", "assistant") and h.content:
            messages.append({"role": h.role, "content": h.content[:1000]})
    messages.append({"role": "user", "content": q})

    answer = llm_client.chat(messages, temperature=0.2, max_tokens=500)
    if not answer:
        return {
            "answer": _heuristic_answer(q, payload.report, payload.mode or "customer"),
            "backend": "heuristic",
            "grounded_in_report": payload.report is not None,
            "evidence": evidence,
            "fallback_reason": "llm_call_returned_empty",
        }

    return {
        "answer": answer.strip(),
        "backend": backend,
        "grounded_in_report": payload.report is not None,
        "evidence": evidence,
    }


@router.get("/ask/suggestions", summary="Starter questions a user can ask about their analysis")
def ask_suggestions(mode: str = "customer") -> Dict[str, List[str]]:
    """Return a small set of starter questions tailored to the mode."""
    if mode == "company":
        return {
            "suggestions": [
                "What should we fix first?",
                "Where is sentiment trending?",
                "What feature do customers want most?",
                "What are verified facts vs single opinions?",
                "Which products are people switching from/to?",
                "What do long-term owners say vs first-time buyers?",
            ]
        }
    return {
        "suggestions": [
            "Should I buy this?",
            "What's the biggest risk?",
            "Is it good for daily commuting?",
            "What do long-term owners say?",
            "What's confirmed by multiple owners?",
            "What are people switching from?",
        ]
    }
