# backend/insight/intelligence/synthesizer.py
# Intelligence Synthesizer.
#
# A post-processing layer that connects insights ACROSS dashboard sections to
# make the whole report smarter. It does NOT add a new dashboard section — it
# enriches the existing `overview` / `per_review` outputs with cross-referenced
# intelligence so other sections can render richer signals.
#
# Three outputs (see CLAUDE.md "THE BUILD: Intelligence Synthesizer"):
#   1. Review Intelligence Scores  — `_intelligence` block on every per_review item
#   2. Cross-Section Insights      — `overview["cross_insights"]` list
#   3. Adaptive Summary Brief      — `overview["_summary_brief"]` string fed to the
#                                    executive-summary LLM call
#
# Hard rules (see CLAUDE.md "What NOT to Do"):
#   - Pure computation. The scorer and cross-insight detector make NO LLM/network/
#     disk calls — they are regex + heuristics over data already in the dicts.
#     (The single LLM call lives in build_smart_summary, which only CONSUMES the
#      brief this module produces.)
#   - Runs for ALL depth modes (quick/balanced/deep) — no quick_mode guard.
#   - Additive only: it ADDS new fields, never removes/renames existing ones.
#   - Fail-open: every entry point is defensive; a crash anywhere degrades to a
#     valid-but-empty result rather than breaking the dashboard.

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


# -------------------- shared helpers --------------------

_STAR_RE = re.compile(r"\s*(\d+)")


def _stars_of(r: Dict[str, Any]) -> Optional[float]:
    """Best-effort star rating (1-5) for a per_review result.

    `sentiment` may be a textual label ("3 stars") or a numeric value; the
    canonical numeric signal is `sentiment_score` in 0..1, which we map onto
    1..5. Returns None when nothing usable is present.
    """
    s = r.get("sentiment")
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = _STAR_RE.match(s)
        if m:
            try:
                return float(int(m.group(1)))
            except ValueError:
                pass
    sc = r.get("sentiment_score")
    if isinstance(sc, (int, float)):
        return 1.0 + 4.0 * float(sc)
    return None


def _parse_date(value: Any) -> Optional[datetime]:
    """Lenient ISO-ish date parser for `published_at`. Returns None on failure."""
    if not value or not isinstance(value, str):
        return None
    txt = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
        # drop tz so naive comparisons are safe across the run
        return dt.replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(txt[:len(fmt) + 2], fmt)
        except Exception:
            continue
    return None


# ==================================================================
# Output 1 — Review Intelligence Scores
# ==================================================================

