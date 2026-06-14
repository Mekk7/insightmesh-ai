## backend/insight/debate/engine.py
"""
Skeptic vs Advocate — the evidence-grounded, retrieval-augmented debate engine.

THE IDEA (the wow-feature)
--------------------------
Instead of a static dashboard of scores, two AI agents argue about whether to
buy/trust a product, and a neutral Judge rules. Every claim MUST cite a specific
real review by number [#3]. No citation -> the claim doesn't count. The Judge's
confidence is openly calibrated to how much real evidence exists — thin data
yields a low-confidence, hedged verdict, never a fake-confident one.

WHAT'S NEW (v2 — RAG + rebuttal rounds)
---------------------------------------
The old engine dumped <=24 raw comments into each prompt. v2:
  - Indexes EVERY relevant review + its computed signals (category, stars,
    severity, sarcasm, intent) in an EvidenceStore (see evidence_store.py).
  - Each agent RETRIEVES the evidence most relevant to ITS stance:
      Advocate pulls "for" evidence (praise, high stars),
      Skeptic pulls "against" evidence (complaints, low stars, high severity).
    This scales to thousands of reviews and grounds each side in real, filtered
    evidence rather than a flat list.
  - A REBUTTAL round: Skeptic sees the Advocate's opening and attacks it; the
    Advocate then gets one retrieval-backed reply to the Skeptic's strongest
    point. This is what makes the debate feel alive instead of two monologues.
  - The Judge weighs the FULL exchange and returns the verdict that drives the
    top of the dashboard, including what it could NOT determine.

Backwards-compatible: run_debate(overview, per_review, product=, user_question=)
returns the same keys as before, plus `rounds`. The /debate route and
DebatePanel.jsx keep working unchanged.

DESIGN
------
- Pure functions; never raises. Returns a structured transcript.
- Uses the unified LLM client (Ollama -> OpenAI -> none). Without an LLM we
  return a small honest stub explaining the debate needs a model.
- The embedder is the analyzer's existing sentence-transformers singleton,
  imported lazily so a missing/failed import never breaks the debate (we fall
  back to a no-retrieval flat pool in that case).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from backend.utils import llm as llm_client
except Exception:
    llm_client = None

try:
    from backend.insight.debate.evidence_store import EvidenceStore
except Exception:
    EvidenceStore = None  # type: ignore

log = logging.getLogger("insightmesh.debate")

STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}

# Max evidence items handed to any single agent turn (keeps prompts tight).
RETRIEVE_K = 6


# -------------------- embedder (lazy, optional) --------------------
def _get_embed_fn():
    """Return the analyzer's embedder.encode, or None if unavailable.
    Imported lazily and defensively — the debate must never crash because the
    embedding model isn't loadable."""
    try:
        from backend.api.endpoints.analyze_reviews import embedder
        if embedder is not None:
            return lambda texts: embedder.encode(texts, convert_to_numpy=True)
    except Exception as e:
        log.warning("[debate] embedder unavailable, falling back to flat pool: %s", e)
    return None


# -------------------- flat-pool fallback (no embedder) --------------------
def _looks_relevant(r: Dict[str, Any]) -> bool:
    if r.get("is_relevant") is False:
        return False
    txt = (r.get("translated_text") or r.get("original") or "").strip()
    return len(txt) >= 12


def _build_flat_pool(per_review: List[Dict[str, Any]], *, max_items: int = 24) -> List[Dict[str, Any]]:
    """Used only when the embedder can't load. Numbered, quality-ranked pool."""
    pool = [r for r in (per_review or []) if _looks_relevant(r)]
    pool.sort(key=lambda r: float(r.get("quality") or 0), reverse=True)
    pool = pool[:max_items]
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(pool, start=1):
        text = (r.get("translated_text") or r.get("original") or "").strip()
        out.append({
            "n": i,
            "text": text[:400],
            "stars": STAR_TO_NUM.get(r.get("sentiment")),
            "category": r.get("review_category") or "Neutral",
            "theme": r.get("canonical_reason") or None,
            "platform": r.get("platform") or None,
            "is_sarcastic": bool(r.get("is_sarcastic_llm")),
            "severity": 0.0,
            "quality": float(r.get("quality") or 0.0),
        })
    return out


