# backend/insight/hierarchy/decomposer.py
"""
Hierarchical Aspect Decomposer.

Solves the "Battery / charging — 100%" problem.

Today, when a review says "the cold-weather range drops 30%", the system files
it under the broad "battery" aspect and stops there. The user is left wondering
WHAT about the battery — range? charging speed? port reliability? cold-weather
loss? supercharger queue waits? Those are FIVE different problems engineering
would solve in five different ways.

This module takes the flat aspect breakdown and decomposes each aspect with
≥6 mentions into 2-5 specific sub-issues. Each sub-issue gets:
  - LLM-generated specific name ("Highway range below EPA spec")
  - mention count, share of aspect, share of all complaints
  - average sentiment within the sub-cluster
  - severity tier (inherited from severity scorer)
  - sample quotes
  - fix-difficulty estimate (engineering / firmware / docs / process)

For ASPECTS with sentiment_stars < 3.5 we drill into complaints.
For ASPECTS with sentiment_stars >= 4.0 we drill into praise themes.
For mixed aspects (3.5-4.0) we do both.

The output makes the dashboard genuinely actionable — you can finally tell
the difference between "fix range anxiety" and "fix the charging port flap."

Method (every step has a heuristic fallback so it never returns nothing):
  1. Group all reviews by their dominant aspect mention
  2. Within each aspect bucket, semantically sub-cluster the comments (mini
     k-means on embeddings, k=auto from sqrt(n))
  3. For each sub-cluster, pick the most informative quotes
  4. Ask LLM: "Give me a specific 4-7 word name for this sub-issue"
     (with a clean keyword-extraction fallback if LLM unavailable)
  5. Score severity, sentiment, fix-difficulty
  6. Return the hierarchy
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except Exception:
    np = None

try:
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
except Exception:
    KMeans = None
    TfidfVectorizer = None

try:
    from backend.utils import llm as llm_client
except Exception:
    llm_client = None

try:
    from backend.insight.severity.scorer import score_text as score_severity_text
except Exception:
    score_severity_text = None


log = logging.getLogger("insightmesh.hierarchy")

_STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}

# Generic stopwords — kept tight, real noise filtering happens in TF-IDF
_STOP = {
    "the", "and", "for", "with", "that", "have", "this", "from", "your", "you",
    "are", "was", "were", "has", "had", "can", "could", "would", "should", "but",
    "very", "just", "really", "still", "even", "much", "more", "than", "then",
    "they", "them", "their", "what", "when", "where", "while", "which", "after",
    "into", "about", "over", "some", "such", "also", "only", "like", "been",
}


# --------------- Helpers ---------------

def _safe_int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _safe_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t for t in re.findall(r"[a-z][a-z0-9'\-]{2,}", text.lower()) if t not in _STOP]


def _which_aspects(text: str, taxonomy_aspects: Dict[str, Dict[str, Any]]) -> List[str]:
    """Return all aspect keys mentioned in this text (lexical match)."""
    if not text:
        return []
    tl = text.lower()
    hits: List[str] = []
    for aspect_key, spec in taxonomy_aspects.items():
        # Phrases first (higher specificity)
        if any(p in tl for p in (spec.get("phrases") or [])):
            hits.append(aspect_key)
            continue
        # Then aliases at word boundaries
        for alias in (spec.get("aliases") or []):
            if re.search(rf"\b{re.escape(alias)}\b", tl):
                hits.append(aspect_key)
                break
    return hits


def _heuristic_subcluster_name(quotes: List[str], aspect_label: str) -> str:
    """Fast TF-IDF based name when LLM unavailable. Extracts the salient bi/tri-gram."""
    if not quotes:
        return f"{aspect_label} concern"
    if TfidfVectorizer is None:
        # Even simpler: most common bigram outside stopwords
        bigrams: Counter = Counter()
        for q in quotes:
            toks = _tokenize(q)
            for i in range(len(toks) - 1):
                bigrams[(toks[i], toks[i + 1])] += 1
        if bigrams:
            (a, b), _ = bigrams.most_common(1)[0]
            return f"{a.title()} {b.title()}"
        return f"{aspect_label} concern"

    try:
        vec = TfidfVectorizer(
            ngram_range=(2, 3),
            min_df=1,
            max_df=0.9,
            stop_words="english",
            max_features=80,
        )
        X = vec.fit_transform([q for q in quotes if q.strip()])
        if X.shape[0] == 0:
            return f"{aspect_label} concern"
        scores = X.mean(axis=0)
        scores = scores.A1 if hasattr(scores, "A1") else scores
        terms = vec.get_feature_names_out()
        ranked = sorted(zip(terms, scores), key=lambda x: x[1], reverse=True)
        top = ranked[0][0] if ranked else None
        if not top:
            return f"{aspect_label} concern"
        # Title-case but keep "and"/"or" lower
        smalls = {"and", "or", "the", "for", "of", "in", "on", "to", "a", "an", "with"}
        return " ".join(w if w in smalls and i > 0 else w.capitalize() for i, w in enumerate(top.split()))
    except Exception:
        return f"{aspect_label} concern"


def _llm_name_subclusters(
    product: str,
    aspect_label: str,
    sub_clusters: List[Dict[str, Any]],
) -> Optional[List[str]]:
    """Batch-name sub-clusters with a single LLM call. Returns names list aligned to input order."""
    if llm_client is None or llm_client.available_backend() == "none":
        return None
    if not sub_clusters:
        return None

    blocks = []
    for i, sc in enumerate(sub_clusters):
        quotes = sc.get("quotes", [])[:3]
        # Trim each quote to keep prompt small
        snip = "\n  - " + "\n  - ".join(q[:180] for q in quotes if q)
        blocks.append(f"SUB-CLUSTER {i + 1}:{snip}")
    blob = "\n\n".join(blocks)[:4000]

    prompt = f"""You are a product analyst. The product is "{product}" and the aspect is "{aspect_label}".