def score_review_intelligence(review: Dict[str, Any],
                              all_reviews: List[Dict[str, Any]],
                              feature_terms: Optional[set] = None) -> Dict[str, Any]:
    """Score one review on 4 dimensions (0-1 each), averaged to a composite.

    Pure regex + heuristics — no LLM. See CLAUDE.md for the dimension rubric.
    Tuned for YouTube comments (comparisons, price talk, personal experience,
    feature mentions, length) which lack the formal-review signals (durations,
    measurements) the original rubric looked for. `feature_terms` is the product's
    tracked-aspect vocabulary (from Product Intelligence) — mentioning one is a
    specificity signal.
    """
    text = review.get("original") or ""
    word_count = len(text.split())

    # 1. Specificity — concrete details vs vague opinions
    has_numbers = bool(re.search(r'\d+\s*(hour|day|month|week|min|gb|tb|inch|mm|kg|lb|dollar|\$|%)', text, re.I))
    # comparisons incl. YouTube-style competitor phrasing ("quest does this", "meta can")
    has_comparison = bool(re.search(
        r'(compared to|better than|worse than|\bvs\b|\bversus\b|unlike|switched from|'
        r'does this|can do|beats|over the|quest (does|can|has)|meta (can|does|has))', text, re.I))
    # price references — strong specificity signal on YouTube
    has_price = bool(re.search(
        r'(\$\s?\d|\d+\s?(dollars|usd|bucks)|expensive|overpriced|worth it|not worth|'
        r'too pricey|price point|cheaper|affordable|\bcosts?\b)', text, re.I))
    has_feature_name = bool(re.search(
        r'(battery|display|screen|camera|speaker|comfort|weight|price|app|software|build|'
        r'resolution|passthrough|\bfov\b|field of view|lens|strap|fit|audio|microphone|'
        r'tracking|latency|\bfps\b|refresh)', text, re.I))
    # mention of a product-specific tracked aspect (from Product Intelligence)
    has_tracked = False
    if feature_terms:
        has_tracked = bool(re.search(
            r'\b(' + '|'.join(re.escape(t) for t in list(feature_terms)[:40]) + r')\b', text, re.I))
    specificity = min(1.0, (
        (0.3 if has_numbers else 0) +
        (0.2 if has_comparison else 0) +
        (0.15 if has_price else 0) +
        (0.2 if has_feature_name else 0) +
        (0.15 if has_tracked else 0) +
        (0.1 if word_count > 50 else 0) +  # YouTube comments >50 words are unusually detailed
        min(0.3, word_count / 100.0)
    ))

    # 2. Experience depth — first impression vs long-term owner
    has_duration = bool(re.search(r'(after|for)\s+\d+\s*(month|week|year|day)', text, re.I))
    has_usage = bool(re.search(r'(daily|everyday|regular|long.term|months? of use)', text, re.I))
    has_ownership = bool(re.search(r'(bought|purchased|owned|returned|refunded|switched)', text, re.I))
    # first-hand personal experience — common on YouTube, strong depth signal
    has_personal = bool(re.search(
        r"\b(i (bought|use|used|own|owned|returned|tried|wear|wore|have|had|got|tested)|"
        r"i'?ve (been|used|had|owned|tried)|my (headset|unit|one|device|pair)|"
        r"mine (has|is|was|came|broke)|in my experience|for me\b)", text, re.I))
    experience_stage = review.get("experience_stage") or ""
    depth = min(1.0, (
        (0.35 if has_duration else 0) +
        (0.25 if has_usage else 0) +
        (0.25 if has_ownership else 0) +
        (0.2 if has_personal else 0) +
        (0.15 if experience_stage in ("long_term", "expert") else 0)
    ))

    # 3. Actionability — info a product team could act on
    has_problem_detail = bool(re.search(r'(when|if|after|during).*?(crash|lag|heat|drain|break|fail|error|bug|issue)', text, re.I))
    has_suggestion = bool(re.search(r'(should|could|need to|wish|hope|please|add|fix|improve)', text, re.I))
    has_condition = bool(re.search(r'(when it|if you|after the|during|while)', text, re.I))
    actionability = min(1.0, (
        (0.35 if has_problem_detail else 0) +
        (0.3 if has_suggestion else 0) +
        (0.2 if has_condition else 0) +
        (0.15 if word_count > 30 else 0)
    ))

    # ── Platform-specific signals ──
    # YouTube signals (comparisons, price, personal experience, length, feature
    # mentions) are already baked into the universal base scores above. Here we add
    # the nuances unique to Reddit and App Store, then re-clamp to [0, 1].
    platform = (review.get("platform")
                or (review.get("meta") or {}).get("platform") or "").lower()
    if platform == "reddit":
        # Being in a product subreddit = an engaged user.
        if review.get("subreddit"):
            depth += 0.1
        # Reddit users often provide structured pros/cons.
        if re.search(r'(pros?:|cons?:|pros? and cons|on one hand|on the other)', text, re.I):
            specificity += 0.2
            actionability += 0.15
        # Reddit users reference specific use cases.
        if re.search(r'(i use it for|my use case|for (work|gaming|movies|productivity|development))', text, re.I):
            depth += 0.2
            actionability += 0.1
    elif platform == "appstore":
        # App Store reviews are product-focused by default — they chose to write one.
        specificity += 0.1
        # Version/update mentions = engaged user tracking changes.
        if re.search(r'(version|update|\bv\d|after (the )?update|latest|new (version|update))', text, re.I):
            depth += 0.2
            actionability += 0.15
        # Bug reports with reproduction steps = gold.
        if re.search(r"(steps?:|when i|if you|crash|bug|error|freeze|won'?t (load|open|work))", text, re.I):
            actionability += 0.25

    specificity = min(1.0, specificity)
    depth = min(1.0, depth)
    actionability = min(1.0, actionability)

    # 4. Uniqueness — says something no other review said (keyphrase-overlap proxy)
    this_kps = set(k.lower() for k in (review.get("keyphrases") or []) if isinstance(k, str))
    if this_kps:
        overlaps = []
        for other in all_reviews:
            if other is review:
                continue
            other_kps = set(k.lower() for k in (other.get("keyphrases") or []) if isinstance(k, str))
            if other_kps:
                overlaps.append(len(this_kps & other_kps) / len(this_kps))
        avg_overlap = (sum(overlaps) / len(overlaps)) if overlaps else 0.0
        uniqueness = 1.0 - avg_overlap
    else:
        uniqueness = 0.3  # no keyphrases = can't assess, neutral score

    composite = round((specificity + depth + actionability + uniqueness) / 4, 2)

    return {
        "composite": composite,
        "specificity": round(specificity, 2),
        "depth": round(depth, 2),
        "actionability": round(actionability, 2),
        "uniqueness": round(uniqueness, 2),
        "label": "HIGH" if composite >= 0.5 else "MEDIUM" if composite >= 0.3 else "LOW",
    }


