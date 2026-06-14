# backend/insight/actions/suggester.py
# Turn canonical clusters into concrete suggestions.
# - Universal keyword taxonomy (no product hard-coding)
# - Priority from size/share + severity cues + cohesion
# - Optional LLM refinement (uses OPENAI_API_KEY if set), safe heuristics otherwise

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Optional OpenAI (graceful fallback if missing)
try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

# ----------------------------- Config ---------------------------------------

@dataclass
class SuggestionConfig:
    max_items_per_cluster: int = 3
    high_share_threshold: float = 20.0  # %
    med_share_threshold: float  = 8.0   # %
    high_cohesion_threshold: float = 0.70  # centroid_sim_mean
    med_cohesion_threshold: float  = 0.55
    # Severity boosts when these cues appear in the reason
    severe_cues: Tuple[str, ...] = (
        "not working", "doesn't", "does not", "cannot", "can't", "won't",
        "crash", "crashes", "error", "broken", "fails", "failure", "bug",
        "unsafe", "security", "privacy leak", "overheat", "drain", "drains",
        "disconnect", "offline", "data loss", "blocking"
    )
    # LLM usage
    use_llm: bool = True
    llm_model: str = os.getenv("SUGGESTIONS_MODEL", "gpt-4o-mini")
    llm_temperature: float = 0.2

# ----------------------------- Taxonomy -------------------------------------

# Lightweight, universal mapping from keywords -> aspect bucket
_ASPECT_MAP: List[Tuple[str, str]] = [
    ("battery", "Battery"),
    ("charge", "Battery"),
    ("camera", "Camera"),
    ("photo", "Camera"),
    ("display", "Display"),
    ("screen", "Display"),
    ("lag", "Performance"),
    ("slow", "Performance"),
    ("fps", "Performance"),
    ("connect", "Connectivity"),
    ("bluetooth", "Connectivity"),
    ("wifi", "Connectivity"),
    ("network", "Connectivity"),
    ("lte", "Connectivity"),
    ("subscription", "Monetization"),
    ("billing", "Monetization"),
    ("price", "Monetization"),
    ("paywall", "Monetization"),
    ("login", "Account"),
    ("signup", "Account"),
    ("auth", "Account"),
    ("account", "Account"),
    ("update", "Software"),
    ("bug", "Software"),
    ("crash", "Software"),
    ("install", "Onboarding"),
    ("setup", "Onboarding"),
    ("ux", "Ergonomics"),
    ("ui", "Ergonomics"),
    ("button", "Ergonomics"),
    ("privacy", "Privacy/Security"),
    ("security", "Privacy/Security"),
    ("docs", "Documentation"),
    ("manual", "Documentation"),
    ("support", "Support"),
]

def _bucket_aspect(reason: str) -> str:
    r = (reason or "").lower()
    for kw, asp in _ASPECT_MAP:
        if kw in r:
            return asp
    return "Other"

# ----------------------------- Heuristics -----------------------------------

_SEVERITY_MULT = {
    "low": 1,
    "med": 2,
    "high": 3,
}

def _severity(reason: str, severe_cues: Tuple[str, ...]) -> str:
    r = (reason or "").lower()
    if any(cue in r for cue in severe_cues):
        return "high"
    # mild negative words
    if any(x in r for x in ("inaccurate", "missing", "too slow", "confusing", "hard to", "latency", "delay")):
        return "med"
    return "low"

def _priority_score(share_pct: float, cohesion: float, sev: str, cfg: SuggestionConfig) -> float:
    share_band = 2 if share_pct >= cfg.high_share_threshold else (1 if share_pct >= cfg.med_share_threshold else 0)
    coh_band = 2 if cohesion >= cfg.high_cohesion_threshold else (1 if cohesion >= cfg.med_cohesion_threshold else 0)
    sev_band = _SEVERITY_MULT.get(sev, 1)
    # Weighted sum (bounded ~ 0..10)
    return round( (2.5 * share_band) + (2.0 * coh_band) + (2.0 * sev_band), 2 )

# ----------------------------- Templates ------------------------------------

