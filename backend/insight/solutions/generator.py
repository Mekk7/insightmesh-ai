# backend/insight/solutions/generator.py
# Universal, guarded solution generator for feedback clusters.
# - No product hard-coding. Works from cluster reason text + intent mix.
# - Optional lightweight RAG over local docs (env RAG_DOCS_DIRS).
# - Optional LLM refinement if OPENAI_API_KEY is set (kept short, guarded).
# - Emits 1–3 concrete bullets + optional backlog note + sources + confidence.

from __future__ import annotations
import os, re, json, glob, pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

# Optional OpenAI (passed from caller; we don't import at module level)
OpenAIClient = Any

# ---------------------------- Config ----------------------------

TEXT_EXTS = {".md", ".txt", ".rst", ".html", ".htm", ".json", ".yaml", ".yml", ".ini"}
MAX_FILE_BYTES = 1_000_000  # 1MB cap per file
CHUNK_LEN = 900
CHUNK_OVERLAP = 180

RAG_DIRS_ENV = "RAG_DOCS_DIRS"  # comma-separated directories for local knowledge (release notes, FAQs, etc.)

NEG_CUES  = r"(?:does(?:\s*not|n't)|won't|wont|cant|can't|cannot|error|fail(?:ed|s)?|crash(?:es)?|bug|broken|drops|lag|slow|stuck|freeze|frozen|hang|no\s+support|not\s+work|inaccurate)"
SUGG_CUES = r"(?:should|please\s+add|feature\s+request|would\s+love|could\s+you|let\s+us|add|allow|enable|support\s+for|option\s+to)"
RISK_CUES = r"(?:safety|unsafe|privacy|security|data\s+loss|lost\s+data|crash(?:ed)?\s+and\s+lost|accessibility|wheelchair|disabled|emergency)"

# Theme keyword groups (broad, product-agnostic)
THEME_KEYWORDS: Dict[str, List[str]] = {
    "stability":     ["crash", "error", "failed", "fail", "bug", "hang", "freeze", "frozen", "stuck"],
    "performance":   ["lag", "slow", "delay", "latency", "janky", "sluggish"],
    "connectivity":  ["disconnect", "drops", "bluetooth", "wifi", "network", "offline"],
    "auth_override": ["phone", "watch", "key", "keycard", "unlock", "lock", "handle", "latch", "door", "access"],
    "cold_weather":  ["ice", "icy", "frozen", "winter", "cold", "snow"],
    "docs_comms":    ["how", "help", "manual", "doc", "documentation", "faq"],
    "accessibility": ["wheelchair", "mute", "blind", "deaf", "voice", "siri", "alexa", "assistant", "hands-free"],
}

# ---------------------------- Utils ----------------------------

