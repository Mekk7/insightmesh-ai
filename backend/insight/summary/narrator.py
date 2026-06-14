# backend/insight/summary/narrator.py
"""
Smart Summary Narrator.

The dashboard has tons of cards. But when a user opens it, they want ONE thing
first: "tell me what's going on here, in plain English."

This module produces a rich narrative summary that synthesizes everything:
  - TrustScore + grade
  - Average sentiment + forecast direction
  - Top complaint themes with severity
  - Top praise themes
  - Buyer intent distribution
  - Persona breakdown
  - Aspect breakdown highlights
  - Customer effort signals
  - Risk register hits

It produces TWO flavors of summary (consumer + company) so the frontend can
just swap when the mode toggle changes — no re-fetch needed.

Resolution:
  1. If LLM available (Ollama → OpenAI), use it for narrative generation
  2. Fall back to template-based prose using the same input data
  3. Never empty — always returns something for each mode

Output shape:
  {
    "consumer": {
      "headline": "Strong buy with eyes on battery",
      "summary":  "Across 247 reviewers in 4 languages, this product earns...",
      "key_takeaways": ["...", "...", "..."],
      "recommendation": "Buy with awareness of ...",
      "best_quote": "I've owned mine for two years and...",
    },
    "company": {
      "headline": "Healthy product, one growth lever",
      "summary":  "Sentiment is steady at 4.2 stars with ...",
      "key_takeaways": ["...", "...", "..."],
      "strategic_priority": "Address support friction in Q1...",
      "marketing_lead": "Lean on the noise-cancellation story...",
    }
  }
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from backend.utils import llm as llm_client
except Exception:
    llm_client = None

log = logging.getLogger("insightmesh.narrator")


# -------------------- Input extraction --------------------
def _facts_from_overview(overview: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Extract a compact dict of facts the LLM (or template) will consume."""
    if not isinstance(overview, dict):
        return {}

    ts = overview.get("trust_score") or {}
    ev = overview.get("evidence") or {}
    forecast = overview.get("sentiment_forecast") or {}
    dh = (overview.get("buyer_intent_summary") or {}).get("decision_health") or {}
    aspects = ((overview.get("aspect_sentiment") or {}).get("aspects") or [])[:6]
    risks = (overview.get("risk_register") or [])[:3]
    roadmap = (overview.get("next_version_roadmap") or [])[:3]
    praise = (overview.get("what_users_love") or [])[:3]
    personas = (overview.get("personas") or [])[:4]
    angles = (overview.get("marketing_angles") or [])[:3]
    effort = overview.get("customer_effort") or {}
    sarcasm = overview.get("sarcasm_stats") or {}
    astroturf = overview.get("astroturf_signals") or {}

    return {
        "product": query or "the product",
        "n_reviews": int(sarcasm.get("total") or 0),
        # Honesty signals — the narrator MUST respect these so it never
        # manufactures a crisis from thin data or a merely-average score.
        "insufficient_data": bool(ts.get("insufficient_data") or ev.get("insufficient")),
        "n_relevant": ev.get("n_relevant"),
        "evidence_level": ev.get("level"),
        "avg_sentiment": overview.get("average_sentiment"),
        "trust_score": ts.get("score"),
        "trust_grade": ts.get("grade"),
        "trust_verdict": ts.get("verdict"),
        "trust_confidence": ts.get("confidence"),
        "languages": list((overview.get("language_distribution") or {}).keys())[:5],
        "forecast_trend": forecast.get("trend"),
        "forecast_narrative": forecast.get("narrative"),
        "decision_health": {
            "recommend_pct": dh.get("recommend_pct"),
            "return_pct":    dh.get("return_pct"),
            "avoid_pct":     dh.get("avoid_pct"),
            "buy_pct":       dh.get("buy_pct"),
            "net_intent":    dh.get("net_intent"),
        },
        "top_aspects_loved":     [a for a in aspects if (a.get("avg_sentiment_stars") or 0) >= 4],
        "top_aspects_struggling": [a for a in aspects if (a.get("avg_sentiment_stars") or 5) < 3],
        "top_complaints": [
            {"reason": r.get("complaint"), "severity": r.get("severity"), "share_pct": r.get("share_pct"), "category": r.get("category_label")}
            for r in risks
        ],
        "roadmap_top": [
            {"reason": r.get("complaint"), "share_pct": r.get("share_pct"), "projected_uplift": (r.get("counterfactual") or {}).get("sentiment_delta")}
            for r in roadmap
        ],
        "praise_themes": [{"theme": p.get("theme"), "count": p.get("count")} for p in praise],
        "personas": [
            {"label": p.get("label"), "pct": p.get("pct"), "verdict": p.get("verdict"), "stars": p.get("avg_sentiment_stars"), "top_concern": p.get("top_concern")}
            for p in personas
        ],
        "marketing_angles": [{"theme": a.get("theme"), "mentions": a.get("mentions")} for a in angles],
        "customer_effort": {
            "score": effort.get("score"),
            "label": effort.get("label"),
            "affected_share_pct": effort.get("affected_share_pct"),
            "top_pain": (effort.get("breakdown") or [{}])[0].get("label") if effort.get("breakdown") else None,
        } if effort else None,
        "astroturf_flag": bool(astroturf.get("flag")),
        "sarcasm_pct": round(100 * (sarcasm.get("flagged_count", 0) / max(1, sarcasm.get("total", 1))), 1),
    }