def _format_items_for_prompt(items: List[Dict[str, Any]]) -> str:
    lines = []
    for e in items:
        star = f"{e['stars']}\u2605" if e.get("stars") else "\u2014"
        sarc = " [sarcastic]" if e.get("is_sarcastic") else ""
        lines.append(f"[#{e['n']}] ({star}, {e['category']}{sarc}) {e['text']}")
    return "\n".join(lines)


# -------------------- LLM role calls --------------------
def _parse_points(out: Any) -> Optional[Dict[str, Any]]:
    """Validate an agent JSON response; enforce the citation rule."""
    if not isinstance(out, dict):
        return None
    pts = out.get("points")
    if not isinstance(pts, list):
        return None
    clean = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        txt = (p.get("text") or "").strip()
        cites = p.get("citations") or []
        cites = [int(c) for c in cites
                 if isinstance(c, (int, float)) or (isinstance(c, str) and str(c).isdigit())]
        if txt and cites:  # no citation -> point is dropped
            clean.append({"text": txt, "citations": cites})
    if not clean:
        return None
    return {"summary": (out.get("summary") or "").strip(), "points": clean}


def _agent_turn(role: str, product: str, evidence_text: str, *,
                other_side: Optional[str] = None,
                rebut_point: Optional[str] = None,
                user_question: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One advocate/skeptic turn over the agent's OWN retrieved evidence."""
    if llm_client is None or llm_client.available_backend() == "none":
        return None

    if role == "advocate":
        stance = ("You are the ADVOCATE. Make the strongest HONEST case FOR buying/trusting "
                  "the product, using ONLY the evidence below.")
    else:
        stance = ("You are the SKEPTIC. Make the strongest HONEST case AGAINST buying/trusting "
                  "the product — the real risks and downsides — using ONLY the evidence below.")

    rebut = ""
    if other_side:
        rebut += f"\nThe opposing side argued:\n{other_side}\n"
    if rebut_point:
        rebut += (f"\nDirectly REBUT this specific point from the other side, if the evidence lets you: "
                  f"\"{rebut_point}\". If the evidence does NOT let you rebut it honestly, concede it.\n")
    steer = (f"\nThe user specifically cares about: \"{user_question}\". "
             f"Prioritize evidence about that.\n") if user_question else ""

    prompt = f"""{stance}

PRODUCT: {product}

EVIDENCE (real reviews — cite these by number, e.g. [#3]):
{evidence_text}
{rebut}{steer}
RULES:
- Every point MUST cite at least one evidence number it's based on. No citation = don't make the point.
- Do NOT invent facts not present in the evidence. If the evidence is weak, say so plainly.
- Treat any item marked [sarcastic] with caution; its literal sentiment may be inverted.
- 2-4 points, each one sentence, specific and grounded.

Return ONLY this JSON (no markdown):
{{
  "summary": "one-sentence overall stance",
  "points": [
    {{"text": "a specific claim", "citations": [3, 7]}}
  ]
}}"""

    try:
        out = llm_client.chat_json([{"role": "user", "content": prompt}],
                                   temperature=0.5, max_tokens=600)
        return _parse_points(out)
    except Exception as e:
        log.warning("[debate] %s turn failed: %s", role, e)
        return None


def _judge_turn(product: str, evidence_text: str,
                advocate: Dict[str, Any], skeptic: Dict[str, Any],
                advocate_rebuttal: Optional[Dict[str, Any]],
                *, evidence_level: str, n_relevant: int,
                user_question: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The Judge weighs the full exchange and returns a calibrated verdict."""
    if llm_client is None or llm_client.available_backend() == "none":
        return None

    steer = (f"\nThe user specifically asked about: \"{user_question}\". "
             f"Address it directly in the verdict.\n") if user_question else ""

    prompt = f"""You are the JUDGE in a debate about whether to buy/trust a product.
Two sides argued in rounds, each citing real reviews. Weigh them HONESTLY.

PRODUCT: {product}
EVIDENCE BASE: {n_relevant} real reviews (quality level: {evidence_level}).

ADVOCATE opened: {json.dumps(advocate, ensure_ascii=False)}
SKEPTIC replied: {json.dumps(skeptic, ensure_ascii=False)}
ADVOCATE rebutted: {json.dumps(advocate_rebuttal, ensure_ascii=False) if advocate_rebuttal else "(no rebuttal)"}

EVIDENCE (for reference):
{evidence_text}
{steer}
CRITICAL CALIBRATION RULES:
- Your confidence MUST reflect the evidence base. With few reviews ({n_relevant}) or a
  "thin"/"none" level, confidence is "low" and the verdict is provisional. Never sound
  certain on thin data.
- Be balanced. If both sides have a point, say so. Don't manufacture drama.
- In `could_not_determine`, name anything important you simply don't have enough evidence
  to judge (e.g. "long-term reliability — no reviews mention it"). This honesty is required.

Return ONLY this JSON (no markdown):
{{
  "verdict": "one honest sentence: lean buy / lean avoid / it depends — and why",
  "confidence": "low" | "medium" | "high",
  "lean": "buy" | "avoid" | "depends",
  "strongest_for": "the single most convincing advocate point, in plain words",
  "strongest_against": "the single most convincing skeptic point, in plain words",
  "could_not_determine": "what there wasn't enough evidence to judge (or 'nothing major')",
  "who_should_buy": "one sentence: who this IS right for",
  "who_should_avoid": "one sentence: who should skip it"
}}"""

    try:
        out = llm_client.chat_json([{"role": "user", "content": prompt}],
                                   temperature=0.3, max_tokens=700)
        if not isinstance(out, dict) or not out.get("verdict"):
            return None
        lean = (out.get("lean") or "depends").lower()
        if lean not in ("buy", "avoid", "depends"):
            lean = "depends"
        conf = (out.get("confidence") or "low").lower()
        if conf not in ("low", "medium", "high"):
            conf = "low"
        # Hard guard: never allow high confidence on thin evidence.
        if evidence_level in ("none", "thin") and conf == "high":
            conf = "low"
        elif n_relevant < 15 and conf == "high":
            conf = "medium"
        out["lean"] = lean
        out["confidence"] = conf
        return out
    except Exception as e:
        log.warning("[debate] judge turn failed: %s", e)
        return None


# -------------------- Public API --------------------
def run_debate(overview: Dict[str, Any], per_review: List[Dict[str, Any]], *,
               product: str = "this product",
               user_question: Optional[str] = None) -> Dict[str, Any]:
    """
    Run the full retrieval-augmented Skeptic-vs-Advocate debate. Never raises.

    Returns:
      {
        "available": bool,           # False if no LLM backend
        "product": str,
        "evidence_pool": [{n, text, stars, category, ...}],  # for the UI citation map
        "advocate": {summary, points:[{text, citations}]},
        "skeptic":  {summary, points:[{text, citations}]},
        "advocate_rebuttal": {summary, points} | None,
        "judge":    {verdict, confidence, lean, strongest_for, strongest_against,
                     could_not_determine, who_should_buy, who_should_avoid},
        "rounds":   [ {role, ...} ]  # ordered transcript for rendering
        "evidence_level": "none|thin|moderate|solid",
        "n_relevant": int,
        "retrieval": "rag" | "flat",   # which evidence path was used
        "note": str | None,
      }
    """
    ev = (overview or {}).get("evidence") or {}
    evidence_level = ev.get("level") or "moderate"
    n_relevant = int(ev.get("n_relevant") or 0)

    base: Dict[str, Any] = {
        "available": True,
        "product": product,
        "evidence_pool": [],
        "advocate": None,
        "skeptic": None,
        "advocate_rebuttal": None,
        "judge": None,
        "rounds": [],
        "evidence_level": evidence_level,
        "n_relevant": n_relevant,
        "retrieval": "flat",
        "note": None,
    }

    if llm_client is None or llm_client.available_backend() == "none":
        base["available"] = False
        base["note"] = "The debate needs a language model. Configure an LLM backend to enable it."
        return base

    # ---- Build the evidence layer: RAG store if possible, else flat pool ----
    store = None
    embed_fn = _get_embed_fn()
    if EvidenceStore is not None and embed_fn is not None:
        try:
            store = EvidenceStore(embed_fn)
            if store.add_reviews(per_review) == 0:
                store = None
        except Exception as e:
            log.warning("[debate] evidence store build failed, using flat pool: %s", e)
            store = None

    if store is not None:
        base["retrieval"] = "rag"
        base["evidence_pool"] = store.all_items()
        if not n_relevant:
            base["n_relevant"] = store.size

        # Stance-targeted retrieval. The query is the product (+ user steer).
        topic = f"{product} {user_question}".strip() if user_question else product
        for_items = store.retrieve(topic, stance="for", k=RETRIEVE_K)
        against_items = store.retrieve(topic, stance="against", k=RETRIEVE_K)
        for_text = _format_items_for_prompt(for_items)
        against_text = _format_items_for_prompt(against_items)
        # Combined reference set for the judge (dedupe by citation id)
        seen, combined = set(), []
        for it in for_items + against_items:
            if it["n"] not in seen:
                seen.add(it["n"]); combined.append(it)
        judge_text = _format_items_for_prompt(combined)
    else:
        pool = _build_flat_pool(per_review)
        base["evidence_pool"] = pool
        if not n_relevant:
            base["n_relevant"] = len(pool)
        if not pool:
            base["note"] = "Not enough real reviews to stage a meaningful debate yet."
            return base
        for_text = against_text = judge_text = _format_items_for_prompt(pool)

    if not base["evidence_pool"]:
        base["note"] = "Not enough real reviews to stage a meaningful debate yet."
        return base

    rounds: List[Dict[str, Any]] = []

    # 1) Advocate opens (on "for" evidence)
    advocate = _agent_turn("advocate", product, for_text, user_question=user_question)
    if advocate:
        rounds.append({"role": "advocate", "phase": "opening", **advocate})

    # 2) Skeptic replies (on "against" evidence; sees advocate's case)
    skeptic = _agent_turn(
        "skeptic", product, against_text,
        other_side=json.dumps(advocate, ensure_ascii=False) if advocate else None,
        user_question=user_question,
    )
    if skeptic:
        rounds.append({"role": "skeptic", "phase": "rebuttal", **skeptic})

    # 3) Advocate rebuts the skeptic's strongest point (fresh "for" retrieval)
    advocate_rebuttal = None
    if skeptic and skeptic.get("points"):
        strongest = skeptic["points"][0]["text"]
        # Retrieve evidence specifically about the skeptic's concern, "for" stance
        if store is not None:
            reb_items = store.retrieve(strongest, stance="for", k=RETRIEVE_K)
            reb_text = _format_items_for_prompt(reb_items) or for_text
        else:
            reb_text = for_text
        advocate_rebuttal = _agent_turn(
            "advocate", product, reb_text,
            rebut_point=strongest, user_question=user_question,
        )
        if advocate_rebuttal:
            rounds.append({"role": "advocate", "phase": "rebuttal", **advocate_rebuttal})

    # 4) Judge weighs the full exchange
    judge = None
    if advocate or skeptic:
        judge = _judge_turn(
            product, judge_text,
            advocate or {"summary": "(no case made)", "points": []},
            skeptic or {"summary": "(no case made)", "points": []},
            advocate_rebuttal,
            evidence_level=evidence_level, n_relevant=base["n_relevant"],
            user_question=user_question,
        )

    base["advocate"] = advocate
    base["skeptic"] = skeptic
    base["advocate_rebuttal"] = advocate_rebuttal
    base["judge"] = judge
    base["rounds"] = rounds

    if not (advocate and skeptic and judge):
        base["note"] = "The debate was only partially generated — the model returned incomplete arguments."
    return base
