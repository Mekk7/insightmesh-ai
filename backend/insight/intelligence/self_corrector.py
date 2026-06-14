"""
Multi-Pass Self-Correction — a single gpt-4o-mini QA pass over the FINISHED
analysis, run after every other layer and before the overview leaves the backend.

It catches cross-layer problems no individual layer sees: off-topic clusters/quotes
(memes, video reactions), executive-summary claims that don't match the data, cluster
labels that don't fit their quotes, and over-confidence on thin data. It then applies
safe, mechanical corrections and grades the analysis A–D.

Contract:
  • ONE LLM call (chat_json, gpt-4o-mini). ~2-3s.
  • Pure fail-open: any exception / missing LLM / invalid JSON → overview untouched.
  • Mutates `overview` in place; also returns it.
"""
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("insightmesh.self_corrector")

_MAX_QUOTE = 180          # chars per quote sent to the model
_MAX_CLUSTERS_IN_PROMPT = 12
_VALID_GRADES = {"A", "B", "C", "D"}


# ------------------------- context extraction -------------------------

def _cluster_count(c: Dict[str, Any], n_total: int) -> int:
    """Best-effort review count for a cluster (count/size, else derive from share_%)."""
    for k in ("count", "size", "n"):
        v = c.get(k)
        if isinstance(v, (int, float)) and v:
            return int(v)
    sp = c.get("share_%")
    if isinstance(sp, (int, float)) and n_total:
        return max(1, round(sp / 100.0 * n_total))
    return 1


def _narrative_text(v: Any) -> str:
    """A smart_summary narrative is either a plain string or a dict
    {headline, summary, key_takeaways, ...}. Reduce to readable text."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        parts = [v.get("headline"), v.get("summary")]
        kt = v.get("key_takeaways")
        if isinstance(kt, list):
            parts.append(" ".join(str(x) for x in kt if isinstance(x, (str, int, float))))
        return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip()).strip()
    return ""


def _summary_text(overview: Dict[str, Any]) -> str:
    s = overview.get("smart_summary")
    if isinstance(s, dict):
        for key in ("company", "consumer"):
            txt = _narrative_text(s.get(key))
            if txt:
                return txt
    elif isinstance(s, str) and s.strip():
        return s.strip()
    es = overview.get("executive_summary")
    return es.strip() if isinstance(es, str) else ""


def _top_voc_quotes(per_review: List[Dict[str, Any]], k: int = 3) -> List[str]:
    ranked = sorted(
        (r for r in (per_review or []) if isinstance(r, dict) and (r.get("original") or "").strip()),
        key=lambda r: (r.get("_intelligence") or {}).get("composite", 0),
        reverse=True,
    )
    out = []
    for r in ranked[:k]:
        out.append((r.get("original") or "").strip()[:_MAX_QUOTE])
    return out


def _build_prompt(overview: Dict[str, Any], per_review: List[Dict[str, Any]],
                  product_name: str, product_context: str) -> str:
    n_total = len(per_review or []) or overview.get("review_count") or 0
    clusters = overview.get("canonical_clusters") or []

    cl_lines = []
    for c in clusters[:_MAX_CLUSTERS_IN_PROMPT]:
        if not isinstance(c, dict):
            continue
        cid = c.get("cluster_id")
        reason = (c.get("reason") or c.get("canonical_reason") or "?")
        cnt = _cluster_count(c, n_total)
        share = c.get("share_%")
        q = ""
        quotes = c.get("quotes") or []
        if quotes:
            first = quotes[0]
            q = first if isinstance(first, str) else (first.get("quote") or "")
        cl_lines.append(
            f'  - cluster_id={cid} | "{reason}" | size={cnt}'
            + (f" ({share}%)" if share is not None else "")
            + (f' | quote: "{q[:_MAX_QUOTE]}"' if q else "")
        )

    aspects = (overview.get("aspect_sentiment") or {}).get("aspects") or []
    asp_lines = []
    for a in aspects[:12]:
        if not isinstance(a, dict):
            continue
        nm = a.get("aspect") or a.get("label") or a.get("name") or "?"
        rating = a.get("score")
        if rating is None:
            rating = a.get("sentiment")
        asp_lines.append(f"  - {nm}: {rating}")

    voc = _top_voc_quotes(per_review)
    voc_lines = [f'  - "{q}"' for q in voc]

    cross = overview.get("cross_insights") or []
    cross_lines = [f"  - {ci.get('type')}: {ci.get('description')}"
                   for ci in cross[:5] if isinstance(ci, dict)]

    conf = overview.get("_analysis_confidence") or {}
    conf_label = conf.get("label", "?")
    conf_overall = conf.get("overall", "?")

    summary = _summary_text(overview) or "(no summary)"

    return f"""You are a quality reviewer for a product review analysis system.