def _feature_terms(overview: Dict[str, Any]) -> set:
    """Tracked-aspect vocabulary from Product Intelligence (overview.aspect_sentiment.aspects).
    Used as a specificity signal: a comment naming a tracked aspect is more specific."""
    terms: set = set()
    try:
        aspects = (overview.get("aspect_sentiment") or {}).get("aspects") or []
        for a in aspects:
            if isinstance(a, dict):
                nm = a.get("aspect") or a.get("label") or a.get("name") or ""
                for w in re.findall(r"[a-z]{3,}", str(nm).lower()):
                    terms.add(w)
    except Exception:
        pass
    return terms


def _score_all(per_review: List[Dict[str, Any]], feature_terms: Optional[set] = None) -> None:
    """Attach `_intelligence` to every per_review item, in place."""
    for r in per_review:
        if not isinstance(r, dict):
            continue
        try:
            r["_intelligence"] = score_review_intelligence(r, per_review, feature_terms)
        except Exception:
            r["_intelligence"] = {
                "composite": 0.0, "specificity": 0.0, "depth": 0.0,
                "actionability": 0.0, "uniqueness": 0.3, "label": "LOW",
            }


# ==================================================================
# Output 2 — Cross-Section Insights
# ==================================================================

def _cluster_reason(c: Dict[str, Any]) -> str:
    """Cluster label — canonical_clusters carry `reason`; tolerate other shapes."""
    return (c.get("reason") or c.get("canonical_reason")
            or c.get("canonical_label") or "").strip()


