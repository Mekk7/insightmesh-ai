# backend/insight/why_layer/synthesizer.py
"""
Cross-Signal Narrative Synthesizer ("why" layer).

The hierarchy module gives us WHAT is wrong. This module answers WHY it happens
and WHO it affects most — by cross-referencing sub-issues with personas,
geography, time-of-review, and severity.

Example output for one sub-issue:
  Input:  "Cold-weather range drop" — 28 mentions, 1.9 stars, HIGH severity
  Output:
    "Almost all of these reports come from long-term owners writing about
     winter driving experiences. Tech enthusiasts mention specific kWh loss
     numbers; mainstream owners describe it as range anxiety. The pattern is
     consistent with battery chemistry behavior in cold conditions."

For EACH sub-issue, we extract:
  - dominant_personas: which reviewer types mention this most
  - geographic_hints: regions mentioned in the quotes
  - temporal_hint: do the mentions cluster in a season / month / version?
  - language_hint: which languages this complaint appears in
  - rephrased_takeaway: LLM-written one-line summary

This is what makes the dashboard *answer questions* instead of just showing data.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

try:
    from backend.utils import llm as llm_client
except Exception:
    llm_client = None

log = logging.getLogger("insightmesh.why_layer")


# --- Geography extraction (regex-based, no external dep) ---
_GEO_PATTERNS = {
    "North America": re.compile(r"\b(?:US|USA|United States|Canada|California|Texas|New York|NYC|Boston|Toronto|Quebec|Vancouver|Florida|Chicago|Seattle|Minnesota|Northeast|Midwest)\b", re.I),
    "Europe":        re.compile(r"\b(?:UK|England|London|Germany|France|Paris|Italy|Spain|Netherlands|Norway|Sweden|Finland|Denmark|Scandinavia|EU|Europe)\b", re.I),
    "Asia":          re.compile(r"\b(?:Japan|Tokyo|China|Beijing|Shanghai|Korea|Seoul|India|Mumbai|Delhi|Singapore|Thailand|Vietnam|Hong Kong)\b", re.I),
    "Oceania":       re.compile(r"\b(?:Australia|Sydney|Melbourne|New Zealand|Auckland)\b", re.I),
    "South America": re.compile(r"\b(?:Brazil|Sao Paulo|Argentina|Buenos Aires|Mexico|Chile|Colombia)\b", re.I),
    "Middle East":   re.compile(r"\b(?:UAE|Dubai|Israel|Saudi|Turkey|Istanbul)\b", re.I),
    "Africa":        re.compile(r"\b(?:South Africa|Cape Town|Nigeria|Lagos|Kenya|Egypt)\b", re.I),
}

# --- Temporal hints ---
_SEASON_PATTERNS = {
    "winter":  re.compile(r"\b(?:winter|cold weather|snow|freezing|sub.?zero|december|january|february)\b", re.I),
    "summer":  re.compile(r"\b(?:summer|hot weather|heat wave|june|july|august|sweltering)\b", re.I),
    "spring":  re.compile(r"\b(?:spring|march|april|may)\b", re.I),
    "fall":    re.compile(r"\b(?:fall|autumn|september|october|november)\b", re.I),
}

_VERSION_PATTERN = re.compile(r"\b(?:v\d+\.\d+|version \d+|gen \d+|generation \d+|firmware \d+|update \d+|\d{4} model)\b", re.I)


def _extract_geo(quotes: List[str]) -> List[str]:
    """Return the top 1-3 regions mentioned across all quotes."""
    blob = " ".join(quotes).lower()
    hits = []
    for region, pat in _GEO_PATTERNS.items():
        if pat.search(blob):
            hits.append(region)
    return hits[:3]


def _extract_season(quotes: List[str]) -> Optional[str]:
    blob = " ".join(quotes)
    counts: Counter = Counter()
    for season, pat in _SEASON_PATTERNS.items():
        if pat.search(blob):
            counts[season] += 1
    return counts.most_common(1)[0][0] if counts else None


def _extract_version_hints(quotes: List[str]) -> List[str]:
    blob = " ".join(quotes)
    found = list({m.group(0) for m in _VERSION_PATTERN.finditer(blob)})
    return found[:3]


def _persona_dominance(sub_issue: Dict[str, Any], all_personas: List[Dict[str, Any]]) -> Optional[str]:
    """Build a sentence about which personas are most affected."""
    persona_keys = sub_issue.get("personas_most_affected") or []
    if not persona_keys:
        return None
    name_by_key = {p.get("key"): p.get("label") for p in (all_personas or []) if p.get("key")}
    named = [name_by_key.get(k, k.replace("_", " ").title()) for k in persona_keys[:2]]
    if len(named) == 1:
        return f"Mostly affects {named[0].lower()}s."
    return f"Affects {named[0].lower()}s and {named[1].lower()}s most."


def _llm_synthesize(
    product: str,
    aspect_label: str,
    sub_issue: Dict[str, Any],
    extras: Dict[str, Any],
) -> Optional[str]:
    """Ask LLM to write a 1-2 sentence 'why' narrative for this sub-issue."""
    if llm_client is None or llm_client.available_backend() == "none":
        return None

    quotes = sub_issue.get("sample_quotes", [])[:3]
    quote_blob = "\n".join(f"- {q[:200]}" for q in quotes)

    extras_blob = []
    if extras.get("personas_sentence"):
        extras_blob.append(f"Persona pattern: {extras['personas_sentence']}")
    if extras.get("geographic_hints"):
        extras_blob.append(f"Geographic mentions: {', '.join(extras['geographic_hints'])}")
    if extras.get("temporal_hint"):
        extras_blob.append(f"Season hint: {extras['temporal_hint']}")
    if extras.get("version_hints"):
        extras_blob.append(f"Version references: {', '.join(extras['version_hints'])}")

    prompt = f"""You analyze product review patterns. The product is "{product}". The aspect is "{aspect_label}". The sub-issue is "{sub_issue.get('name')}" with {sub_issue.get('mentions')} mentions, average sentiment {sub_issue.get('avg_sentiment_stars')}, severity {sub_issue.get('severity')}.