Review this analysis of "{product_name or 'the product'}" and identify any problems.

PRODUCT CONTEXT: {product_context or '(none)'}
DATA SIZE: {n_total} reviews/comments analyzed.
ANALYSIS CONFIDENCE (self-reported): {conf_label} ({conf_overall})

EXECUTIVE SUMMARY:
{summary}

CLUSTERS (id | label | size | sample quote):
{chr(10).join(cl_lines) or '  (none)'}

ASPECTS (name: rating):
{chr(10).join(asp_lines) or '  (none)'}

TOP VOICE-OF-CUSTOMER QUOTES:
{chr(10).join(voc_lines) or '  (none)'}

CROSS-INSIGHTS:
{chr(10).join(cross_lines) or '  (none)'}

Check for these problems:
1. RELEVANCE: Are any clusters or quotes clearly NOT about the product? (gaming memes
   on a hardware product, video reactions, off-topic banter.) List which to remove/relabel.
2. CONSISTENCY: Does the executive summary match the data? (e.g. summary says "price is
   the top concern" but the price cluster is only 10%.)
3. LABEL: Are cluster names accurate for their representative quotes? (e.g. a cluster
   called "Performance issues" whose quotes are all about docking.)
4. CONFIDENCE: Is the system too confident given data quality? (15 YouTube comments
   should not produce HIGH-confidence assertions.)

Return ONLY JSON:
{{
  "corrections": [
    {{"type": "remove_cluster", "cluster_id": N, "reason": "..."}},
    {{"type": "relabel_cluster", "cluster_id": N, "new_label": "...", "reason": "..."}},
    {{"type": "adjust_summary", "issue": "...", "suggestion": "..."}},
    {{"type": "flag_low_quality", "message": "..."}}
  ],
  "quality_grade": "A" | "B" | "C" | "D",
  "quality_note": "one short sentence"
}}
Only include corrections you are confident about. An empty corrections list is fine."""


# ------------------------- correction application -------------------------

def _recalc_shares(clusters: List[Dict[str, Any]], n_total: int) -> None:
    """Renormalize share_% over the remaining clusters' counts (sum ~= 100)."""
    total = sum(_cluster_count(c, n_total) for c in clusters) or 1
    for c in clusters:
        c["share_%"] = round(_cluster_count(c, n_total) / total * 100, 1)


def _apply(overview: Dict[str, Any], per_review: List[Dict[str, Any]],
           corrections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply corrections in place. Returns the list of corrections actually applied."""
    n_total = len(per_review or []) or overview.get("review_count") or 0
    clusters = overview.get("canonical_clusters")
    clusters = clusters if isinstance(clusters, list) else []
    by_id = {}
    for c in clusters:
        if isinstance(c, dict) and c.get("cluster_id") is not None:
            try:
                by_id[int(c["cluster_id"])] = c
            except (TypeError, ValueError):
                pass

    applied: List[Dict[str, Any]] = []
    flags: List[str] = list(overview.get("_quality_flags") or [])
    remove_ids: set = set()

    for corr in corrections:
        if not isinstance(corr, dict):
            continue
        ctype = corr.get("type")

        if ctype == "remove_cluster":
            try:
                cid = int(corr.get("cluster_id"))
            except (TypeError, ValueError):
                continue
            if cid in by_id:
                remove_ids.add(cid)
                applied.append(corr)

        elif ctype == "relabel_cluster":
            try:
                cid = int(corr.get("cluster_id"))
            except (TypeError, ValueError):
                continue
            new_label = (corr.get("new_label") or "").strip()
            c = by_id.get(cid)
            if c is not None and new_label:
                c["reason"] = new_label
                if "canonical_reason" in c:
                    c["canonical_reason"] = new_label
                for r in (per_review or []):
                    try:
                        if isinstance(r, dict) and int(r.get("cluster_id", -999)) == cid:
                            r["canonical_reason"] = new_label
                            r["cluster_topic"] = [new_label]
                    except (TypeError, ValueError):
                        pass
                applied.append(corr)

        elif ctype == "adjust_summary":
            issue = (corr.get("issue") or "").strip()
            sugg = (corr.get("suggestion") or "").strip()
            if issue or sugg:
                # One-call budget: don't re-call the LLM. Record the caveat and append a
                # short note to the summary narratives so the dashboard reflects it.
                note = f"Quality note: {sugg or issue}"
                flags.append(note)
                # Append the caveat into the narrative without a second LLM call. The
                # narratives are dicts {summary, ...} or plain strings — handle both.
                s = overview.get("smart_summary")
                if isinstance(s, dict):
                    for key in ("company", "consumer"):
                        v = s.get(key)
                        if isinstance(v, dict) and isinstance(v.get("summary"), str):
                            v["summary"] = v["summary"].rstrip() + f"\n\n⚠ {note}"
                        elif isinstance(v, str) and v.strip():
                            s[key] = v.rstrip() + f"\n\n⚠ {note}"
                applied.append(corr)

        elif ctype == "flag_low_quality":
            msg = (corr.get("message") or "").strip()
            if msg:
                flags.append(msg)
                applied.append(corr)

    if remove_ids:
        kept = [c for c in clusters if not (isinstance(c, dict)
                and c.get("cluster_id") is not None
                and _safe_int(c.get("cluster_id")) in remove_ids)]
        overview["canonical_clusters"] = kept
        _recalc_shares(kept, n_total)

    if flags:
        # de-dup, preserve order
        seen = set()
        overview["_quality_flags"] = [f for f in flags if not (f in seen or seen.add(f))]

    return applied


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return -999999


# ------------------------- entry point -------------------------

def self_correct(overview: Dict[str, Any],
                 per_review: Optional[List[Dict[str, Any]]] = None,
                 product_context: str = "",
                 product_name: str = "") -> Dict[str, Any]:
    """Run the QA pass and apply corrections in place. Fail-open."""
    if not isinstance(overview, dict):
        return overview
    try:
        from backend.utils.llm import chat_json, available_backend
        if not available_backend():
            return overview
        clusters = overview.get("canonical_clusters") or []
        if not clusters and not _summary_text(overview):
            return overview  # nothing to review

        prompt = _build_prompt(overview, per_review or [], product_name, product_context)
        result = chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=700,
        )
        if not isinstance(result, dict):
            log.info("[self_corrector] no usable result; skipping")
            return overview

        corrections = result.get("corrections")
        corrections = corrections if isinstance(corrections, list) else []
        applied = _apply(overview, per_review or [], corrections)

        grade = result.get("quality_grade")
        grade = grade if grade in _VALID_GRADES else None
        note = (result.get("quality_note") or "").strip()

        overview["_self_correction"] = {
            "quality_grade": grade,
            "quality_note": note,
            "corrections_suggested": len(corrections),
            "corrections_applied": len(applied),
            "types_applied": sorted({c.get("type") for c in applied if isinstance(c, dict)}),
        }
        log.info("[self_corrector] grade=%s applied=%d/%d types=%s",
                 grade, len(applied), len(corrections),
                 overview["_self_correction"]["types_applied"])
    except Exception as e:
        log.warning("[self_corrector] skipped (%s)", e)
    return overview