def _cluster_count(c: Dict[str, Any]) -> int:
    try:
        return int(c.get("count") or c.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def find_cross_insights(overview: Dict[str, Any],
                        per_review: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find connections between sections that no single section sees alone.

    Each insight: {type, description, sections, severity}. Pure heuristics.
    """
    insights: List[Dict[str, Any]] = []
    clusters = overview.get("canonical_clusters") or []
    if not isinstance(clusters, list):
        clusters = []

    # 1. Cluster-Temporal correlation — does the biggest complaint cluster
    #    cluster in time around a detected sentiment anomaly?
    anomalies = overview.get("temporal_anomalies") or []
    if isinstance(anomalies, list) and anomalies and clusters:
        try:
            # reviews carry cluster_id + published_at; group review dates by cluster
            top_cluster = max(clusters, key=_cluster_count, default=None)
            if top_cluster is not None and _cluster_count(top_cluster) >= 3:
                try:
                    top_cid = int(top_cluster.get("cluster_id", top_cluster.get("id", -1)))
                except (TypeError, ValueError):
                    top_cid = -1
                for anomaly in anomalies:
                    a_date = _parse_date(anomaly.get("date"))
                    if a_date is None:
                        continue
                    window = timedelta(days=5)
                    in_window = 0
                    top_in_window = 0
                    for r in per_review:
                        rd = _parse_date(r.get("published_at"))
                        if rd is None or abs((rd - a_date).days) > window.days:
                            continue
                        in_window += 1
                        try:
                            if int(r.get("cluster_id", -999)) == top_cid:
                                top_in_window += 1
                        except (TypeError, ValueError):
                            pass
                    if in_window >= 3 and top_in_window / max(1, in_window) > 0.5:
                        insights.append({
                            "type": "cluster_temporal",
                            "description": (
                                f'The sentiment drop around {anomaly.get("date")} is driven by '
                                f'"{_cluster_reason(top_cluster) or "the top theme"}" — '
                                f'{top_in_window} of {in_window} reviews in that window are about it.'
                            ),
                            "sections": ["sentiment", "clusters"],
                            "severity": "warning",
                        })
                        break  # one is enough to make the point
        except Exception:
            pass

    # 2. Confidence-Quality mismatch — HIGH confidence built on LOW-quality reviews
    analysis_conf = overview.get("_analysis_confidence") or {}
    quality_scores = [(_intel(r).get("composite") or 0) for r in per_review]
    avg_quality = (sum(quality_scores) / len(quality_scores)) if quality_scores else 0.0
    overall_conf = analysis_conf.get("overall") or 0
    if overall_conf > 0.6 and avg_quality < 0.25:
        insights.append({
            "type": "confidence_quality_mismatch",
            "description": (
                f"Analysis confidence is {analysis_conf.get('label', 'MODERATE')} but average "
                f"review quality is low ({avg_quality:.0%}). High-confidence conclusions are "
                f"built on thin evidence."
            ),
            "sections": ["analysis_confidence", "voice_of_customer"],
            "severity": "warning",
        })

    # 3. Dominant theme — one cluster owns >40% of reviews → it's THE story
    if clusters:
        total = sum(_cluster_count(c) for c in clusters)
        for c in clusters:
            share = _cluster_count(c) / max(1, total)
            if share > 0.40:
                insights.append({
                    "type": "dominant_theme",
                    "description": (
                        f'"{_cluster_reason(c) or "Unknown"}" dominates at {share:.0%} of all '
                        f'reviews. This is the primary signal — other themes are secondary.'
                    ),
                    "sections": ["clusters", "executive_summary"],
                    "severity": "info",
                })
                break

    # 4. Polarization — multiple themes show reviewers strongly disagreeing
    polarized_clusters = [
        c for c in clusters
        if isinstance(c.get("_evidence"), dict)
        and (c["_evidence"].get("conflict") or {}).get("polarized")
    ]
    if len(polarized_clusters) >= 2:
        insights.append({
            "type": "polarization",
            "description": (
                f"{len(polarized_clusters)} themes show polarized opinions — reviewers "
                f"strongly disagree. Average sentiment may be misleading."
            ),
            "sections": ["sentiment", "clusters", "trust_score"],
            "severity": "warning",
        })

    # 5. High-quality minority signal — the most detailed reviewers converge on a
    #    theme that has LOW overall confidence (worth investigating despite low volume)
    top_reviews = sorted(
        per_review, key=lambda r: _intel(r).get("composite") or 0, reverse=True
    )[:3]
    top_themes = [r.get("canonical_reason", "") for r in top_reviews if r.get("canonical_reason")]
    theme_counts = Counter(top_themes)
    if theme_counts:
        most_common_theme, count = theme_counts.most_common(1)[0]
        if count >= 2 and most_common_theme:
            for c in clusters:
                if _cluster_reason(c) == most_common_theme:
                    ev = c.get("_evidence") or {}
                    conf = ev.get("confidence", 1)
                    if conf < 0.5:
                        insights.append({
                            "type": "quality_signal",
                            "description": (
                                f'The most detailed reviewers focus on "{most_common_theme}" — '
                                f'but it has low overall confidence ({conf:.2f}). Worth '
                                f'investigating despite limited volume.'
                            ),
                            "sections": ["clusters", "voice_of_customer"],
                            "severity": "insight",
                        })
                    break

    # 6. Aspect consensus — when reviewers who mention an aspect overwhelmingly
    #    AGREE (>=80%) on its sentiment, that's a CONFIRMED strength/weakness, not
    #    a contested one. Uses the dense ABSA aspect_sentiment (over ALL reviews).
    aspects = (overview.get("aspect_sentiment") or {}).get("aspects") or []
    n_consensus = 0
    for a in (aspects if isinstance(aspects, list) else []):
        if not isinstance(a, dict):
            continue
        mentions = int(a.get("mentions") or 0)
        # Floor of 3: the ABSA produces modest per-aspect mention counts, so 3 is the
        # smallest sample where ">=80% agree" is meaningful (3/3 unanimous; 2/3=67% won't
        # trip it). The mention count is shown in the text so the evidence base is explicit.
        if mentions < 3:
            continue
        name = a.get("aspect") or "this aspect"
        try:
            pos = float(a.get("pct_positive") or 0)
            neg = float(a.get("pct_negative") or 0)
        except (TypeError, ValueError):
            continue
        if neg >= 80:
            insights.append({
                "type": "strong_consensus_negative",
                "description": (
                    f'Strong consensus: {neg:.0f}% of the {mentions} reviewers who '
                    f'mention "{name}" are negative. This is a confirmed weakness.'
                ),
                "sections": ["aspects", "clusters"],
                "severity": "warning",
            })
            n_consensus += 1
        elif pos >= 80:
            insights.append({
                "type": "strong_consensus_positive",
                "description": (
                    f'Strong consensus: {pos:.0f}% of the {mentions} reviewers who '
                    f'mention "{name}" are positive. This is a confirmed strength.'
                ),
                "sections": ["aspects"],
                "severity": "insight",
            })
            n_consensus += 1
        if n_consensus >= 3:
            break

    # 7. Credibility gap — credible reviewers (ownership + detail) diverge from
    #    casual commenters. A large gap means the raw average is misleading.
    cred = overview.get("credibility_intelligence") or {}
    gap = cred.get("sentiment_gap")
    if isinstance(gap, (int, float)) and abs(gap) >= 0.8:
        if gap > 0:
            insights.append({
                "type": "credibility_gap_positive",
                "description": (
                    f"Credible reviewers (ownership + detail) rate {gap:.1f}★ higher than "
                    f"casual commenters. The raw average undersells this product."
                ),
                "sections": ["sentiment", "credibility"],
                "severity": "insight",
            })
        else:
            insights.append({
                "type": "credibility_gap_negative",
                "description": (
                    f"Credible reviewers rate {abs(gap):.1f}★ lower than casual commenters. "
                    f"The raw average oversells this product."
                ),
                "sections": ["sentiment", "credibility"],
                "severity": "warning",
            })

    return insights


def _intel(r: Dict[str, Any]) -> Dict[str, Any]:
    v = r.get("_intelligence")
    return v if isinstance(v, dict) else {}


# ==================================================================
# Output 2b — Deep cross-insights (need deep_classify signals)
# ==================================================================

def find_deep_cross_insights(overview: Dict[str, Any],
                             per_review: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cross-insights that depend on the deep-classify signals (consensus per
    aspect, expectation-vs-reality, first-impression vs long-term sentiment).

    These run LATER than find_cross_insights — in the deferred enrich phase, once
    `overview["deep_signals"]` exists and each per_review item has `r["deep"]`
    attached. Same shape ({type, description, sections, severity}) so they render
    as CrossInsight cards. Pure heuristics, fail-open.
    """
    insights: List[Dict[str, Any]] = []
    deep = overview.get("deep_signals") or {}
    if not isinstance(deep, dict):
        deep = {}

    # NOTE: aspect CONSENSUS lives in find_cross_insights (it uses the dense
    # aspect_sentiment computed over ALL reviews, not the sparser deep signals).
    # Here we only handle the two signals that REQUIRE deep classification.

    # (b) Expectation vs reality — aggregate the deep classifier's expectation_gap.
    exp = deep.get("expectation_summary") or {}
    if isinstance(exp, dict):
        total_exp = sum(int(exp.get(k) or 0) for k in ("exceeded", "met", "fell_short"))
        if total_exp >= 3:
            fell = int(exp.get("fell_short") or 0)
            exc = int(exp.get("exceeded") or 0)
            if fell / total_exp >= 0.4:
                insights.append({
                    "type": "expectation_gap",
                    "description": (
                        f"Of {total_exp} reviewers who voiced expectations, "
                        f"{fell / total_exp:.0%} say the product FELL SHORT — a meaningful "
                        f"disappointment signal."
                    ),
                    "sections": ["expectations", "trust_score"],
                    "severity": "warning",
                })
            elif exc / total_exp >= 0.4:
                insights.append({
                    "type": "expectation_exceeded",
                    "description": (
                        f"Of {total_exp} reviewers who voiced expectations, "
                        f"{exc / total_exp:.0%} say the product EXCEEDED them — a strong "
                        f"satisfaction signal."
                    ),
                    "sections": ["expectations"],
                    "severity": "insight",
                })

    # (c) Reviewer experience correlation — first-impression vs long-term sentiment.
    first_stars: List[float] = []
    long_stars: List[float] = []
    for r in (per_review or []):
        if not isinstance(r, dict):
            continue
        stage = ((r.get("deep") or {}).get("experience_stage") or {}).get("stage")
        s = _stars_of(r)
        if s is None or not stage:
            continue
        if stage == "first_impression":
            first_stars.append(s)
        elif stage in ("long_term", "expert"):
            long_stars.append(s)
    if len(first_stars) >= 2 and len(long_stars) >= 2:
        fa = sum(first_stars) / len(first_stars)
        la = sum(long_stars) / len(long_stars)
        delta = la - fa
        if delta >= 0.5:
            insights.append({
                "type": "experience_growth",
                "description": (
                    f"Long-term owners rate {delta:.1f}★ HIGHER than first-impression "
                    f"reviewers ({la:.1f}★ vs {fa:.1f}★) — this product grows on people."
                ),
                "sections": ["voice_of_customer", "trust_score"],
                "severity": "insight",
            })
        elif delta <= -0.5:
            insights.append({
                "type": "honeymoon_problem",
                "description": (
                    f"Long-term owners rate {abs(delta):.1f}★ LOWER than first-impression "
                    f"reviewers ({la:.1f}★ vs {fa:.1f}★) — a honeymoon problem: the shine "
                    f"wears off with use."
                ),
                "sections": ["voice_of_customer", "trust_score"],
                "severity": "warning",
            })

    return insights


# ==================================================================
# Output 3 — Adaptive Summary Brief
# ==================================================================

def build_summary_brief(overview: Dict[str, Any],
                        per_review: List[Dict[str, Any]]) -> str:
    """Compose a data-aware brief that the executive-summary LLM uses to decide
    the SHAPE of the summary. Returns "" when the data has nothing distinctive
    to say (the summary then runs as before). No LLM call here.
    """
    lines: List[str] = []

    confidence = overview.get("_analysis_confidence") or {}
    conf_level = confidence.get("overall") or 0

    clusters = overview.get("canonical_clusters") or []
    if not isinstance(clusters, list):
        clusters = []
    total_reviews = len(per_review)

    # Polarization — variance of star ratings
    stars = [s for s in (_stars_of(r) for r in per_review) if s is not None]
    is_polarized = False
    variance = 0.0
    if len(stars) >= 2:
        try:
            variance = statistics.variance(stars)
            is_polarized = variance > 2.0
        except statistics.StatisticsError:
            is_polarized = False

    # Dominant theme
    total_cluster = sum(_cluster_count(c) for c in clusters)
    dominant = None
    for c in clusters:
        share = _cluster_count(c) / max(1, total_cluster)
        if share > 0.35:
            dominant = (_cluster_reason(c), share)
            break

    anomalies = overview.get("temporal_anomalies") or []
    forecast = overview.get("sentiment_forecast") or {}

    # ---- compose ----
    if conf_level and conf_level < 0.4:
        lines.append("INSTRUCTION: Confidence is LOW. Be cautious. Qualify every claim. "
                     "Lead with 'Based on limited data...' Do not make strong recommendations.")

    if is_polarized:
        lines.append(f"DATA SHAPE: Opinions are POLARIZED (variance={variance:.1f}). Do NOT "
                     f"average them. Lead with: 'This product divides opinion sharply.' "
                     f"Explain both sides with evidence.")

    if dominant and dominant[0]:
        name, share = dominant
        lines.append(f"DATA SHAPE: One theme dominates — '{name}' at {share:.0%} of reviews. "
                     f"Make this THE headline. Other themes are secondary.")

    if isinstance(anomalies, list) and anomalies:
        a = anomalies[0]
        drop = a.get("star_drop") or a.get("drop") or 0
        lines.append(f"TEMPORAL: Sentiment dropped {float(drop):.1f}★ around "
                     f"{a.get('date', 'recently')}. Mention this trend and what may have caused it.")

    trend = 0
    if isinstance(forecast, dict):
        trend = (forecast.get("trend_per_week")
                 or (forecast.get("fit") or {}).get("slope_per_week") or 0)
    try:
        trend = float(trend)
    except (TypeError, ValueError):
        trend = 0.0
    if trend < -0.1:
        lines.append(f"TREND: Sentiment is declining at {trend:.2f}★/week. Flag this as concerning.")
    elif trend > 0.1:
        lines.append(f"TREND: Sentiment is improving at +{trend:.2f}★/week. Note the positive trajectory.")

    # Cross-insights (already attached by the time the brief is built)
    cross = overview.get("cross_insights") or []
    for ci in cross[:3]:
        if isinstance(ci, dict) and ci.get("severity") in ("warning", "insight"):
            lines.append(f"CROSS-INSIGHT: {ci.get('description', '')}")

    # Review-quality distribution
    quality_scores = [(_intel(r).get("composite") or 0) for r in per_review]
    high_quality = sum(1 for q in quality_scores if q >= 0.5)
    if high_quality < 3 and total_reviews > 10:
        lines.append(f"QUALITY WARNING: Only {high_quality} of {total_reviews} reviews are "
                     f"detailed/substantive. Most are brief reactions. Note this limitation.")

    return "\n".join(lines)


# ==================================================================
# Main entry point
# ==================================================================

def synthesize(overview: Dict[str, Any],
               per_review: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run all three outputs and mutate `overview` / `per_review` in place.

    Order matters: scores first (cross-insights + brief read them), then
    cross-insights (the brief reads them), then the brief. Fail-open at every
    step — a failure in one output never blocks the others.
    """
    if not isinstance(overview, dict):
        return overview
    per_review = per_review or []

    try:
        _score_all(per_review, _feature_terms(overview))
    except Exception:
        pass

    try:
        overview["cross_insights"] = find_cross_insights(overview, per_review)
    except Exception:
        overview["cross_insights"] = []

    try:
        overview["_summary_brief"] = build_summary_brief(overview, per_review)
    except Exception:
        overview["_summary_brief"] = ""

    return overview