def _templated_suggestions(aspect: str, reason: str) -> List[str]:
    a = aspect.lower()
    r = reason.strip()
    out: List[str] = []

    # Generic “4T” pattern: Triage, Temporary workaround, Targeted fix, Telemetry
    out.append(f"Triage: reproduce '{r}' across latest versions; define acceptance test to fail until fixed.")
    out.append(f"Temporary workaround: document a short-term mitigation for '{r}' in support/FAQ and inside the UI where relevant.")
    out.append(f"Targeted fix: add a dedicated task to address '{r}' in the {aspect} area, with an A/B or canary rollout.")
    out.append(f"Telemetry: add instrumentation to quantify '{r}' frequency and impact; alert when regression > baseline.")

    # Aspect-specific spice
    if a in ("connectivity",):
        out.append("Connectivity: add retry/backoff and offline queue; surface clear state (connected/limited/offline).")
        out.append("Add network health diagnostics (DNS, latency, captive portal, proxy) and a one-click 'fix network'.")
    elif a in ("battery",):
        out.append("Battery: profile background tasks; schedule heavy work on charge/Wi-Fi; add low-power mode toggle.")
    elif a in ("performance",):
        out.append("Performance: capture flamegraphs for slow paths; set performance SLO (p95) and regressions gates in CI.")
    elif a in ("software",):
        out.append("Software: implement crash shields and safe-guarded rollbacks; add e2e tests for the failing scenario.")
    elif a in ("ergonomics", "display", "camera", "account", "onboarding"):
        out.append(f"{aspect}: run 5 quick usability tests focused on '{r}' and ship a low-effort UI affordance.")
    elif a in ("privacy/security",):
        out.append("Privacy/Security: add permission prompts with clear rationale; conduct a quick threat model & patch risky defaults.")
    elif a in ("documentation", "support"):
        out.append("Docs/Support: add a concise how-to addressing the issue; ensure searchable anchors and in-product links.")
    elif a in ("monetization",):
        out.append("Monetization: clarify feature gating; offer a basic tier alternative or time-limited trial for blocked workflows.")

    # Deduplicate & cap
    seen = set()
    uniq = []
    for s in out:
        k = s.strip().lower()
        if k not in seen:
            uniq.append(s)
            seen.add(k)
    return uniq[:5]

# ----------------------------- LLM (optional) --------------------------------

def _llm_client() -> Optional[Any]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or OpenAI is None:
        return None
    try:
        return OpenAI(api_key=key)
    except Exception:
        return None

def _llm_refine(reason: str, aspect: str, base: List[str], cfg: SuggestionConfig) -> List[str]:
    client = _llm_client()
    if client is None or not cfg.use_llm:
        return base[: cfg.max_items_per_cluster]
    prompt = (
        "You are a senior product engineer. Improve these action items to be specific, measurable, "
        "and implementable within 2 sprints. Keep them short (≤120 chars), remove duplicates, and "
        "ensure they address the user's reason directly.\n\n"
        f"Aspect: {aspect}\n"
        f"Reason: {reason}\n"
        f"Draft items:\n- " + "\n- ".join(base[:6])
    )
    try:
        resp = client.chat.completions.create(
            model=cfg.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=cfg.llm_temperature,
        )
        text = (resp.choices[0].message.content or "").strip()
        # Parse bullets (very permissive)
        items = [re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip() for ln in text.splitlines() if ln.strip()]
        items = [i for i in items if len(i) >= 6]
        # Dedup and cap
        seen = set(); out = []
        for i in items:
            k = i.lower()
            if k not in seen:
                out.append(i); seen.add(k)
        return out[: cfg.max_items_per_cluster] or base[: cfg.max_items_per_cluster]
    except Exception:
        return base[: cfg.max_items_per_cluster]

# ----------------------------- Public API ------------------------------------

def suggestions_for_clusters(
    canonical_clusters: List[Dict[str, Any]],
    *,
    cfg: Optional[SuggestionConfig] = None
) -> List[Dict[str, Any]]:
    """
    Input: list of cluster dicts as produced by analyzer canonical clustering:
      { 'cluster_id', 'reason', 'count', 'share_%', 'centroid_sim_mean', 'support', 'quotes': [...] }
    Output: list of suggestion blocks:
      {
        "cluster_id": int,
        "reason": str,
        "aspect": str,
        "priority": int,  # 1..5
        "score": float,   # internal score
        "suggestions": [str, ...]
      }
    """
    if not canonical_clusters:
        return []

    cfg = cfg or SuggestionConfig()
    blocks: List[Dict[str, Any]] = []

    for c in canonical_clusters:
        reason = c.get("reason") or "General issue / suggestion"
        share = float(c.get("share_%", 0.0))
        coh   = float(c.get("centroid_sim_mean", 0.0))
        aspect = _bucket_aspect(reason)
        sev = _severity(reason, cfg.severe_cues)
        score = _priority_score(share, coh, sev, cfg)
        priority = int(min(5, max(1, round(score / 2))))  # map ~0..10 → 1..5

        base = _templated_suggestions(aspect, reason)
        refined = _llm_refine(reason, aspect, base, cfg)

        blocks.append({
            "cluster_id": int(c.get("cluster_id", 0)),
            "reason": reason,
            "aspect": aspect,
            "priority": priority,
            "score": score,
            "suggestions": refined
        })

    # Order: higher priority first, then bigger share, then cohesion
    blocks.sort(key=lambda b: (-b["priority"], -float(next((x.get("share_%", 0.0) for x in canonical_clusters if int(x.get("cluster_id", -1)) == b["cluster_id"]), 0.0)), -b["score"]))
    return blocks