Sample quotes:
{quote_blob}

Extra signals detected:
{chr(10).join(extras_blob) if extras_blob else "(no extra patterns)"}

Write 1-2 sentences explaining WHY this is happening and WHO it most affects. Use the extra signals when they're informative. Be factual, don't invent data, and don't start with "Reviewers...". Plain language, no marketing speak.

Return ONLY the narrative text — no JSON, no preamble, no quotes around the answer.
"""

    try:
        text = llm_client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=180,
        )
        if not text:
            return None
        # Strip any accidental quote wrapping
        out = text.strip().strip('"').strip("'").strip()
        # Limit length defensively
        return out[:600] if out else None
    except Exception as e:
        log.debug("[why_layer] LLM call failed: %s", e)
        return None


def _heuristic_narrative(
    sub_issue: Dict[str, Any],
    extras: Dict[str, Any],
) -> str:
    """Stitch a narrative from extracted signals when LLM unavailable."""
    parts = []
    if extras.get("personas_sentence"):
        parts.append(extras["personas_sentence"])
    if extras.get("geographic_hints"):
        parts.append(f"Reports concentrated in {', '.join(extras['geographic_hints'][:2])}.")
    if extras.get("temporal_hint"):
        parts.append(f"Mentions cluster around {extras['temporal_hint']} conditions.")
    if extras.get("version_hints"):
        parts.append(f"Multiple reviewers reference {extras['version_hints'][0]}.")
    sev = sub_issue.get("severity")
    if sev == "CRITICAL":
        parts.append("This carries critical-severity language and should not wait for the next version.")
    elif sev == "HIGH" and not parts:
        parts.append("High-severity language is consistent across mentions.")
    if not parts:
        # Last resort — describe sub-issue stats
        parts.append(f"{sub_issue.get('mentions', 0)} reviewers mention this with average sentiment {sub_issue.get('avg_sentiment_stars', '?')}\u2605.")
    return " ".join(parts)


def _llm_synthesize_aspect(
    product: str,
    aspect_label: str,
    subs_with_extras: List[tuple],
) -> Dict[str, Any]:
    """Write the 'why' narrative for ALL sub-issues of one aspect in a SINGLE LLM call.

    This replaces the previous one-call-per-sub-issue loop, which on a CPU-bound
    local model (qwen2.5:7b at ~24s/call) turned ~25 sub-issues into ~10 minutes of
    serial inference. Batching collapses that to one call per aspect (~5 total),
    with identical output and no behavioural change across depth modes.

    `subs_with_extras`: list of (sub_issue_dict, extras_dict).
    Returns {sub_issue_name: narrative}. Empty dict when no LLM / on failure ->
    callers fall back to `_heuristic_narrative` per sub-issue.
    """
    if llm_client is None or llm_client.available_backend() == "none":
        return {}
    if not subs_with_extras:
        return {}

    blocks: List[str] = []
    for n, (sub, extras) in enumerate(subs_with_extras, 1):
        quotes = (sub.get("sample_quotes") or [])[:3]
        quote_blob = "\n".join(f"    - {str(q)[:200]}" for q in quotes) or "    (no quotes)"
        sig = []
        if extras.get("personas_sentence"):
            sig.append(f"persona: {extras['personas_sentence']}")
        if extras.get("geographic_hints"):
            sig.append(f"geo: {', '.join(extras['geographic_hints'])}")
        if extras.get("temporal_hint"):
            sig.append(f"season: {extras['temporal_hint']}")
        if extras.get("version_hints"):
            sig.append(f"versions: {', '.join(extras['version_hints'])}")
        blocks.append(
            f'{n}. "{sub.get("name")}" | {sub.get("mentions")} mentions | '
            f'avg {sub.get("avg_sentiment_stars")} stars | severity {sub.get("severity")}\n'
            f'   signals: {"; ".join(sig) if sig else "(none)"}\n'
            f'   quotes:\n{quote_blob}'
        )

    n_subs = len(subs_with_extras)
    prompt = f"""You analyze product review patterns. The product is "{product}". The aspect is "{aspect_label}".

