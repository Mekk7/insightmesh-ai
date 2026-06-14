# backend/api/insightmesh/export.py
"""
Export endpoints for pipeline reports.

Two formats, both fully self-contained:
  - Markdown : a polished, structured `.md` document for sharing/archiving.
  - HTML     : a styled standalone page (open in browser, hit Cmd/Ctrl-P → "Save as PDF").

Sources:
  - GET /export/run/{run_id}.md    -> Markdown (pulled from history DB)
  - GET /export/run/{run_id}.html  -> HTML (pulled from history DB)
  - POST /export/report.md         -> Markdown from a final_report payload
  - POST /export/report.html       -> HTML from a final_report payload

Why both pull (by id) and push (by body)?
  - "Pull" is convenient for sharing a stored report.
  - "Push" lets the dashboard export the result it currently has in memory
    (no DB roundtrip).
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from backend.utils.db import get_run

router = APIRouter()


# -------------------- Pydantic --------------------

class ReportPayload(BaseModel):
    final_report: Dict[str, Any]


# -------------------- Helpers --------------------

def _safe(s: Any, default: str = "—") -> str:
    if s is None or s == "":
        return default
    return str(s)


def _fmt_pct(n: Any) -> str:
    try:
        return f"{round(float(n))}%"
    except Exception:
        return "—"


def _fmt_num(n: Any, ndigits: int = 2) -> str:
    try:
        return str(round(float(n), ndigits))
    except Exception:
        return "—"


def _bullet_list(items: Optional[List[Any]], default: str = "_(none)_") -> str:
    if not items:
        return default
    return "\n".join(f"- {item}" for item in items)


# -------------------- Markdown rendering --------------------

def render_markdown(final_report: Dict[str, Any]) -> str:
    if not isinstance(final_report, dict):
        raise ValueError("final_report must be a dict")

    meta = final_report.get("meta") or {}
    analysis = final_report.get("analysis") or {}
    overview = analysis.get("overview") or {}
    exec_summary = analysis.get("executive_summary") or {}
    exec_brief = analysis.get("executive_brief") or {}
    canonical = overview.get("canonical_clusters") or []
    suggestions = overview.get("cluster_suggestions") or []
    contributions = final_report.get("contributions") or {}
    per_review = analysis.get("per_review") or []
    action_items = analysis.get("action_items") or []

    # Merge cluster suggestions by id
    sug_by_id: Dict[int, List[str]] = {}
    for s in suggestions:
        try:
            sug_by_id[int(s.get("cluster_id"))] = list(s.get("suggestions") or [])
        except Exception:
            pass

    lines: List[str] = []

    # ---- Header ----
    title_query = _safe(meta.get("query_used"), "Untitled run")
    lines.append(f"# InsightMesh AI — Report")
    lines.append("")
    lines.append(f"**Query / dataset:** {title_query}")
    lines.append(f"**Mode:** {_safe(meta.get('user_mode'))}  ·  **Strictness:** {_safe(meta.get('strictness'))}")
    if meta.get("time_from") or meta.get("time_to"):
        lines.append(f"**Time window:** {_safe(meta.get('time_from'))} → {_safe(meta.get('time_to'))}")
    lines.append(f"**Elapsed:** {_safe(meta.get('elapsed_ms'))} ms  ·  **Generated:** {datetime.utcnow().isoformat()}Z")
    if meta.get("from_cache"):
        lines.append(f"**Cache hit:** yes")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- Executive Brief ----
    lines.append("## Executive Brief")
    lines.append("")
    if exec_brief:
        pain = exec_brief.get("biggest_pain") or {}
        qw = exec_brief.get("quick_win") or {}
        risk = exec_brief.get("risk_or_opportunity") or {}
        if pain:
            share = pain.get("share_pct")
            share_txt = f" ({share}% of reviews)" if isinstance(share, (int, float)) else ""
            lines.append(f"**Biggest pain:** {_safe(pain.get('theme'))}{share_txt}")
            if pain.get("evidence_quote"):
                lines.append(f"> _\"{pain.get('evidence_quote')}\"_")
        if qw:
            actions = " · ".join(qw.get("actions") or []) or "—"
            impact = qw.get("expected_impact") or ""
            tail = f" — _{impact}_" if impact else ""
            lines.append(f"**Quick win:** {actions}{tail}")
        if risk:
            lines.append(f"**{_safe(risk.get('label'), 'Risk/Opportunity')}:** {_safe(risk.get('note'))}")
    else:
        lines.append("_No executive brief returned._")
    lines.append("")

    # ---- Key Metrics ----
    lines.append("## Key Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Mood index (−1 … +1) | {_fmt_num(overview.get('mood_index'), 3)} |")
    lines.append(f"| Average sentiment (1 … 5★) | {_fmt_num(overview.get('average_sentiment'), 2)} |")
    lines.append(f"| Total reviews analyzed | {len(per_review)} |")
    lines.append(f"| Strictness | {_safe(meta.get('strictness'))} |")
    lines.append("")

    # ---- Star distribution ----
    stars = overview.get("stars") or {}
    if stars:
        lines.append("### Star distribution")
        lines.append("")
        lines.append("| Stars | Count |")
        lines.append("|---|---|")
        for n in (1, 2, 3, 4, 5):
            key1, key2 = f"{n} star", f"{n} stars"
            c = stars.get(key1, stars.get(key2, 0))
            lines.append(f"| {n}★ | {c} |")
        lines.append("")

    # ---- Category totals ----
    totals = exec_summary.get("totals_by_category") or {}
    if totals:
        lines.append("### Categories")
        lines.append("")
        for k, v in totals.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    # ---- Canonical clusters with solutions ----
    if canonical:
        lines.append("## Canonical Clusters")
        lines.append("")
        for c in canonical:
            cid = c.get("cluster_id", c.get("id"))
            reason = _safe(c.get("reason") or c.get("canonical_reason"))
            count = c.get("count") or c.get("size") or 0
            share = c.get("share_%") or 0
            coh = c.get("centroid_sim_mean") or 0
            sol = c.get("solution") or {}
            bullets = sol.get("bullets") or []
            backlog = sol.get("backlog")
            conf = sol.get("confidence")
            quotes = c.get("quotes") or []
            sug = sug_by_id.get(int(cid)) if cid is not None else None

            lines.append(f"### Cluster #{cid}: {reason}")
            lines.append("")
            lines.append(f"**Count:** {count}  ·  **Share:** {share}%  ·  **Cohesion:** {_fmt_num(coh, 2)}"
                         + (f"  ·  **Solution confidence:** {_fmt_pct((conf or 0) * 100)}" if conf is not None else ""))
            lines.append("")
            if sol.get("high_risk"):
                lines.append("> ⚠️  **High risk** — safety/access concerns mentioned.")
            if sol.get("expectation_gap"):
                lines.append("> 💡 **Expectation gap** — users want a missing feature.")
            if bullets:
                lines.append("")
                lines.append("**Recommended actions:**")
                lines.append(_bullet_list(bullets))
            if sug:
                lines.append("")
                lines.append("**Auto-suggestions:**")
                lines.append(_bullet_list(sug))
            if backlog:
                lines.append("")
                lines.append(f"**Backlog:** {backlog}")
            if quotes:
                lines.append("")
                lines.append("**Representative quotes:**")
                for q in quotes[:2]:
                    qtext = q if isinstance(q, str) else (q.get("quote") if isinstance(q, dict) else "")
                    if qtext:
                        lines.append(f"> {qtext}")
            lines.append("")

    # ---- Top reasons overall ----
    top_reasons = exec_summary.get("top_reasons_overall") or {}
    if top_reasons:
        lines.append("## Top Reasons Overall")
        lines.append("")
        for cat, reasons in top_reasons.items():
            if not reasons:
                continue
            lines.append(f"### {cat}")
            lines.append(_bullet_list([str(r) for r in reasons[:5]]))
            lines.append("")

    # ---- Top aspects ----
    aspects = exec_summary.get("top_aspects") or []
    if aspects:
        lines.append("## Top Aspects")
        lines.append("")
        for a in aspects[:6]:
            lines.append(f"### {_safe(a.get('aspect'))}")
            mentions = a.get("mentions") or 0
            share = a.get("share_of_reviews")
            share_txt = f" · {_fmt_pct((share or 0) * 100)}" if share else ""
            lines.append(f"_{mentions} mentions{share_txt}_")
            lines.append("")
            for bc in a.get("by_category") or []:
                lines.append(f"**{_safe(bc.get('category'))}** — {bc.get('count', 0)} mentions")
                rs = bc.get("reasons") or []
                if rs:
                    lines.append(_bullet_list([str(r) for r in rs[:3]]))
                lines.append("")

    # ---- Per-platform contributions ----
    per_platform = contributions.get("per_platform") or []
    if per_platform:
        lines.append("## Platform Contributions")
        lines.append("")
        lines.append("| Platform | Share | Used | Avg sentiment | Top reasons |")
        lines.append("|---|---|---|---|---|")
        for p in per_platform:
            top_r = ", ".join((p.get("top_reasons") or [])[:2]) or "—"
            lines.append(f"| {_safe(p.get('platform'))} | {p.get('share_%', 0)}% | {p.get('used', 0)} | "
                         f"{_fmt_num(p.get('avg_sentiment_score'), 3)} | {top_r} |")
        lines.append("")

    # ---- Action items ----
    if action_items:
        lines.append("## Action Items")
        lines.append("")
        for i, item in enumerate(action_items, 1):
            theme = _safe(item.get("theme") or item.get("item"))
            why = item.get("why")
            lines.append(f"### {i}. {theme}")
            if why:
                lines.append(f"_Why: {why}_")
            sugs = item.get("suggestions") or []
            if sugs:
                lines.append(_bullet_list([str(s) for s in sugs]))
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Generated by InsightMesh AI._")
    return "\n".join(lines).strip() + "\n"


# -------------------- HTML rendering --------------------

_HTML_STYLES = """
<style>
  :root {
    --bg: #0a0a0a; --fg: #e4e4e7; --muted: #a1a1aa; --accent: #3b82f6;
    --card: #18181b; --border: #27272a; --green: #10b981; --red: #f43f5e; --amber: #f59e0b;
  }
  @media print {
    :root { --bg: white; --fg: #111; --muted: #555; --card: #fff; --border: #ddd; }
    body { background: white !important; color: #111 !important; }
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--fg);
    max-width: 900px; margin: 2rem auto; padding: 1.5rem; line-height: 1.55;
  }
  h1 { font-size: 1.9rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.35rem; margin-top: 2rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }
  h3 { font-size: 1.1rem; margin-top: 1.4rem; color: var(--accent); }
  .meta { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
  .meta strong { color: var(--fg); }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin: 1rem 0; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.95rem; }
  th, td { border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }
  th { background: var(--card); }
  blockquote { border-left: 3px solid var(--accent); margin: 0.5rem 0; padding: 0.4rem 1rem; color: var(--muted); font-style: italic; background: var(--card); }
  .badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.75rem; border: 1px solid var(--border); margin-right: 0.3rem; }
  .badge.green { background: rgba(16,185,129,0.15); color: #6ee7b7; border-color: #047857; }
  .badge.red   { background: rgba(244,63,94,0.15);  color: #fda4af; border-color: #be123c; }
  .badge.amber { background: rgba(245,158,11,0.15); color: #fcd34d; border-color: #b45309; }
  ul { padding-left: 1.4rem; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .footer { color: var(--muted); font-size: 0.8rem; text-align: center; margin-top: 3rem; border-top: 1px solid var(--border); padding-top: 1rem; }
  .print-hint { color: var(--muted); font-size: 0.85rem; padding: 0.5rem 1rem; background: var(--card); border-radius: 6px; margin-bottom: 1rem; }
  @media print { .print-hint { display: none; } }
</style>
"""


def render_html(final_report: Dict[str, Any]) -> str:
    if not isinstance(final_report, dict):
        raise ValueError("final_report must be a dict")

    meta = final_report.get("meta") or {}
    analysis = final_report.get("analysis") or {}
    overview = analysis.get("overview") or {}
    exec_summary = analysis.get("executive_summary") or {}
    exec_brief = analysis.get("executive_brief") or {}
    canonical = overview.get("canonical_clusters") or []
    contributions = final_report.get("contributions") or {}
    per_review = analysis.get("per_review") or []

    h = html.escape  # local alias

    parts: List[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8" />')
    parts.append(f'<title>InsightMesh — {h(_safe(meta.get("query_used")))}</title>')
    parts.append(_HTML_STYLES)
    parts.append('</head><body>')

    parts.append('<div class="print-hint">💡 To save as PDF, press Cmd/Ctrl + P and choose "Save as PDF".</div>')

    # Header
    parts.append(f'<h1>📊 {h(_safe(meta.get("query_used")))}</h1>')
    parts.append('<div class="meta">')
    parts.append(f'<strong>Mode:</strong> {h(_safe(meta.get("user_mode")))}  ·  ')
    parts.append(f'<strong>Strictness:</strong> {h(_safe(meta.get("strictness")))}  ·  ')
    parts.append(f'<strong>Elapsed:</strong> {h(_safe(meta.get("elapsed_ms")))} ms')
    if meta.get("time_from"):
        parts.append(f'<br/><strong>Time window:</strong> {h(_safe(meta.get("time_from")))} → {h(_safe(meta.get("time_to")))}')
    parts.append(f'<br/><strong>Generated:</strong> {datetime.utcnow().isoformat()}Z')
    parts.append('</div>')

    # Executive Brief
    if exec_brief:
        parts.append('<h2>🎯 Executive Brief</h2><div class="card">')
        pain = exec_brief.get("biggest_pain") or {}
        qw = exec_brief.get("quick_win") or {}
        risk = exec_brief.get("risk_or_opportunity") or {}
        if pain.get("theme"):
            share = pain.get("share_pct")
            share_txt = f' <span class="badge red">{share}% of reviews</span>' if isinstance(share, (int, float)) else ""
            parts.append(f'<p><strong>Biggest pain:</strong> {h(pain.get("theme"))}{share_txt}</p>')
            if pain.get("evidence_quote"):
                parts.append(f'<blockquote>{h(pain.get("evidence_quote"))}</blockquote>')
        if qw.get("actions"):
            parts.append(f'<p><strong>Quick win:</strong> {h(" · ".join(qw["actions"]))}</p>')
            if qw.get("expected_impact"):
                parts.append(f'<p class="meta">Expected impact: {h(qw["expected_impact"])}</p>')
        if risk.get("note"):
            label = risk.get("label") or "Risk/Opportunity"
            tone = "red" if "risk" in label.lower() else "green"
            parts.append(f'<p><span class="badge {tone}">{h(label)}</span> {h(risk["note"])}</p>')
        parts.append('</div>')

    # Key Metrics
    parts.append('<h2>📈 Key Metrics</h2>')
    parts.append('<table><tr><th>Metric</th><th>Value</th></tr>')
    parts.append(f'<tr><td>Mood index (−1 … +1)</td><td>{h(_fmt_num(overview.get("mood_index"), 3))}</td></tr>')
    parts.append(f'<tr><td>Average sentiment (1 … 5★)</td><td>{h(_fmt_num(overview.get("average_sentiment"), 2))}</td></tr>')
    parts.append(f'<tr><td>Reviews analyzed</td><td>{len(per_review)}</td></tr>')
    parts.append('</table>')

    # Canonical clusters
    if canonical:
        parts.append('<h2>🎯 Canonical Clusters</h2>')
        for c in canonical:
            reason = _safe(c.get("reason") or c.get("canonical_reason"))
            count = c.get("count") or c.get("size") or 0
            share = c.get("share_%") or 0
            sol = c.get("solution") or {}
            parts.append(f'<div class="card"><h3>{h(reason)}</h3>')
            parts.append(f'<p class="meta">Count: <strong>{count}</strong> · Share: <strong>{share}%</strong>')
            if sol.get("high_risk"):
                parts.append(' · <span class="badge red">High risk</span>')
            if sol.get("expectation_gap"):
                parts.append(' · <span class="badge amber">Expectation gap</span>')
            parts.append('</p>')
            if sol.get("bullets"):
                parts.append('<p><strong>Recommended actions:</strong></p><ul>')
                for b in sol["bullets"]:
                    parts.append(f'<li>{h(str(b))}</li>')
                parts.append('</ul>')
            for q in (c.get("quotes") or [])[:2]:
                qt = q if isinstance(q, str) else q.get("quote", "")
                if qt:
                    parts.append(f'<blockquote>{h(qt)}</blockquote>')
            parts.append('</div>')

    # Platform contributions
    per_platform = contributions.get("per_platform") or []
    if per_platform:
        parts.append('<h2>🌐 Platform Contributions</h2>')
        parts.append('<table><tr><th>Platform</th><th>Share</th><th>Used</th><th>Avg sentiment</th></tr>')
        for p in per_platform:
            parts.append(
                f'<tr><td>{h(_safe(p.get("platform")))}</td>'
                f'<td>{p.get("share_%", 0)}%</td>'
                f'<td>{p.get("used", 0)}</td>'
                f'<td>{h(_fmt_num(p.get("avg_sentiment_score"), 3))}</td></tr>'
            )
        parts.append('</table>')

    # Footer
    parts.append('<div class="footer">Generated by InsightMesh AI — '
                 f'{datetime.utcnow().isoformat()}Z</div>')
    parts.append('</body></html>')
    return "\n".join(parts)


# -------------------- Routes --------------------

@router.get("/export/_ping", tags=["Export"])
def export_ping() -> Dict[str, Any]:
    return {"ok": True, "formats": ["md", "html"]}


@router.get(
    "/export/run/{run_id}.md",
    tags=["Export"],
    summary="Download a stored run as Markdown",
    response_class=Response,
)
def export_run_md(run_id: int) -> Response:
    row = get_run(run_id)
    if not row or not row.get("report"):
        raise HTTPException(404, f"Run {run_id} not found.")
    md = render_markdown(row["report"])
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="insightmesh_run_{run_id}.md"'},
    )


@router.get(
    "/export/run/{run_id}.html",
    tags=["Export"],
    summary="View a stored run as styled HTML (print → PDF)",
    response_class=Response,
)
def export_run_html(run_id: int) -> Response:
    row = get_run(run_id)
    if not row or not row.get("report"):
        raise HTTPException(404, f"Run {run_id} not found.")
    body = render_html(row["report"])
    return Response(content=body, media_type="text/html; charset=utf-8")


@router.post(
    "/export/report.md",
    tags=["Export"],
    summary="Render a final_report payload to Markdown",
    response_class=Response,
)
def export_report_md(payload: ReportPayload) -> Response:
    try:
        md = render_markdown(payload.final_report)
    except Exception as e:
        raise HTTPException(400, f"Render failed: {e}")
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="insightmesh_report.md"'},
    )


@router.post(
    "/export/report.html",
    tags=["Export"],
    summary="Render a final_report payload to styled HTML",
    response_class=Response,
)
def export_report_html(payload: ReportPayload) -> Response:
    try:
        body = render_html(payload.final_report)
    except Exception as e:
        raise HTTPException(400, f"Render failed: {e}")
    return Response(content=body, media_type="text/html; charset=utf-8")