# -------------------- LLM path --------------------
def _llm_narrate(facts: Dict[str, Any], product_context: str = "",
                 summary_brief: str = "") -> Optional[Dict[str, Any]]:
    """Try to generate consumer + company narratives using the unified LLM client."""
    if llm_client is None:
        return None
    backend = llm_client.available_backend()
    if backend == "none":
        return None

    facts_blob = json.dumps(facts, default=str, ensure_ascii=False, indent=2)[:4000]
    # Adaptive brief from the Intelligence Synthesizer — data-shape directives that
    # change HOW the summary is written (polarized → don't average; dominant theme →
    # make it the headline; low confidence → qualify everything; etc.). It is the
    # highest-priority steer, so it goes at the very top of the prompt.
    brief_block = ""
    if summary_brief and summary_brief.strip():
        brief_block = (
            "ADAPTIVE BRIEF — these data-shape directives OVERRIDE the default tone. "
            "Follow them exactly when writing both narratives:\n"
            f"{summary_brief.strip()}\n\n"
        )
    # Product Intelligence context anchors the narrative to what the product IS, so a
    # symptom is read in-domain. "Ears get hot" on headphones is a comfort/material
    # issue; the same phrase on a laptop is a thermal defect ("overheating"). Without
    # this block the model guessed the domain and reached for hardware-defect language.
    ctx_block = ""
    if product_context:
        ctx_block = (
            "PRODUCT CONTEXT — what this product IS. Interpret every symptom in THIS domain "
            "and use this product category's natural vocabulary (do not borrow defect terms "
            "from other categories — e.g. headphone ear warmth is a comfort issue, not "
            '"overheating"):\n'
            f"{product_context}\n\n"
        )
    prompt = f"""You are InsightMesh, an expert product-insights analyst.

{brief_block}You will produce a JSON object with two narratives about a product, derived from the FACTS below.
Write in confident, plain English. NEVER invent data not in FACTS. NEVER say "I don't know".
If a field is missing, simply skip it. Always be honest about what the data shows, including weaknesses.

{ctx_block}CRITICAL HONESTY RULES:
- Do NOT manufacture a crisis. A mid TrustScore (40-60) is NOT automatically "red flags" or "trust eroding". Only use alarming language if there are CRITICAL/HIGH complaints in top_complaints, or return_pct/avoid_pct above ~15%.
- If insufficient_data is true, say plainly that there isn't enough real review signal yet for a confident verdict. Do not invent a recommendation — tell them to wait for more reviews.
- A low score caused only by a small sample is a CONFIDENCE problem, not a product problem. Say so.
- Ground every claim in the actual themes/quotes, not in the score number.

PRODUCT: {facts.get("product")}
FACTS:
{facts_blob}

Produce exactly this JSON shape (no extra keys, no markdown fences):
{{
  "consumer": {{
    "headline": "A 6-10 word punchy verdict aimed at a potential buyer",
    "summary": "A 3-4 sentence story for someone deciding whether to buy. Cover: who loves it, who struggles, what to watch out for. Use the actual numbers (TrustScore, recommend%, return%) when relevant. Reference specific aspects and personas by name.",
    "key_takeaways": ["3-5 short factual bullets", "each under 14 words", "decision-relevant"],
    "recommendation": "One sentence: should they buy, wait, or skip? Be direct."
  }},
  "company": {{
    "headline": "A 6-10 word strategic verdict aimed at a product team",
    "summary": "A 3-4 sentence story for the product/marketing team. Cover: where they're winning, where they're losing, what's most urgent. Reference actual numbers and specific aspects/personas.",
    "key_takeaways": ["3-5 short factual bullets", "each under 14 words", "operationally-relevant"],
    "strategic_priority": "One sentence: what should ship next quarter? Be specific.",
    "marketing_lead": "One sentence: what's the strongest praise theme to lean on in marketing?"
  }}
}}
"""

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=1400,
        )
        if not isinstance(parsed, dict):
            return None
        # Validate shape — must have both consumer and company sub-objects with non-empty headline+summary
        for mode in ("consumer", "company"):
            sub = parsed.get(mode)
            if not isinstance(sub, dict):
                return None
            if not (sub.get("headline") and sub.get("summary")):
                return None
        return parsed
    except Exception as e:
        log.warning("[narrator] LLM call failed: %s", e)
        return None