For EACH numbered sub-issue below, write 1-2 sentences explaining WHY it is happening and WHO it most affects. Use the extra signals when they're informative. Be factual, don't invent data, don't start with "Reviewers...". Plain language, no marketing speak.

Sub-issues:
{chr(10).join(blocks)}

Return ONLY a JSON object whose keys are the sub-issue NUMBERS (as strings "1".."{n_subs}") and whose values are the narrative strings. Include every number. No preamble, no extra keys.
Example: {{"1": "Almost all of these reports come from winter drivers...", "2": "..."}}"""

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=min(1400, 160 * n_subs + 200),
        )
    except Exception as e:
        log.debug("[why_layer] batched LLM call failed: %s", e)
        return {}

    if not isinstance(parsed, dict):
        return {}

    # Map the model's keys back to sub-issue NAMES. Primary path: integer index
    # keys ("1".."N") -> position. Fallback: a key that (loosely) matches a name.
    out: Dict[str, Any] = {}
    names = [sub.get("name") for sub, _ in subs_with_extras]
    norm_names = {re.sub(r"\s+", " ", (nm or "").strip().lower()): nm for nm in names}
    for k, v in parsed.items():
        if not (isinstance(v, str) and v.strip()):
            continue
        narrative = v.strip().strip('"').strip("'").strip()[:600]
        key = str(k).strip()
        m = re.match(r"^\D*(\d+)\D*$", key)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(names) and names[idx]:
                out[names[idx]] = narrative
                continue
        # name-ish key: exact-normalized match, else substring
        nk = re.sub(r"\s+", " ", key.lower())
        if nk in norm_names:
            out[norm_names[nk]] = narrative
        else:
            for cand_norm, cand_name in norm_names.items():
                if cand_norm and (cand_norm in nk or nk in cand_norm):
                    out[cand_name] = narrative
                    break
    return out


def enrich_hierarchy_with_why(
    hierarchy: List[Dict[str, Any]],
    *,
    product: str,
    personas: Optional[List[Dict[str, Any]]] = None,
    max_sub_issues_per_aspect: int = 5,
) -> List[Dict[str, Any]]:
    """
    Walks the hierarchy and enriches each sub-issue with a `narrative` field
    plus extracted signal hints. Never raises; missing data just skips.

    The 'why' narratives are produced ONE LLM CALL PER ASPECT (batched across that
    aspect's sub-issues) rather than one call per sub-issue — see
    `_llm_synthesize_aspect`. Signal extraction stays per-sub-issue (pure regex).
    """
    if not hierarchy:
        return hierarchy

    for aspect in hierarchy:
        sub_issues = (aspect.get("sub_issues") or [])[:max_sub_issues_per_aspect]
        if not sub_issues:
            continue

        # 1) Per-sub-issue signal extraction (pure regex, no LLM)
        subs_with_extras: List[tuple] = []
        for sub in sub_issues:
            quotes = sub.get("sample_quotes") or []
            geo = _extract_geo(quotes)
            season = _extract_season(quotes)
            versions = _extract_version_hints(quotes)
            extras = {
                "geographic_hints": geo,
                "temporal_hint": season,
                "version_hints": versions,
                "personas_sentence": _persona_dominance(sub, personas or []),
            }
            subs_with_extras.append((sub, extras))

        # 2) One batched LLM call for all of this aspect's narratives
        batched = _llm_synthesize_aspect(product, aspect.get("label", ""), subs_with_extras)

        # 3) Assign narratives; fall back to heuristic per sub-issue when the
        #    batched result is missing/unusable for a given sub-issue.
        for sub, extras in subs_with_extras:
            narrative = batched.get(sub.get("name"))
            if not narrative:
                narrative = _heuristic_narrative(sub, extras)
            sub["narrative"] = narrative
            sub["signals"] = {
                "geographic_hints": extras["geographic_hints"],
                "temporal_hint": extras["temporal_hint"],
                "version_hints": extras["version_hints"],
            }

    return hierarchy