Below are sub-clusters of review quotes that all relate to "{aspect_label}". Give each sub-cluster a SPECIFIC 3-7 word name that describes the actual problem or theme.

{blob}

Rules:
- Names must be SPECIFIC, not "Battery issue" or "Quality concern".
- Use plain words from the reviews when possible.
- If a sub-cluster has mixed themes, focus on the most common one.
- Title Case but keep "and", "or", "the", "of" lowercase.

Return EXACTLY this JSON shape (no markdown, no extra prose):
{{"names": ["Name for cluster 1", "Name for cluster 2", ...]}}
"""

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        if not isinstance(parsed, dict):
            return None
        names = parsed.get("names")
        if not isinstance(names, list):
            return None
        # Pad/truncate to match input length
        names = [str(n).strip() for n in names if isinstance(n, (str,)) and str(n).strip()]
        if len(names) < len(sub_clusters):
            return None
        return names[:len(sub_clusters)]
    except Exception as e:
        log.debug("[hierarchy] sub-cluster naming LLM call failed: %s", e)
        return None


def _estimate_fix_difficulty(quotes: List[str], severity_tier: str) -> Dict[str, Any]:
    """Heuristic fix-difficulty: engineering / firmware / docs / process / hardware."""
    blob = " ".join(quotes).lower()
    if any(w in blob for w in ("recall", "fire", "smoke", "hardware", "physical", "build", "material", "warp", "crack")):
        category = "hardware"
        effort = "high"
    elif any(w in blob for w in ("software", "update", "ota", "firmware", "bug", "crash", "app", "ui", "ux", "menu")):
        category = "firmware"
        effort = "medium" if severity_tier not in ("CRITICAL", "HIGH") else "high"
    elif any(w in blob for w in ("documentation", "manual", "instructions", "tutorial", "guide", "explain")):
        category = "documentation"
        effort = "low"
    elif any(w in blob for w in ("support", "service", "wait", "response", "agent", "rude", "policy", "warranty")):
        category = "process"
        effort = "medium"
    else:
        category = "engineering"
        effort = "medium"
    return {"category": category, "effort": effort}


def _sub_cluster_one_aspect(
    aspect_key: str,
    aspect_label: str,
    member_reviews: List[Dict[str, Any]],
    embedder: Any = None,
    max_sub: int = 5,
    min_sub_size: int = 2,
) -> List[Dict[str, Any]]:
    """Sub-cluster reviews mentioning this aspect. Returns a list of sub-issue dicts."""
    n = len(member_reviews)
    if n < 3:
        # Too few for clustering — return a single sub-issue
        return [{
            "members": list(range(n)),
            "quotes": [(r.get("translated_text") or r.get("original") or "")[:240] for r in member_reviews[:3]],
        }]

    # k = sqrt(n), clamped to [2, max_sub]
    import math
    k = max(2, min(max_sub, int(math.sqrt(n))))
    if k >= n:
        k = max(2, n - 1)

    texts = [(r.get("translated_text") or r.get("original") or "").strip() for r in member_reviews]
    texts = [t if t else " " for t in texts]

    # Try embeddings first (best quality), fall back to TF-IDF (still pretty good)
    X = None
    if embedder is not None and np is not None:
        try:
            vecs = embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            X = vecs
        except Exception:
            X = None

    if X is None and TfidfVectorizer is not None:
        try:
            vec = TfidfVectorizer(ngram_range=(1, 2), max_features=300, stop_words="english", min_df=1)
            X = vec.fit_transform(texts).toarray()
        except Exception:
            X = None

    labels: List[int] = []
    if X is not None and KMeans is not None and X.shape[0] > k:
        try:
            km = KMeans(n_clusters=k, n_init=4, random_state=42)
            labels = km.fit_predict(X).tolist()
        except Exception:
            labels = []

    if not labels:
        # Last fallback: single sub-cluster
        return [{
            "members": list(range(n)),
            "quotes": [t[:240] for t in texts[:3]],
        }]

    # Group by label
    grouped: Dict[int, List[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        grouped[lab].append(i)

    # Build sub-issue records; drop very small ones (noise)
    out: List[Dict[str, Any]] = []
    for lab, members in grouped.items():
        if len(members) < min_sub_size and len(grouped) > 2:
            continue
        # Pick best quotes — prefer mid-length, well-formed
        ranked = sorted(members, key=lambda i: -min(len(texts[i]), 220))
        quotes = []
        for i in ranked[:5]:
            q = texts[i].strip()
            if 30 <= len(q) <= 280 and q not in quotes:
                quotes.append(q)
            if len(quotes) >= 3:
                break
        if not quotes:
            quotes = [texts[ranked[0]][:240]]
        out.append({"members": members, "quotes": quotes})

    return out


# --------------- Public API ---------------

def decompose_aspects(
    *,
    per_review: List[Dict[str, Any]],
    aspect_sentiment: Dict[str, Any],
    taxonomy: Dict[str, Any],
    product: str,
    embedder: Any = None,
    min_aspect_mentions: int = 6,
    max_aspects: int = 10,
) -> List[Dict[str, Any]]:
    """
    For each aspect with enough mentions, produce a hierarchical breakdown:

      [
        {
          "aspect": "battery",
          "label": "Battery",
          "total_mentions": 67,
          "share_of_complaints_pct": 32.1,
          "avg_sentiment_stars": 2.2,
          "verdict": "struggling",
          "sub_issues": [
            {
              "name": "Highway range below spec",
              "mentions": 28,
              "share_of_aspect_pct": 41.8,
              "share_of_all_pct": 13.4,
              "avg_sentiment_stars": 1.9,
              "severity": "HIGH",
              "is_safety": false,
              "personas_most_affected": ["long_term_owner", "critic"],
              "fix_difficulty": {"category": "engineering", "effort": "high"},
              "sample_quotes": [...]
            },
            ...
          ]
        },
        ...
      ]

    Returns an empty list when there's not enough data. The dashboard skips
    rendering the section if this is empty.
    """
    if not per_review or not aspect_sentiment or not taxonomy:
        return []

    taxonomy_aspects = taxonomy.get("aspects") or {}
    if not taxonomy_aspects:
        return []

    total_complaints = sum(1 for r in per_review if r.get("review_category") == "Complaint")
    total_reviews = max(1, len(per_review))

    # Map every review to the aspects it mentions
    review_aspects: List[List[str]] = []
    for r in per_review:
        text = r.get("translated_text") or r.get("original") or ""
        review_aspects.append(_which_aspects(text, taxonomy_aspects))

    # Aggregate aspect → list of (review_idx, review_dict)
    members_by_aspect: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for i, hits in enumerate(review_aspects):
        for ak in hits:
            members_by_aspect[ak].append((i, per_review[i]))

    # Build the hierarchy
    aspect_summaries_lookup = {
        a.get("aspect"): a for a in (aspect_sentiment.get("aspects") or [])
    }

    hierarchy: List[Dict[str, Any]] = []
    for aspect_key, members in members_by_aspect.items():
        n = len(members)
        if n < min_aspect_mentions:
            continue

        spec = taxonomy_aspects.get(aspect_key) or {}
        label = spec.get("label") or aspect_key.replace("_", " ").title()

        # Pull pre-computed sentiment from ABSA if available
        absa = aspect_sentiment_lookup_safe(aspect_summaries_lookup, aspect_key)
        avg_stars = absa.get("avg_sentiment_stars") if absa else None
        if avg_stars is None:
            stars = [_STAR_TO_NUM.get(r.get("sentiment"), 0) for _, r in members]
            stars = [s for s in stars if s > 0]
            avg_stars = round(sum(stars) / len(stars), 2) if stars else None

        verdict = (
            "loved" if (avg_stars or 0) >= 4
            else "struggling" if (avg_stars or 5) < 3
            else "mixed"
        )

        # Sub-cluster within this aspect
        member_reviews = [r for _, r in members]
        sub_clusters = _sub_cluster_one_aspect(aspect_key, label, member_reviews, embedder=embedder)
        if not sub_clusters:
            continue

        # Name each sub-cluster via LLM (single batched call), else heuristic
        names = _llm_name_subclusters(product, label, sub_clusters)
        if not names:
            names = [_heuristic_subcluster_name(sc.get("quotes", []), label) for sc in sub_clusters]

        # Build sub-issue records
        sub_issues: List[Dict[str, Any]] = []
        for sc, sub_name in zip(sub_clusters, names):
            sc_members = sc.get("members") or []
            if not sc_members:
                continue
            sc_reviews = [member_reviews[i] for i in sc_members]
            sc_n = len(sc_members)

            # Sub-cluster sentiment
            sc_stars = [_STAR_TO_NUM.get(r.get("sentiment"), 0) for r in sc_reviews]
            sc_stars = [s for s in sc_stars if s > 0]
            sc_avg = round(sum(sc_stars) / len(sc_stars), 2) if sc_stars else None

            # Severity — use the worst severity across the sub-cluster's quotes
            sc_severity = "MEDIUM"
            is_safety = False
            is_a11y = False
            if score_severity_text:
                rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
                worst = "LOW"
                for q in sc.get("quotes", []):
                    sev = score_severity_text(q)
                    if rank.get(sev.get("severity", "LOW"), 0) > rank.get(worst, 0):
                        worst = sev["severity"]
                    if sev.get("is_safety"):
                        is_safety = True
                    if sev.get("is_accessibility"):
                        is_a11y = True
                sc_severity = worst

            # Personas most affected (top 2 by count)
            persona_count: Counter = Counter()
            for r in sc_reviews:
                # Persona may be set on review (added if personas module ran)
                p = r.get("persona_key") or r.get("persona")
                if p:
                    persona_count[p] += 1
            top_personas = [p for p, _ in persona_count.most_common(2)]

            sub_issues.append({
                "name": sub_name,
                "mentions": sc_n,
                "share_of_aspect_pct": round(100 * sc_n / max(1, n), 1),
                "share_of_all_pct": round(100 * sc_n / total_reviews, 1),
                "avg_sentiment_stars": sc_avg,
                "severity": sc_severity,
                "is_safety": is_safety,
                "is_accessibility": is_a11y,
                "personas_most_affected": top_personas,
                "fix_difficulty": _estimate_fix_difficulty(sc.get("quotes", []), sc_severity),
                "sample_quotes": sc.get("quotes", [])[:3],
            })

        # Sort sub-issues by mentions desc, but bump CRITICAL/safety to top
        def _rank(s):
            sev_bump = 10000 if (s.get("severity") == "CRITICAL" or s.get("is_safety")) else 0
            return -(sev_bump + s.get("mentions", 0))
        sub_issues.sort(key=_rank)

        hierarchy.append({
            "aspect": aspect_key,
            "label": label,
            "total_mentions": n,
            "share_of_complaints_pct": round(100 * n / max(1, total_complaints), 1) if total_complaints else None,
            "share_of_all_pct": round(100 * n / total_reviews, 1),
            "avg_sentiment_stars": avg_stars,
            "verdict": verdict,
            "sub_issues": sub_issues,
        })

    # Sort top-level aspects by mention count
    hierarchy.sort(key=lambda a: -a["total_mentions"])
    return hierarchy[:max_aspects]


def aspect_sentiment_lookup_safe(table: Dict[str, Any], key: str) -> Dict[str, Any]:
    return table.get(key) or {}