def _read_text_fast(path: pathlib.Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        ext = path.suffix.lower()
        if ext not in TEXT_EXTS:
            return None
        raw = path.read_bytes()
        for enc in ("utf-8", "latin-1"):
            try:
                return raw.decode(enc, errors="ignore")
            except Exception:
                continue
    except Exception:
        return None
    return None

def _chunk_text(t: str, max_len: int = CHUNK_LEN, overlap: int = CHUNK_OVERLAP) -> List[str]:
    import re as _re
    t = _re.sub(r"\s+", " ", (t or "").strip())
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    out, i = [], 0
    step = max_len - overlap
    while i < len(t):
        out.append(t[i:i+max_len])
        if i + max_len >= len(t):
            break
        i += step
    return out

def _discover_docs(dirs: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """
    Returns [(path, chunk_text), ...] for files in RAG_DOCS_DIRS or explicit dirs.
    """
    dir_list = dirs if dirs is not None else [d.strip() for d in os.getenv(RAG_DIRS_ENV, "").split(",") if d.strip()]
    chunks: List[Tuple[str, str]] = []
    for d in dir_list:
        p = pathlib.Path(d)
        if not p.exists():
            continue
        for f in p.rglob("*"):
            txt = _read_text_fast(f)
            if not txt:
                continue
            for ch in _chunk_text(txt):
                chunks.append((str(f), ch))
    return chunks

def _retrieve_sources(reason: str, embedder: Any, doc_chunks: List[Tuple[str, str]], top_k: int = 2) -> List[Dict[str, str]]:
    """
    Return top_k relevant doc chunks [{path, preview}] for grounding.
    """
    if np is None or embedder is None or not doc_chunks or not reason:
        return []
    qv = embedder.encode([reason], convert_to_numpy=True, normalize_embeddings=True)[0]
    cvs = embedder.encode([c for _, c in doc_chunks], convert_to_numpy=True, normalize_embeddings=True)
    sims = (cvs @ qv)  # cosine since normalized
    order = np.argsort(-sims)[:top_k]
    out = []
    for idx in order:
        path, chunk = doc_chunks[int(idx)]
        preview = chunk[:280].strip()
        out.append({"path": path, "preview": preview})
    return out

def _is_expectation_gap(reason: str) -> bool:
    return bool(re.search(SUGG_CUES, reason or "", flags=re.I))

def _is_high_risk(reason: str, quotes: List[str]) -> bool:
    t = (reason or "") + " " + " ".join(quotes or [])[:1200]
    return bool(re.search(RISK_CUES, t, flags=re.I))

def _text_blob(*parts: str) -> str:
    return " ".join([p for p in parts if p]).lower()

# ---------------------------- Theme detection ----------------------------

def _detect_themes(reason: str, quotes: List[str]) -> List[str]:
    blob = _text_blob(reason, *quotes)
    themes: List[str] = []
    for theme, kws in THEME_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            themes.append(theme)
    # Light co-occurrence heuristics
    if "auth_override" in themes and "cold_weather" not in themes and any(k in blob for k in ["freeze", "frozen", "icy", "ice", "winter", "cold"]):
        themes.append("cold_weather")
    if "connectivity" in themes and "docs_comms" not in themes and "how" in blob:
        themes.append("docs_comms")
    # Deduplicate & keep stable order
    seen, out = set(), []
    for t in themes:
        if t not in seen:
            seen.add(t); out.append(t)
    return out[:4]  # cap to avoid dilution

# ---------------------------- Heuristic bullets ----------------------------

def _templates_for(theme: str, complaintish: bool, suggestionish: bool) -> List[str]:
    """
    Return a small set of templated, concrete, <=18-word bullets for a theme.
    """
    T = []

    if theme == "stability":
        T += [
            "Capture crash dumps + breadcrumbs; wire rollback; alert on spike above baseline.",
            "Add guardrails around failing paths; fail soft, preserve user data.",
        ]
    if theme == "performance":
        T += [
            "Profile hot paths; lazy-load heavy assets; add regression budget in CI.",
            "Ship micro-optimizations behind flag; measure P95 latency improvement.",
        ]
    if theme == "connectivity":
        T += [
            "Implement connectivity health checks + retries; offline queue with backoff.",
            "Show status banner with fallback; expose 'retry' CTA.",
        ]
    if theme == "auth_override":
        T += [
            "Provide manual override when device auth unavailable; document emergency sequence.",
            "Cache last good token; allow PIN fallback if phone/watch offline.",
        ]
    if theme == "cold_weather":
        T += [
            "Add cold-weather affordance: de-ice assist, pre-open nudge, material-safe lever.",
            "Detect sub-zero conditions; surface prep guidance before use.",
        ]
    if theme == "accessibility":
        T += [
            "Offer non-voice, single-hand alternatives; comply with WCAG/ADA basics.",
            "Provide large-target manual control; support offline accessibility path.",
        ]
    if theme == "docs_comms":
        T += [
            "Publish concise FAQ with workaround; link in-app near affected control.",
            "Add 'What to do when X fails' quick tip in onboarding.",
        ]

    # If only suggestions (not complaints), add experimentation template
    if suggestionish and not complaintish:
        T.append("Prototype minimal version; A/B via feature flag with target cohort.")

    # Always end with measurable acceptance criteria
    T.append("Define acceptance criteria; add canary metric to prove fix pre/post.")

    # Deduplicate while preserving order
    seen, out = set(), []
    for b in T:
        if b not in seen:
            seen.add(b); out.append(b)
    return out

def _reason_specific_tweaks(reason: str, bullets: List[str]) -> List[str]:
    r = (reason or "").lower()
    out = bullets[:]
    # Tiny tailoring, e.g., door/lock/handle nuance
    if any(k in r for k in ["door", "handle", "latch", "lock", "unlock"]):
        out.insert(0, "Ensure manual door/lock actuation works in cold and no-device scenarios.")
    if any(k in r for k in ["ice", "icy", "frozen", "winter", "cold"]):
        out.insert(0, "Add pre-emptive de-icing prompt when conditions suggest freezing risk.")
    # trim to 6; later we will cap to max_bullets anyway
    return out[:6]

def _compose_bullets(reason: str, intent_mix: Dict[str, int], quotes: List[str], max_bullets: int) -> Tuple[List[str], Optional[str]]:
    reason_l = (reason or "").lower().strip()
    total = sum(int(v) for v in intent_mix.values()) or 0
    complaintish = bool(re.search(NEG_CUES, reason_l, flags=re.I)) or (intent_mix.get("Complaint", 0) >= max(2, total // 2))
    suggestionish = _is_expectation_gap(reason) or (intent_mix.get("Suggestion", 0) >= max(1, total // 3))

    themes = _detect_themes(reason, quotes)
    bullets: List[str] = []
    for th in themes or ["stability"]:  # default to something pragmatic
        bullets.extend(_templates_for(th, complaintish, suggestionish))

    bullets = _reason_specific_tweaks(reason, bullets)

    # If nothing matched, provide safe generic actions
    if not bullets:
        bullets = [
            f"Instrument and reproduce '{reason}' with trace IDs; capture logs around failure.",
            "Publish short in-app note acknowledging issue and current workaround.",
            "Define acceptance criteria; add canary metric to prove fix pre/post trend.",
        ]

    # De-dupe, keep short, limit count
    seen, final_bullets = set(), []
    for b in bullets:
        b = b.strip()
        if not b or b in seen:
            continue
        # hard cap ~18-20 words
        if len(b.split()) > 20:
            b = " ".join(b.split()[:20]).rstrip(",.;:") + "…"
        seen.add(b); final_bullets.append(b)
        if len(final_bullets) >= max_bullets:
            break

    # Backlog note
    backlog = None
    if suggestionish and not complaintish:
        backlog = f"Roadmap: evaluate '{reason}' via impact/effort; size a v1 behind a flag."
    elif complaintish:
        backlog = "Backlog: assign DRI + timeline; keep workaround docs updated until green metrics."

    return final_bullets, backlog

# ---------------------------- Confidence scoring ----------------------------

def _confidence(complaints: int, total: int, support: float, has_sources: bool, *, size: Optional[int] = None) -> float:
    """
    Blend of complaint dominance, label support, grounding availability, and (optionally) cluster size.
    """
    base = 0.42
    if total > 0 and complaints / total >= 0.5:
        base += 0.2
    if support >= 0.55:
        base += 0.15
    if has_sources:
        base += 0.18
    if isinstance(size, int) and size >= 3:
        base += 0.1
    return round(min(0.95, base), 2)

# ---------------------------- LLM remediation (primary path) ----------------------------

def _coerce_str_list(obj: Any) -> Optional[List[str]]:
    """Extract a list of strings from an LLM JSON response.

    `chat_json` runs in JSON-object mode (OpenAI `response_format=json_object`,
    Ollama `format=json`), and BOTH forbid a top-level array — so a model asked
    for "a list" actually returns it WRAPPED in an object, e.g.
    {"suggestions": [...]}. The old code only accepted a bare list, so every
    response was rejected and the hardcoded templates leaked through for every
    product. We now accept the bare list AND the common wrapped shapes.

    Returns:
      - a list of strings on success (possibly empty → LLM judged it pure praise)
      - None when no list-shaped field is found (caller falls back to templates)
    """
    if obj is None:
        return None
    if isinstance(obj, list):
        return [str(x).strip() for x in obj if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(obj, dict):
        for k in ("suggestions", "improvements", "bullets", "actions",
                  "items", "recommendations", "fixes", "remediations"):
            v = obj.get(k)
            if isinstance(v, list):
                return [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
        # Fall back to the first list-of-strings value anywhere in the object
        for v in obj.values():
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                return [x.strip() for x in v if x.strip()]
    return None


def _llm_remediation_bullets(
    reason: str,
    quotes: List[str],
    query: Optional[str],
    product_context: Optional[str],
    max_bullets: int,
) -> Optional[List[str]]:
    """Generate product-specific remediation bullets via the unified LLM client.

    Uses the Product Intelligence context so suggestions are in-domain
    (headphones + "poor sound quality" → "Investigate driver tuning and EQ
    profiles"; "ears get too hot" → "Explore breathable ear-cushion materials
    and ventilation design") rather than the generic software templates.

    Returns None when no LLM is reachable or the response can't be parsed; an
    (possibly empty) list otherwise. An empty list means the model judged the
    feedback to be pure praise with nothing to fix — honored, not overwritten.
    """
    try:
        from backend.utils import llm as _llm
        if _llm.available_backend() == "none":
            return None
    except Exception:
        return None

    product_line = f'The product is: "{query}".\n' if query else ""
    ctx_block = ""
    if product_context:
        ctx_block = (
            "PRODUCT CONTEXT — what this product IS (use it to stay in-domain):\n"
            f"{product_context}\n\n"
        )
    prompt = (
        "You advise the MAKER of a product on how to improve it, based on a "
        "cluster of customer feedback.\n"
        f"{product_line}"
        f"{ctx_block}"
        "First infer the product's domain (e.g. packaged food, mobile app, car, "
        "headphones, appliance, cosmetic, game, service) — use the PRODUCT CONTEXT "
        "above when present. Then give 2-3 concrete, realistic improvements a maker "
        "in THAT domain could actually act on. Match the domain: for a food, think "
        "recipe/taste/texture/packaging/price/availability; for software, think "
        "features/bugs/UX; for hardware, think build/materials/comfort. Examples for "
        "headphones: 'poor sound quality' -> 'Investigate driver tuning and EQ "
        "profiles'; 'ears get too hot' -> 'Explore breathable ear-cushion materials "
        "and ventilation design'. NEVER use software jargon (latency, CI, hot paths, "
        "feature flags, crash dumps, cache tokens) unless the product is clearly "
        "software. Each bullet must be specific and <=18 words.\n"
        'Return ONLY a JSON object of the form {"suggestions": ["...", "..."]}. '
        "Use an empty list if the feedback is pure praise with nothing to fix.\n\n"
        f'Feedback theme: "{reason}"\n'
        f"Customer quotes (verbatim, may be code-mixed): {json.dumps((quotes or [])[:4])}\n"
    )
    try:
        raw = _llm.chat_json([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=400)
    except Exception:
        return None

    items = _coerce_str_list(raw)
    if items is None:
        return None

    clean: List[str] = []
    for x in items:
        s = (x or "").strip()
        if not s:
            continue
        if len(s.split()) > 20:
            s = " ".join(s.split()[:20]).rstrip(",.;:") + "…"
        clean.append(s)
        if len(clean) >= max_bullets:
            break
    return clean


# ---------------------------- Public API ----------------------------

@dataclass
class ClusterInput:
    cluster_id: int
    reason: str
    quotes: List[str]
    support: float
    centroid_sim_mean: float
    intent_counts: Dict[str, int]  # {"Complaint": n, "Suggestion": n, ...}
    # Optional to stay backward-compatible with callers that don't pass it
    size: Optional[int] = None

def generate_solutions(
    clusters: List[ClusterInput],
    *,
    query: Optional[str] = None,
    product_context: Optional[str] = None,
    embedder: Any = None,
    openai_client: Optional[OpenAIClient] = None,
    rag_docs_dirs: Optional[List[str]] = None,
    max_bullets: int = 3
) -> List[Dict[str, Any]]:
    """
    Returns an aligned list of solution dicts (same order as clusters):
      { "bullets": [...], "backlog": str|None, "source": [ {path, preview}, ... ] | [],
        "confidence": float, "expectation_gap": bool, "high_risk": bool }

    `product_context` (optional) is the compact Product-Intelligence string
    (category, segment, direct aspects, buyer expectations). When present it is
    injected into the LLM prompt so remediations are anchored to what the product
    actually IS — e.g. headphones whose "ears get hot" get "breathable ear-cushion
    materials", not the software-flavored "cache last good token".
    """
    # 1) Discover local knowledge once (optional)
    doc_chunks: List[Tuple[str, str]] = []
    try:
        doc_chunks = _discover_docs(rag_docs_dirs)
    except Exception:
        doc_chunks = []

    out: List[Dict[str, Any]] = []

    # 2) Generate per cluster
    for c in clusters:
        total = sum(c.intent_counts.values()) or 0
        complaints = c.intent_counts.get("Complaint", 0)

        # Gate: only generate if it's mainly complaint OR has meaningful suggestions
        expectation_gap = _is_expectation_gap(c.reason) or (c.intent_counts.get("Suggestion", 0) >= max(1, total // 3))

        if total == 0 or (complaints < max(1, total // 3) and not expectation_gap):
            out.append({
                "bullets": [],
                "backlog": None,
                "source": [],
                "confidence": 0.0,
                "expectation_gap": expectation_gap,
                "high_risk": _is_high_risk(c.reason, c.quotes or []),
            })
            continue

        # RAG: get 0–2 grounding snippets (optional)
        sources = _retrieve_sources(c.reason, embedder, doc_chunks, top_k=2) if doc_chunks else []

        # ---- Remediation bullets ----
        # The LLM is the PRIMARY generator: it reads the Product Intelligence
        # context and produces domain-appropriate fixes (headphones → "Investigate
        # driver tuning and EQ profiles" / "Explore breathable ear-cushion
        # materials", never the software-flavored "capture crash dumps" or "cache
        # last good token"). The hardcoded `_compose_bullets` templates are now a
        # LAST-RESORT fallback used ONLY when no LLM is reachable or it returns
        # nothing parseable.
        llm_bullets = _llm_remediation_bullets(
            c.reason, c.quotes or [], query, product_context, max_bullets
        )

        if llm_bullets is not None:
            # Trust the LLM, including an intentional empty list (pure praise).
            bullets = llm_bullets
            # Keep a sensible backlog note (cheap, domain-neutral text).
            _, backlog = _compose_bullets(c.reason, c.intent_counts, c.quotes or [], max_bullets)
        else:
            # No LLM available / unparseable → templated heuristic fallback.
            bullets, backlog = _compose_bullets(c.reason, c.intent_counts, c.quotes or [], max_bullets)

        conf = _confidence(complaints, total, c.support or 0.0, has_sources=bool(sources), size=getattr(c, "size", None))
        out.append({
            "bullets": bullets,
            "backlog": backlog,
            "source": sources,
            "confidence": conf,
            "expectation_gap": expectation_gap,
            "high_risk": _is_high_risk(c.reason, c.quotes or []),
        })

    return out