# -------------------- Template fallback --------------------
def _template_narrate(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic narrative generator — used when LLM is unavailable.
    Stitches together plain-English sentences from the structured facts.

    HONESTY RULES (the whole point of this project):
      - A merely-average score is NOT a crisis. We only use crisis/red-flag
        language when there are GENUINELY severe complaints (CRITICAL/HIGH in
        the risk register) or a real return-rate signal — never just because
        the number landed at 51.
      - When evidence is insufficient, we say so plainly instead of inventing
        a verdict.
    """
    product = facts.get("product") or "this product"
    n = facts.get("n_reviews") or 0
    trust = facts.get("trust_score")
    grade = facts.get("trust_grade") or ""
    avg = facts.get("avg_sentiment")
    dh = facts.get("decision_health") or {}
    trend = facts.get("forecast_trend") or "stable"

    loved = facts.get("top_aspects_loved") or []
    struggling = facts.get("top_aspects_struggling") or []
    complaints = facts.get("top_complaints") or []
    praise = facts.get("praise_themes") or []
    personas = facts.get("personas") or []
    effort = facts.get("customer_effort")
    angles = facts.get("marketing_angles") or []

    # ---- Honest signal reads -------------------------------------------
    insufficient = bool(facts.get("insufficient_data"))
    n_relevant = facts.get("n_relevant")
    # Are there ACTUAL severe problems? Only these justify crisis language.
    has_severe = any((c.get("severity") in ("CRITICAL", "HIGH")) for c in complaints)
    return_pct = float(dh.get("return_pct") or 0)
    avoid_pct = float(dh.get("avoid_pct") or 0)
    real_red_flags = has_severe or return_pct >= 15 or avoid_pct >= 15

    # ===================================================================
    # INSUFFICIENT-DATA PATH — say what's true: we can't be sure yet.
    # ===================================================================
    if insufficient:
        nrel_txt = f"{n_relevant} real review{'s' if (n_relevant or 0) != 1 else ''}" if n_relevant is not None else "very few real reviews"
        honest_summary = (
            f"We only found {nrel_txt} for {product} so far — not enough to judge "
            f"confidently. Here's the early read, but treat it as a first impression, "
            f"not a verdict."
        )
        c_takeaways = []
        if praise:
            c_takeaways.append(f"Early positive: {praise[0].get('theme')}")
        if complaints:
            c_takeaways.append(f"Early concern: {complaints[0].get('reason')}")
        c_takeaways.append("Not enough reviews yet for a confident call")
        return {
            "consumer": {
                "headline": "Too early to call",
                "summary": honest_summary,
                "key_takeaways": c_takeaways[:4],
                "recommendation": "Check back when there are more reviews — the signal is too thin right now.",
            },
            "company": {
                "headline": "Not enough signal yet",
                "summary": (
                    f"Only {nrel_txt} surfaced for {product}. We're not going to manufacture "
                    f"a verdict from this little data. Collect more feedback before acting."
                ),
                "key_takeaways": c_takeaways[:4],
                "strategic_priority": "Gather more reviews before drawing conclusions.",
                "marketing_lead": "",
            },
        }

    # ----- Consumer narrative -----
    if trust and trust >= 75:
        c_head = f"Strong buy{' with eyes open' if struggling else ''}"
        c_rec = "Buy with confidence — most reviewers stand behind it."
    elif trust and trust >= 55:
        c_head = "Cautious buy — solid but uneven"
        c_rec = f"Worth considering, but check {struggling[0].get('aspect') if struggling else 'the weak spots'} before committing."
    elif trust and trust >= 40:
        # Mid score: ONLY call it red flags if real problems exist.
        if real_red_flags:
            c_head = "Wait or skip — real red flags"
            c_rec = "Wait for the next version or look at alternatives."
        else:
            c_head = "Mixed — fine but not a standout"
            c_rec = "Okay buy if it fits your needs; reviewers are split, with no dealbreakers."
    else:
        if real_red_flags:
            c_head = "Skip for now"
            c_rec = "Strong signals against. Look at alternatives."
        else:
            c_head = "Limited, mixed feedback"
            c_rec = "Hard to recommend strongly yet — the feedback is light and split."

    parts = []
    if n and avg is not None:
        parts.append(f"Across {n} reviewers, {product} averages {avg:.1f}★" + (f" with a TrustScore of {int(trust)}/100 ({grade.lower()})." if trust else "."))
    if loved:
        names = ", ".join((a.get("aspect") or "").replace("_", " ") for a in loved[:3])
        parts.append(f"Reviewers love {names}.")
    if struggling:
        names = ", ".join((a.get("aspect") or "").replace("_", " ") for a in struggling[:2])
        parts.append(f"The weak spots are {names}.")
    if dh.get("recommend_pct") and dh["recommend_pct"] >= 25:
        parts.append(f"{dh['recommend_pct']:.0f}% of reviewers actively recommend it.")
    if return_pct >= 15:
        parts.append(f"But {return_pct:.0f}% say they're returning it — a warning sign.")
    if trend == "rising":
        parts.append("Recent sentiment is improving.")
    elif trend == "falling":
        parts.append("Recent sentiment is declining — this may get worse.")
    c_summary = " ".join(parts) if parts else f"Limited signal yet on {product}."

    c_takeaways = []
    if loved:
        c_takeaways.append(f"Strongest at: {(loved[0].get('aspect') or '').replace('_', ' ')}")
    if struggling:
        c_takeaways.append(f"Weakest at: {(struggling[0].get('aspect') or '').replace('_', ' ')}")
    if dh.get("recommend_pct") is not None:
        c_takeaways.append(f"{dh['recommend_pct']:.0f}% recommending, {dh.get('return_pct', 0):.0f}% returning")
    if effort and effort.get("label"):
        c_takeaways.append(f"Customer journey: {effort['label'].lower()} effort")
    if trend != "stable":
        c_takeaways.append(f"Sentiment is {trend} recently")
    if personas:
        diverse = ", ".join(p.get("label", "") for p in personas[:2])
        c_takeaways.append(f"Different verdicts from {diverse}")

    # ----- Company narrative -----
    if trust and trust >= 75:
        co_head = "Healthy product — protect the gains"
        co_priority = f"Focus on {struggling[0].get('aspect') if struggling else 'incremental polish'} to push from Strong to Excellent."
    elif trust and trust >= 55:
        co_head = "Solid foundation with clear next move"
        co_priority = f"Ship a fix for the top complaint: {complaints[0].get('reason') if complaints else 'the highest-share issue'}."
    elif trust and trust >= 40:
        # Mid score: crisis language ONLY when real severe issues exist.
        if real_red_flags:
            co_head = "Trust eroding — intervene now"
            co_priority = f"Address {complaints[0].get('reason') if complaints else 'the urgent issue'} in the next release."
        else:
            co_head = "Mixed reception — no single crisis"
            co_priority = (
                f"No severe issues flagged; chip away at {complaints[0].get('reason') if complaints else 'the most-mentioned theme'} "
                f"and amplify what's working."
            )
    else:
        if real_red_flags:
            co_head = "Crisis — immediate action required"
            co_priority = "Treat the risk register as urgent. Engineering pivot recommended."
        else:
            co_head = "Weak but unclear signal"
            co_priority = "Gather more feedback; nothing severe is flagged despite the low score."

    co_parts = []
    if n and trust is not None:
        co_parts.append(f"{product} sits at TrustScore {int(trust)}/100 ({grade.lower()}) across {n} reviewers.")
    if complaints and complaints[0].get("severity") in ("CRITICAL", "HIGH"):
        co_parts.append(f"Top urgent issue: {complaints[0].get('reason')} ({complaints[0].get('severity', '').lower()}, {complaints[0].get('share_pct', 0):.0f}% of reviews).")
    elif complaints:
        co_parts.append(f"Most-mentioned theme: {complaints[0].get('reason')} ({complaints[0].get('share_pct', 0):.0f}% of reviews) — not flagged as severe.")
    if praise and praise[0].get("theme"):
        co_parts.append(f"Strongest praise theme: '{praise[0].get('theme')}' with {praise[0].get('count')} mentions.")
    if effort and effort.get("label") and effort["label"] in ("Heavy", "Punishing"):
        co_parts.append(f"Customer effort is {effort['label'].lower()} ({effort['affected_share_pct']:.0f}% affected) — top pain: {effort.get('top_pain')}.")
    if trend == "falling":
        co_parts.append("Forecast warning: sentiment is declining.")
    elif trend == "rising":
        co_parts.append("Forecast tailwind: sentiment is rising.")
    co_summary = " ".join(co_parts) if co_parts else f"Limited signal yet for {product}."

    co_takeaways = []
    if complaints:
        co_takeaways.append(f"#1 to address: {complaints[0].get('reason')}")
    if praise:
        co_takeaways.append(f"#1 to amplify: {praise[0].get('theme')}")
    if effort and effort.get("score"):
        co_takeaways.append(f"Customer effort score: {int(effort['score'])}/100 ({effort.get('label', '').lower()})")
    if dh.get("net_intent") is not None:
        co_takeaways.append(f"Net buyer intent: {dh['net_intent']:+.2f}")
    if angles:
        co_takeaways.append(f"PR angle: '{angles[0].get('theme')}'")
    if facts.get("astroturf_flag"):
        co_takeaways.append("⚠ astroturf signals detected — investigate")

    mk_lead = ""
    if angles:
        mk_lead = f"Lean on '{angles[0].get('theme')}' — {angles[0].get('mentions')} reviewers back this claim."
    elif praise:
        mk_lead = f"The praise theme '{praise[0].get('theme')}' is your strongest customer-voice asset."

    return {
        "consumer": {
            "headline": c_head,
            "summary":  c_summary,
            "key_takeaways": c_takeaways[:5],
            "recommendation": c_rec,
        },
        "company": {
            "headline": co_head,
            "summary":  co_summary,
            "key_takeaways": co_takeaways[:5],
            "strategic_priority": co_priority,
            "marketing_lead": mk_lead,
        },
    }


# -------------------- Public API --------------------
def build_smart_summary(overview: Dict[str, Any], query: str = "", product_context: str = "",
                        summary_brief: str = "") -> Optional[Dict[str, Any]]:
    """
    Returns a dict with `consumer` and `company` narratives. Never raises.
    Returns None only if `overview` is empty/invalid.

    `product_context` is the compact Product-Intelligence string (category,
    segment, aspects). When supplied it's injected into the LLM prompt so symptoms
    are interpreted in-domain (headphone ear warmth = comfort, not "overheating").

    `summary_brief` is the Intelligence Synthesizer's adaptive brief — data-shape
    instructions (polarized / dominant-theme / low-confidence / temporal trend /
    cross-insights) that steer HOW the summary is written. When supplied it is
    prepended to the prompt so the narrative adapts to what the data actually shows.
    """
    if not isinstance(overview, dict) or not overview:
        return None

    facts = _facts_from_overview(overview, query or "")

    # If we have essentially nothing, return None so the frontend skips the card
    has_signal = bool(
        facts.get("n_reviews", 0) >= 1
        or facts.get("trust_score") is not None
        or facts.get("top_complaints")
        or facts.get("praise_themes")
    )
    if not has_signal:
        return None

    # Try LLM first
    llm_result = _llm_narrate(facts, product_context=product_context or "",
                              summary_brief=summary_brief or "")
    if llm_result:
        # Attach which backend was used so frontend can show "AI-generated" vs heuristic
        llm_result["_source"] = "llm"
        return llm_result

    # Fall back to template
    result = _template_narrate(facts)
    result["_source"] = "heuristic"
    return result
