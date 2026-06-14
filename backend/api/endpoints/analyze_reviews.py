# backend/api/endpoints/analyze_reviews.py

# 1) Load environment variables (must be first)
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

# 2) FastAPI imports
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
from copy import deepcopy

# 3) Standard libraries
import os
import re
import json
import time
import logging

_stage_log = logging.getLogger("insightmesh.timing")
from collections import Counter, defaultdict
import pandas as pd

# Quality scoring for picking the best sample comments and weighting aggregates
from backend.utils.quality import quality_score, is_shallow

# Astroturf / coordinated-review detection
try:
    from backend.utils.astroturf import detect_astroturf
except Exception:
    detect_astroturf = None

# Next-version roadmap + consumer praise-theme aggregator
try:
    from backend.utils.roadmap import build_next_version_roadmap, what_users_love
except Exception:
    build_next_version_roadmap = None
    what_users_love = None

# Per-Insight Evidence Engine — pure post-processing, adds _evidence metadata
try:
    from backend.insight.intelligence.evidence_engine import enrich_with_evidence
except Exception:
    enrich_with_evidence = None

# Temporal Anomaly Detection — pure post-processing, flags sentiment drops
try:
    from backend.insight.intelligence.temporal_detector import detect_temporal_anomalies
except Exception:
    detect_temporal_anomalies = None

# Intelligence Synthesizer — cross-section intelligence; scores review quality,
# finds cross-insights, and builds the adaptive summary brief. Pure (no LLM).
try:
    from backend.insight.intelligence.synthesizer import synthesize as synthesize_intelligence
except Exception:
    synthesize_intelligence = None

# Multi-Pass Self-Correction — one gpt-4o-mini QA pass over the finished analysis,
# run LAST (after evidence/synthesize/summary). Fail-open.
try:
    from backend.insight.intelligence.self_corrector import self_correct
except Exception:
    self_correct = None

# Advanced Intelligence Pack — runs AFTER the self-corrector (see _enrich_overview).
# Competitive intel + purchase advisor each make ONE gpt-4o-mini call; the
# deal-breaker detector is pure regex. All three are fail-open.
try:
    from backend.insight.intelligence.competitive_intel import extract_competitive_intel
except Exception:
    extract_competitive_intel = None
try:
    from backend.insight.intelligence.dealbreaker_detector import detect_dealbreakers
except Exception:
    detect_dealbreakers = None
try:
    from backend.insight.intelligence.purchase_advisor import generate_purchase_advice
except Exception:
    generate_purchase_advice = None

# Reviewer Credibility Intelligence — pure computation (no LLM). Scores each
# reviewer's trustworthiness and computes credibility-weighted sentiment.
try:
    from backend.insight.intelligence.credibility_scorer import (
        score_credibility, compute_weighted_metrics,
    )
except Exception:
    score_credibility = None
    compute_weighted_metrics = None

# Advanced analysis layers (lazy-imported, never raise at module load)
try:
    from backend.insight.absa.aspect_sentiment import analyze_aspects
except Exception:
    analyze_aspects = None
try:
    from backend.insight.sarcasm.detector import detect_batch as detect_sarcasm_batch, adjust_sentiment as adjust_for_sarcasm
except Exception:
    detect_sarcasm_batch = None
    adjust_for_sarcasm = None
try:
    from backend.insight.intent.buyer_intent import classify_batch as classify_intent_batch, aggregate_intents
except Exception:
    classify_intent_batch = None
    aggregate_intents = None
try:
    from backend.insight.severity.scorer import score_cluster as score_cluster_severity
except Exception:
    score_cluster_severity = None
try:
    from backend.insight.severity.risk_register import build_risk_register
except Exception:
    build_risk_register = None
try:
    from backend.insight.trust.score import compute_trust_score
except Exception:
    compute_trust_score = None
try:
    from backend.insight.trust.evidence import assess_evidence
except Exception:
    assess_evidence = None
try:
    from backend.insight.counterfactual.impact import compute_counterfactuals, cumulative_impact
except Exception:
    compute_counterfactuals = None
    cumulative_impact = None
try:
    from backend.insight.forecast.sentiment_predict import forecast_sentiment
except Exception:
    forecast_sentiment = None
try:
    from backend.insight.personas.segmenter import segment_reviewers
except Exception:
    segment_reviewers = None
try:
    from backend.insight.effort.scorer import compute_effort_score
except Exception:
    compute_effort_score = None
try:
    from backend.insight.marketing.angles import extract_marketing_angles
except Exception:
    extract_marketing_angles = None
try:
    from backend.insight.summary.narrator import build_smart_summary
except Exception:
    build_smart_summary = None
# Phase A: universal taxonomy + hierarchical decomposition + cross-signal narrative
try:
    from backend.insight.taxonomy.learner import learn_aspect_taxonomy
except Exception:
    learn_aspect_taxonomy = None
try:
    from backend.insight.hierarchy.decomposer import decompose_aspects
except Exception:
    decompose_aspects = None
try:
    from backend.insight.why_layer.synthesizer import enrich_hierarchy_with_why
except Exception:
    enrich_hierarchy_with_why = None

# Unified LLM client — tries Ollama (free, local), then OpenAI, then None
from backend.utils import llm as llm_client

# Smart per-review understanding — the heart of the analyzer. Natively handles
# romanized / code-mixed text (Hinglish etc.) that the transformer stack gets
# badly wrong. Falls back gracefully when no LLM backend is configured.
try:
    from backend.insight.understanding.review_understanding import (
        understand_review,
        detect_language_smart,
        looks_romanized_indic,
        llm_available as _understanding_llm_available,
    )
except Exception:
    understand_review = None
    detect_language_smart = None
    looks_romanized_indic = None
    _understanding_llm_available = None

# 4) NLP & ML imports
from langdetect import detect
from transformers import pipeline, AutoModelForSeq2SeqLM
from transformers import MarianTokenizer
from transformers import pipeline as zpipeline
from keybert import KeyBERT
import spacy

# 5) Clustering & embeddings
from sentence_transformers import SentenceTransformer
from bertopic import BERTopic
from hdbscan import HDBSCAN
from sklearn.feature_extraction.text import CountVectorizer

# 6) OpenAI v1 SDK client (lazy/optional)
from openai import OpenAI

# 6.1) Universal product-relevance filter (NEW)
try:
    from backend.insight.filters.relevance import (
        filter_comments,
        RelevanceConfig,
    )
except Exception:
    filter_comments = None
    RelevanceConfig = None

# 6.2) Canonical clustering (NEW, universal)
try:
    from backend.insight.reasons.cluster import (
        canonical_clusters,
        ClusterConfig,
    )
except Exception:
    canonical_clusters = None
    ClusterConfig = None

# 6.3) Solutions (NEW)
try:
    from backend.insight.solutions.generator import generate_solutions, ClusterInput
except Exception:
    generate_solutions = None
    ClusterInput = None

# 6.4) Cluster suggestions (NEW)
try:
    from backend.insight.actions.suggester import suggestions_for_clusters, SuggestionConfig
except Exception:
    suggestions_for_clusters = None
    SuggestionConfig = None

# ---------------- Config toggles (speed & safety) ----------------
MAX_GPT_PER_REVIEW      = int(os.getenv("MAX_GPT_PER_REVIEW", "12"))
PHRASES_MODEL           = os.getenv("PHRASES_MODEL", "gpt-3.5-turbo")
SKIP_PHRASE_EXTRACTION  = os.getenv("SKIP_PHRASE_EXTRACTION", "0") in {"1", "true", "True"}
SKIP_ACTION_ITEMS       = os.getenv("SKIP_ACTION_ITEMS", "0") in {"1", "true", "True"}
DEFAULT_STRICTNESS      = os.getenv("STRICTNESS", "ultra")  # ultra|normal|low
USE_REAL_EMOTION        = os.getenv("USE_REAL_EMOTION", "1") in {"1", "true", "True"}
EMOTION_MODEL           = os.getenv("EMOTION_MODEL", "j-hartmann/emotion-english-distilroberta-base")

# Smart per-review understanding (LLM-primary categorization/sentiment/translation).
# ON by default — this is the accuracy fix for code-mixed / romanized text. Every
# call is cached permanently, so the cost is paid once per unique comment. Set to 0
# to fall back to the pure transformer pipeline (faster first run, lower accuracy).
USE_SMART_UNDERSTANDING = os.getenv("USE_SMART_UNDERSTANDING", "1") in {"1", "true", "True"}
# Cap how many reviews get the per-review understanding call on a single run.
# 0 = no cap (recommended; cache makes repeats free). Set a number to bound
# first-run cost on very large batches.
MAX_UNDERSTANDING_PER_RUN = int(os.getenv("MAX_UNDERSTANDING_PER_RUN", "0"))

# RAG docs dirs for solutions grounding (comma-separated)
RAG_DOCS_DIRS = [d.strip() for d in os.getenv("RAG_DOCS_DIRS", "").split(",") if d.strip()]

# 7) OpenAI client (do NOT hard-crash if missing API key)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client: Optional[OpenAI] = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# 8) Initialize NLP pipelines (module singletons)
# Sentiment (multilingual, 1–5 stars)
sentiment_pipe = pipeline(
    "sentiment-analysis",
    model="nlptown/bert-base-multilingual-uncased-sentiment"
)

# Translate non-EN → EN for normalization
tokenizer     = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-mul-en")
seq2seq_model = AutoModelForSeq2SeqLM.from_pretrained("Helsinki-NLP/opus-mt-mul-en")

# Keyphrase extraction
kw_model      = KeyBERT()

# spaCy: prefer en_core_web_sm, but fall back to blank('en') if it isn't installed
try:
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = spacy.blank("en")  # keeps pipeline functional without NER

# 9) Zero-shot classifier (multilingual)
zero_shot = zpipeline("zero-shot-classification", model="facebook/bart-large-mnli")
CANDIDATE_LABELS = [
    "Battery", "Camera", "Display", "Performance",
    "Connectivity", "Software", "Ergonomics", "Other"
]
ZS_THRESHOLD = 0.5  # stricter to reduce noise

# 10) Sentence embedder (+ used by canonical clustering)
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# (Keep BERTopic objects as a fallback path only)
vectorizer_model = CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2, max_df=0.9)
topic_model = BERTopic(
    language="multilingual",
    vectorizer_model=vectorizer_model,
    calculate_probabilities=True,
    nr_topics=None,
    min_topic_size=5,
)

_emotion_pipe = None
_emotion_load_failed = False

def _get_emotion_pipe():
    """Lazy-load the emotion classifier (j-hartmann by default).
    First call downloads ~270MB; subsequent calls are cached. Returns None
    if disabled or load fails — callers must handle that."""
    global _emotion_pipe, _emotion_load_failed
    if not USE_REAL_EMOTION:
        return None
    if _emotion_load_failed:
        return None
    if _emotion_pipe is not None:
        return _emotion_pipe
    try:
        _emotion_pipe = pipeline(
            "text-classification",
            model=EMOTION_MODEL,
            top_k=None,
            truncation=True,
            max_length=512,
        )
        return _emotion_pipe
    except Exception as e:
        import logging
        logging.warning("[emotion] load failed (%s); falling back to category mapping. Set USE_REAL_EMOTION=0 to silence.", e)
        _emotion_load_failed = True
        return None


def _classify_emotion(text: str) -> Dict[str, Any]:
    """Return {label: str, score: float, all: {label: score, ...}}.
    Falls back to neutral when model is disabled/unavailable."""
    pipe = _get_emotion_pipe()
    if not pipe or not text or len(text) < 4:
        return {"label": "neutral", "score": 0.0, "all": {}}
    try:
        raw = pipe(text[:512])
        scores = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw
        if not scores:
            return {"label": "neutral", "score": 0.0, "all": {}}
        all_scores = {item["label"]: round(float(item["score"]), 4) for item in scores}
        top = max(scores, key=lambda x: float(x.get("score", 0)))
        return {
            "label": top["label"],
            "score": round(float(top["score"]), 4),
            "all": all_scores,
        }
    except Exception:
        return {"label": "neutral", "score": 0.0, "all": {}}

# ---------- Utilities --------------------------------------------------------

STAR_TO_MOOD = {
    "1 star": -1.0,
    "2 stars": -0.5,
    "3 stars": 0.0,
    "4 stars": 0.5,
    "5 stars": 1.0,
}
STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}

CANON_CATS = ("Praise", "Complaint", "Suggestion", "Prediction", "Neutral")

def _canon_cat(label: Optional[str]) -> str:
    if not label:
        return "Neutral"
    l = label.strip().lower()
    if l.startswith("praise"): return "Praise"
    if l.startswith("complaint"): return "Complaint"
    if l.startswith("suggestion"): return "Suggestion"
    if l.startswith("prediction"): return "Prediction"
    if l.startswith("neutral"): return "Neutral"
    return "Neutral"

# --- Phrase normalization / cleaning (NEW) -----------------------------------

_BAD_SINGLETONS = {
    "tesla","siri","beta","male","day","la","hey","car","it","they","we","i","you"
}
_GENERIC_REASONS = {
    "General positive feedback",
    "General complaint",
    "General comment",
    "Feature request or expectation gap",
}

_RE_EMOJI = re.compile(r"[\u2600-\u27BF\u1F300-\u1FAD6]+", re.UNICODE)
_RE_WS = re.compile(r"\s+")
_RE_PUNCT_RUN = re.compile(r"[,\.;:]+")
_RE_NONWORD = re.compile(r"[^a-z0-9 \-'/]")

_NORMALIZE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # cold weather + door/lock/handle issues
    (re.compile(r"\b(ice|icy|frozen|freeze|freezing).*(door|lock|handle|latch)\b|\b(door|lock|handle|latch).*(ice|icy|frozen|freeze|freezing)\b", re.I),
     "Door/lock freezes in cold weather"),
    # phone/watch/device required to open/ unlock
    (re.compile(r"\b(phone|watch|device).*(open|unlock|door|handle|access)\b|\b(open|unlock|door|handle|access).*(phone|watch|device)\b", re.I),
     "Requires device to open door; needs manual override"),
    # voice assistant unreliable
    (re.compile(r"\b(siri|alexa|assistant|voice)\b.*\b(refuse|won't|cant|can't|doesn't|not|attitude|fails?)\b", re.I),
     "Voice assistant unreliable"),
    # renting concerns
    (re.compile(r"\brent(?:ing)?\b.*\btesla\b|\btesla\b.*\brent(?:ing)?\b", re.I),
     "Unclear Tesla rental logistics"),
    # glovebox / permission / access
    (re.compile(r"\bglove[\s-]?box\b.*\b(access|open)\b|\b(access|open)\b.*\bglove[\s-]?box\b", re.I),
     "Cannot access glovebox without system permission"),
]

def _basic_clean(s: str) -> str:
    s = s or ""
    s = _RE_EMOJI.sub(" ", s)
    s = _RE_NONWORD.sub(" ", s.lower())
    s = _RE_WS.sub(" ", s).strip()
    s = _RE_PUNCT_RUN.sub(",", s)
    return s

def _normalize_reason(s: str) -> Optional[str]:
    """
    Returns a short, human-readable reason or None if not actionable.
    """
    if not s: return None
    raw = s.strip()
    txt = _basic_clean(raw)

    # hard filters
    if not txt or len(txt) < 3:
        return None
    if txt in _BAD_SINGLETONS:
        return None
    # at least two words
    if " " not in txt:
        return None

    # pattern-based humanization
    for pat, repl in _NORMALIZE_PATTERNS:
        if pat.search(raw) or pat.search(txt):
            return repl

    # generic cleanup: clip overly long tails
    if len(txt) > 120:
        txt = txt[:120].rsplit(" ", 1)[0]

    # title case first letter
    txt = txt[0].upper() + txt[1:]
    return txt

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = (x or "").strip().lower()
        if k and k not in seen:
            seen.add(k); out.append(x.strip())
    return out

def _mine_reason_phrases(text: str) -> List[str]:
    """Capture short phrases after because/due to/since/as; plus 'issue/problem with ...'."""
    if not text:
        return []
    phrases = []
    for m in re.finditer(r"\b(because|due to|since|as)\b\s+(.{5,120}?)(?:[.;!?]|$)", text, flags=re.IGNORECASE):
        chunk = m.group(2).strip()
        if 5 <= len(chunk) <= 120:
            phrases.append(chunk)
    for m in re.finditer(r"\b(issue|problem|bug|fault)\b\s+(with\s+)?([A-Za-z0-9\- ]{3,60})", text, flags=re.IGNORECASE):
        chunk = (m.group(2) or "") + m.group(3)
        chunk = chunk.strip()
        if 3 <= len(chunk) <= 80:
            phrases.append(chunk)
    # NEW: normalize + dedupe
    phrases = [p for p in ( _normalize_reason(p) for p in phrases ) if p]
    return _dedupe_keep_order(phrases)[:3]

def _pick_aspects(review: Dict[str, Any]) -> List[str]:
    labs = review.get("topic_labels") or []
    out = [l for l in labs if l in CANDIDATE_LABELS]
    return out or ["Other"]

def _safe_keyphrases(review: Dict[str, Any]) -> List[str]:
    kps = review.get("keyphrases") or []
    cleaned = [ _normalize_reason(str(k)) for k in kps if isinstance(k, str) and k.strip() ]
    return [k for k in cleaned if k][:5]

def _collect_star_breakdown(per_review: List[Dict[str, Any]]) -> Dict[str, int]:
    counter = Counter()
    for r in per_review:
        lbl = str(r.get("sentiment") or "").strip()
        if lbl:
            counter[lbl] += 1
    return dict(counter)

def _mood_index(per_review: List[Dict[str, Any]]) -> Optional[float]:
    vals = []
    for r in per_review:
        lbl = str(r.get("sentiment") or "")
        if lbl in STAR_TO_MOOD:
            vals.append(STAR_TO_MOOD[lbl])
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)

def _avg_star(per_review: List[Dict[str, Any]]) -> Optional[float]:
    vals = []
    for r in per_review:
        lbl = str(r.get("sentiment") or "")
        if lbl in STAR_TO_NUM:
            vals.append(STAR_TO_NUM[lbl])
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)

def _count_languages(per_review: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return {lang_code: count} for all detected languages."""
    c = Counter()
    for r in per_review:
        lang = (r.get("language") or "").strip().lower()
        if lang and lang != "unknown":
            c[lang] += 1
    return dict(c.most_common())

CATEGORY_TO_EMOTION = {
    "Praise": "Delighted",
    "Complaint": "Frustrated",
    "Suggestion": "Hopeful",
    "Prediction": "Curious",
    "Neutral": "Neutral",
}


EMOTION_LABEL_PRESENTATION = {
    "joy": "Delighted",
    "surprise": "Excited",
    "neutral": "Neutral",
    "sadness": "Disappointed",
    "fear": "Worried",
    "anger": "Frustrated",
    "disgust": "Disgusted",
}


def _compute_sentiment_over_time(per_review: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Bucket per-review entries by their UTC date (when `published_at` is present)
    and emit a daily series: [{date, n, avg_sentiment, mood_index, top_emotion}].
    Entries without a usable timestamp are dropped from the series (but still
    appear in everything else). Days are returned in chronological order.
    """
    from datetime import datetime, timezone
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in per_review:
        ts = r.get("published_at")
        if not isinstance(ts, str) or not ts:
            continue
        try:
            t = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
            dt = datetime.fromisoformat(t)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            day = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            continue
        b = buckets.setdefault(day, {"n": 0, "star_sum": 0, "mood_sum": 0.0, "emotions": Counter()})
        b["n"] += 1
        lbl = str(r.get("sentiment") or "")
        if lbl in STAR_TO_NUM:
            b["star_sum"] += STAR_TO_NUM[lbl]
            b["mood_sum"] += STAR_TO_MOOD[lbl]
        emo = r.get("emotion")
        if isinstance(emo, str) and emo and emo.lower() != "neutral":
            b["emotions"][EMOTION_LABEL_PRESENTATION.get(emo.lower(), emo.title())] += 1

    series: List[Dict[str, Any]] = []
    for day in sorted(buckets.keys()):
        b = buckets[day]
        n = max(1, b["n"])
        top_emo = b["emotions"].most_common(1)
        series.append({
            "date": day,
            "n": b["n"],
            "avg_sentiment": round(b["star_sum"] / n, 2) if b["star_sum"] else None,
            "mood_index": round(b["mood_sum"] / n, 3),
            "top_emotion": top_emo[0][0] if top_emo else None,
        })
    return series


def _category_to_emotion_mix(per_review: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Aggregate emotion labels across all reviews. Uses the real classifier output
    (`emotion` field on each review) when available, falling back to the
    category-based mapping when not.
    """
    c = Counter()
    for r in per_review:
        label = r.get("emotion")
        if isinstance(label, dict):
            label = label.get("label")
        score = float(r.get("emotion_score") or 0.0)
        if isinstance(label, str) and label and label.lower() != "neutral" and score >= 0.35:
            pretty = EMOTION_LABEL_PRESENTATION.get(label.lower(), label.title())
            c[pretty] += 1
            continue
        cat = _canon_cat(r.get("review_category"))
        c[CATEGORY_TO_EMOTION.get(cat, "Neutral")] += 1
    return dict(c)

def _filter_generic_reasons(reasons: List[str]) -> List[str]:
    """Drop generic placeholders if we have any specific reasons."""
    if not reasons:
        return reasons
    specific = [r for r in reasons if r not in _GENERIC_REASONS]
    return specific or reasons[:3]

# --- Small-N-safe BERTopic wrapper (fallback only) -------------------------
def _fit_topics_safe(base_model: BERTopic, docs: List[str], embeddings):
    N = len(docs)
    if N <= 2:
        topics = [0] * N
        probs  = [[1.0]] * N
        return None, topics, probs, {"mode": "small_n_fallback", "n_docs": N}

    tm = deepcopy(base_model)

    umap_model = deepcopy(tm.umap_model)
    current_nc = getattr(umap_model, "n_components", 2)
    safe_nc    = max(1, min(current_nc, N - 2))
    umap_model.n_components = safe_nc
    if hasattr(umap_model, "n_neighbors"):
        current_nn = getattr(umap_model, "n_neighbors", 15)
        umap_model.n_neighbors = max(2, min(current_nn, N - 1))
    if hasattr(umap_model, "init"):
        umap_model.init = "random"
    tm.umap_model = umap_model

    hdb = getattr(tm, "hdbscan_model", None)
    if not isinstance(hdb, HDBSCAN):
        hdb = HDBSCAN(min_cluster_size=5, min_samples=None, prediction_data=True)
    mcs = getattr(hdb, "min_cluster_size", 5)
    safe_mcs = max(2, min(mcs, N))
    ms = getattr(hdb, "min_samples", None)
    safe_ms = (min(safe_mcs, N) if ms is None else max(1, min(ms, N)))

    hdb_safe = HDBSCAN(
        min_cluster_size=safe_mcs,
        min_samples=safe_ms,
        metric=getattr(hdb, "metric", "euclidean"),
        cluster_selection_epsilon=getattr(hdb, "cluster_selection_epsilon", 0.0),
        alpha=getattr(hdb, "alpha", 1.0),
        algorithm=getattr(hdb, "algorithm", "best"),
        leaf_size=getattr(hdb, "leaf_size", 40),
        approx_min_span_tree=getattr(hdb, "approx_min_span_tree", True),
        gen_min_span_tree=getattr(hdb, "gen_min_span_tree", False),
        core_dist_n_jobs=getattr(hdb, "core_dist_n_jobs", 1),
        cluster_selection_method=getattr(hdb, "cluster_selection_method", "eom"),
        prediction_data=True,
    )
    tm.hdbscan_model = hdb_safe
    if N < 5:
        tm.calculate_probabilities = False

    try:
        topics, probs = tm.fit_transform(docs, embeddings)

        # Reduce outliers if any (-1) to the nearest existing topics
        if any(t == -1 for t in topics):
            try:
                new_topics = tm.reduce_outliers(docs, topics, probs, strategy="probabilities")
                if new_topics is not None:
                    topics = new_topics
            except Exception:
                pass

        return tm, topics, probs, {
            "mode": "bertopic_fallback",
            "n_docs": N,
            "probabilities": tm.calculate_probabilities,
        }
    except Exception as e:
        topics = [0] * N
        probs  = [[1.0]] * N
        return None, topics, probs, {
            "mode": "fallback_on_exception",
            "n_docs": N,
            "err": str(e),
        }

# ---------------- Pydantic schema & router ----------------------------------
class ReviewsInput(BaseModel):
    reviews: List[str]
    # NEW: universal, no hard-coding. Optional search/product hint to steer relevance.
    query: Optional[str] = None
    # NEW: allow caller to override strictness (ultra|normal|low); defaults to env.
    strictness: Optional[str] = None
    # NEW: optional parallel metadata list (per-review). Same length as reviews.
    # Keys may include: published_at (ISO), author, score, like_count, platform, video_id, post_id, subreddit
    meta: Optional[List[Dict[str, Any]]] = None

router = APIRouter()

# ---------------- Executive Summary Builder ---------------------------------
def _aggregate_reasons(per_review: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build universal, reasoned insights across categories and aspects.
    Returns a dict that is easy to render on the frontend.
    """
    # counters
    cat_counts = Counter()
    aspect_cat_counts = defaultdict(lambda: Counter())
    # reasons
    reasons_by_cat = defaultdict(lambda: Counter())
    reasons_by_aspect_cat = defaultdict(lambda: defaultdict(Counter))
    # representative quotes
    quotes_by_aspect_cat = defaultdict(lambda: defaultdict(list))

    def _add_reason(cat: str, aspect: str, reason: str, weight: float = 1.0):
        if not reason:
            return
        r = reason.strip()
        if len(r) < 3:
            return
        reasons_by_cat[cat][r] += weight
        reasons_by_aspect_cat[aspect][cat][r] += weight

    for r in per_review:
        cat = _canon_cat(r.get("review_category"))
        cat_counts[cat] += 1

        aspects = _pick_aspects(r)
        kps     = _safe_keyphrases(r)
        text    = r.get("original") or ""

        # Prefer classification phrases if present
        classification = r.get("classification") or {}
        cls_phrases = []
        if isinstance(classification, dict):
            for k, v in classification.items():
                if isinstance(v, list) and v:
                    if _canon_cat(k) == cat:
                        # normalize each phrase (NEW)
                        cls_phrases.extend([p for p in (_normalize_reason(str(x)) for x in v) if p])

        # Fallback reason mining
        mined_reasons = _mine_reason_phrases(text)
        # Final reason candidates: classification > mined phrases > keyphrases
        candidates = cls_phrases or mined_reasons or kps

        # last-resort generic reason if everything filtered out
        if not candidates:
            if cat == "Praise":
                candidates = ["General positive feedback"]
            elif cat == "Complaint":
                candidates = ["General complaint"]
            elif cat == "Suggestion":
                candidates = ["Feature request or expectation gap"]
            else:
                candidates = ["General comment"]

        for a in aspects:
            aspect_cat_counts[a][cat] += 1
            for pr in _dedupe_keep_order(candidates)[:3]:
                _add_reason(cat, a, pr, weight=1.0)
            if len(quotes_by_aspect_cat[a][cat]) < 2:
                quotes_by_aspect_cat[a][cat].append({
                    "quote": text[:500],
                    "sentiment": r.get("sentiment"),
                    "sentiment_score": r.get("sentiment_score"),
                    "keyphrases": kps[:3]
                })

    # Top aspects
    aspect_totals = []
    for a, cc in aspect_cat_counts.items():
        total = sum(cc.values())
        aspect_totals.append((a, total))
    aspect_totals.sort(key=lambda x: x[1], reverse=True)
    top_aspects = [a for a, _ in aspect_totals[:5]]

    aspect_summaries = []
    for a in top_aspects:
        cc = aspect_cat_counts[a]
        total = sum(cc.values())
        if total == 0:
            continue
        share = round(total / max(1, len(per_review)), 3)
        cat_blocks = []
        for cat in CANON_CATS:
            if cc[cat] == 0:
                continue
            reasons_raw = [r for r, _ in reasons_by_aspect_cat[a][cat].most_common(7)]
            reasons = _filter_generic_reasons(reasons_raw)[:5]
            quotes  = quotes_by_aspect_cat[a][cat][:2]
            cat_blocks.append({
                "category": cat,
                "count": int(cc[cat]),
                "reasons": reasons,
                "quotes": quotes
            })
        aspect_summaries.append({
            "aspect": a,
            "mentions": int(total),
            "share_of_reviews": share,
            "by_category": cat_blocks
        })

    # De-genericize overall reasons per category
    top_reasons_overall = {
        cat: _filter_generic_reasons([r for r, _ in reasons_by_cat[cat].most_common(12)])[:7]
        for cat in CANON_CATS
    }

    return {
        "totals_by_category": dict(cat_counts),
        "top_reasons_overall": top_reasons_overall,
        "top_aspects": aspect_summaries
    }

def _heuristic_actions(executive: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate actions when OpenAI is not available:
    for aspects with high complaint share suggest basic remediations.
    """
    actions = []
    for block in executive.get("top_aspects", []):
        aspect = block["aspect"]
        total  = block["mentions"]
        bycat  = {c["category"]: c for c in block.get("by_category", [])}
        comp   = bycat.get("Complaint", {})
        sugg   = bycat.get("Suggestion", {})
        if total <= 0:
            continue
        comp_share = (comp.get("count", 0) / total) if total else 0.0
        if comp_share >= 0.35 or comp.get("count", 0) >= 5:
            reasons = comp.get("reasons", [])[:3]
            actions.append({
                "theme": aspect,
                "why": f"{int(comp.get('count',0))} complaints (~{int(comp_share*100)}%)",
                "suggestions": [
                    f"Investigate {aspect.lower()} complaints focusing on: {', '.join(reasons) or 'key failure modes'}",
                    f"Ship a quick fix or comms update addressing top reason: {reasons[0] if reasons else 'the main issue'}",
                    f"Add telemetry / A-B test to validate improvements in {aspect.lower()} experience"
                ]
            })
        elif sugg.get("count", 0) >= 3:
            reasons = sugg.get("reasons", [])[:2]
            actions.append({
                "theme": aspect,
                "why": f"{sugg.get('count',0)} user suggestions",
                "suggestions": [
                    f"Prototype top-requested {aspect.lower()} enhancement",
                    f"Publish roadmap note acknowledging: {', '.join(reasons) or 'frequent requests'}"
                ]
            })
    return actions

# ===== Back-compat aliases (fix NameError if older code imports these) =====
aggregate_reasons = _aggregate_reasons
heuristic_actions = _heuristic_actions


def _intel_context_and_category(product_intelligence: Any) -> Tuple[str, str]:
    """Derive (context_string, category) from a ProductIntelligence object or dict.

    The context string is the compact, prompt-ready summary of what the product IS
    (category, segment, direct aspects, buyer expectations). It is threaded into the
    AI summary, the roadmap remediation generator, and ABSA so every layer reads a
    symptom in-domain (headphone ear warmth = comfort, not a thermal defect).
    Returns ("", "") when nothing usable was supplied — callers then behave exactly
    as before."""
    pi = product_intelligence
    if pi is None:
        return "", ""
    # Preferred: a ProductIntelligence dataclass instance (has the helper + .category)
    if hasattr(pi, "to_classifier_context"):
        try:
            ctx = pi.to_classifier_context() or ""
        except Exception:
            ctx = ""
        return ctx, (getattr(pi, "category", "") or "")
    # Also accept a plain dict (e.g. ProductIntelligence.to_dict())
    if isinstance(pi, dict):
        lines: List[str] = []
        if pi.get("product"):
            lines.append(f"PRODUCT: {pi['product']}")
        cat = (pi.get("category") or "").strip()
        if cat:
            seg = (pi.get("segment") or "").strip()
            tier = (pi.get("price_tier") or "").strip()
            extra = ", ".join([x for x in (seg, tier) if x])
            lines.append(f"CATEGORY: {cat}" + (f" ({extra})" if extra else ""))
        if pi.get("direct_aspects"):
            lines.append("DIRECT ASPECTS (about the product itself): " + ", ".join(pi["direct_aspects"]))
        if pi.get("ecosystem_aspects"):
            lines.append("ECOSYSTEM (related but separate): " + ", ".join(pi["ecosystem_aspects"]))
        if pi.get("expectation_anchors"):
            lines.append("BUYER EXPECTATIONS: " + ", ".join(pi["expectation_anchors"]))
        return "\n".join(lines), cat
    return "", ""


def _enrich_overview(overview: Dict[str, Any], per_review: Optional[List[Dict[str, Any]]] = None, product_context: str = "", quick_mode: bool = False) -> Dict[str, Any]:
    """
    Add derived intelligence to the overview AFTER its base fields are built:
      - trust_score (uses sentiment + sample + sarcasm + astroturf + severity)
      - sentiment_forecast (uses sentiment_over_time)
      - next_version_roadmap items get counterfactual.* blocks
      - cumulative_impact (what if top-3 are fixed together)
      - personas (reviewer segmentation; only if differentiation exists)
      - customer_effort (CES; only if effort signals detected)
      - marketing_angles (only if multiple strong praise themes exist)
    Never raises — missing modules just skip. Sections that don't have enough
    signal return None/[] and the dashboard simply doesn't render them.
    """
    try:
        sample_size = 0
        # Best-effort sample size from sarcasm_stats.total which mirrors per_review len
        ss = overview.get("sarcasm_stats") or {}
        sample_size = int(ss.get("total") or 0)
    except Exception:
        sample_size = 0

    # ---- Evidence quality (the honesty gate) ----
    # Assess how much REAL review signal we actually have before we let any
    # confident number render. This is what stops a thin/junk sample from being
    # narrated as "Risky — red flags" when the truth is "we don't know yet."
    evidence = None
    if assess_evidence and per_review is not None:
        try:
            evidence = assess_evidence(
                per_review,
                decision_health=(overview.get("buyer_intent_summary") or {}).get("decision_health"),
                language_count=len(overview.get("language_distribution") or {}),
            )
            overview["evidence"] = evidence
        except Exception:
            evidence = None

    # ---- TrustScore ----
    if compute_trust_score:
        try:
            overview["trust_score"] = compute_trust_score(
                average_sentiment=overview.get("average_sentiment"),
                sample_size=sample_size,
                language_count=len(overview.get("language_distribution") or {}),
                astroturf_flag=bool((overview.get("astroturf_signals") or {}).get("flag")),
                sarcasm_stats=overview.get("sarcasm_stats"),
                decision_health=(overview.get("buyer_intent_summary") or {}).get("decision_health"),
                canonical_clusters=overview.get("canonical_clusters"),
                sentiment_over_time=overview.get("sentiment_over_time"),
                evidence=evidence,
            )
        except Exception:
            overview["trust_score"] = None

    # ---- Sentiment forecast ----
    if forecast_sentiment:
        try:
            overview["sentiment_forecast"] = forecast_sentiment(
                overview.get("sentiment_over_time") or [],
                horizon_days=14,
            )
        except Exception:
            overview["sentiment_forecast"] = None

    # ---- Counterfactuals on roadmap items ----
    if compute_counterfactuals:
        try:
            enriched_roadmap = compute_counterfactuals(
                roadmap_items=overview.get("next_version_roadmap") or [],
                canonical_clusters=overview.get("canonical_clusters") or [],
                average_sentiment=overview.get("average_sentiment"),
                current_trust_score=(overview.get("trust_score") or {}).get("score"),
            )
            overview["next_version_roadmap"] = enriched_roadmap
        except Exception:
            pass

    # ---- Cumulative impact of fixing top 3 ----
    if cumulative_impact:
        try:
            overview["cumulative_impact"] = cumulative_impact(
                overview.get("next_version_roadmap") or [],
                top_k=3,
                average_sentiment=overview.get("average_sentiment"),
            )
        except Exception:
            overview["cumulative_impact"] = None

    # ---- Reviewer personas ("people like me") ----
    # Returns [] when not enough differentiation; the dashboard skips rendering then.
    if segment_reviewers and per_review:
        try:
            personas = segment_reviewers(per_review)
            if personas:
                overview["personas"] = personas
        except Exception:
            pass

    # ---- Customer Effort Score ----
    # Returns None when no effort signals; the dashboard skips rendering then.
    if compute_effort_score and per_review:
        try:
            ces = compute_effort_score(per_review)
            if ces:
                overview["customer_effort"] = ces
        except Exception:
            pass

    # ---- Marketing Angles ----
    # Returns [] when no strong praise themes; the dashboard skips rendering then.
    if extract_marketing_angles and per_review:
        try:
            angles = extract_marketing_angles(per_review)
            if angles:
                overview["marketing_angles"] = angles
        except Exception:
            pass

    # ---- Smart Summary moved BELOW ----
    # The executive-summary LLM call now runs AFTER evidence + intelligence
    # synthesis (see the "Intelligence layer" block near the end of this
    # function) so it can consume the adaptive summary brief and the
    # cross-section insights. Keeping it here would run it before those signals
    # exist, so the brief could never shape the narrative.

    # ---- Phase A: Hierarchical aspect decomposition + why-layer narratives ----
    # This is the depth fix: instead of one flat "Battery / charging" label, we get
    # 3-6 specific sub-issues per aspect with their own stats, severity, samples,
    # plus a cross-signal narrative explaining WHO it affects and WHY.
    query_hint = overview.get("_query_hint") or ""
    if per_review and decompose_aspects:
        try:
            # 1) Learn taxonomy (LLM proposes aspects per product; falls back to hand-coded)
            taxonomy_obj = None
            if learn_aspect_taxonomy:
                sample_texts = [
                    (r.get("translated_text") or r.get("original") or "")[:300]
                    for r in per_review[:25] if r.get("original")
                ]
                _t0 = time.perf_counter()
                taxonomy_obj = learn_aspect_taxonomy(query_hint, sample_texts)
                _stage_log.info("[timing] learn_aspect_taxonomy: %.1fs", time.perf_counter() - _t0)
                if taxonomy_obj:
                    overview["aspect_taxonomy"] = {
                        "source": taxonomy_obj.get("source"),
                        "domain_detected": taxonomy_obj.get("domain_detected"),
                        "aspect_count": len(taxonomy_obj.get("aspects") or {}),
                    }

            # 2) Decompose each aspect into sub-issues
            if taxonomy_obj:
                _t0 = time.perf_counter()
                hierarchy = decompose_aspects(
                    per_review=per_review,
                    aspect_sentiment=overview.get("aspect_sentiment") or {},
                    taxonomy=taxonomy_obj,
                    product=query_hint or "the product",
                    embedder=embedder,
                )
                _stage_log.info("[timing] decompose_aspects: %.1fs (%d aspects)",
                                time.perf_counter() - _t0, len(hierarchy or []))
                if hierarchy:
                    # 3) Enrich each sub-issue with a "why" narrative
                    if enrich_hierarchy_with_why:
                        try:
                            _t0 = time.perf_counter()
                            _n_sub = sum(len(a.get("sub_issues") or []) for a in hierarchy)
                            hierarchy = enrich_hierarchy_with_why(
                                hierarchy,
                                product=query_hint or "the product",
                                personas=overview.get("personas") or [],
                            )
                            _stage_log.info("[timing] enrich_hierarchy_with_why: %.1fs (%d sub-issues)",
                                            time.perf_counter() - _t0, _n_sub)
                        except Exception:
                            pass
                    overview["aspect_hierarchy"] = hierarchy
        except Exception as e:
            import logging
            logging.warning("[analyzer] aspect-hierarchy decomposition failed: %s", e)

    # Temporal Anomaly Detection — annotate sharp sentiment drops on the time
    # series with the dominant theme in each window. Pure math, all depths, fail-open.
    if detect_temporal_anomalies is not None:
        try:
            overview["temporal_anomalies"] = detect_temporal_anomalies(
                overview.get("sentiment_over_time") or [], per_review or []
            )
        except Exception:
            overview["temporal_anomalies"] = []

    # ---- Intelligence layer (Evidence → Synthesizer → Smart Summary) ----
    # This ordering is deliberate and runs for ALL depth modes, fail-open:
    #   1. enrich_with_evidence stamps _evidence on clusters/aspects/roadmap and
    #      _analysis_confidence on the root.
    #   2. synthesize scores each review's _intelligence, finds cross_insights,
    #      and builds the adaptive _summary_brief (which reads 1's outputs).
    #   3. build_smart_summary writes the narrative LAST, using the brief so the
    #      executive summary adapts to the actual data shape.
    if enrich_with_evidence is not None and per_review is not None:
        try:
            _t0 = time.perf_counter()
            enrich_with_evidence(overview, per_review)
            _stage_log.info("[timing] enrich_with_evidence: %.2fs", time.perf_counter() - _t0)
        except Exception:
            pass

    if synthesize_intelligence is not None and per_review is not None:
        try:
            _t0 = time.perf_counter()
            synthesize_intelligence(overview, per_review)
            _stage_log.info("[timing] synthesize_intelligence: %.2fs", time.perf_counter() - _t0)
        except Exception:
            pass

    if build_smart_summary:
        try:
            _t0 = time.perf_counter()
            summary = build_smart_summary(
                overview,
                query=(overview.get("_query_hint") or ""),
                product_context=product_context or "",
                summary_brief=(overview.get("_summary_brief") or ""),
            )
            _stage_log.info("[timing] build_smart_summary: %.1fs", time.perf_counter() - _t0)
            if summary:
                overview["smart_summary"] = summary
        except Exception:
            pass

    # 4. Multi-Pass Self-Correction — ONE gpt-4o-mini QA pass over the finished
    #    analysis. Catches cross-layer problems (off-topic clusters, summary/data
    #    mismatch, mislabeled clusters, over-confidence), applies safe corrections,
    #    and grades A-D. LAST step; fully fail-open.
    if self_correct is not None and per_review is not None:
        try:
            _t0 = time.perf_counter()
            self_correct(
                overview,
                per_review,
                product_context=product_context or "",
                product_name=(overview.get("_query_hint") or ""),
            )
            _stage_log.info("[timing] self_correct: %.1fs", time.perf_counter() - _t0)
        except Exception:
            pass

    # Strip private hint fields that shouldn't leak into the API response
    overview.pop("_query_hint", None)
    overview.pop("_summary_brief", None)

    return overview


WISH_VERBS = ("wish", "hope", "should", "could", "would love", "please add", "need", "want", "add", "include", "make it")
_RE_WISH = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in WISH_VERBS) + r")\b", re.IGNORECASE)
_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(\[])|[\n\r]+")

def _is_wish_sentence(s: str) -> bool:
    if not s or len(s) < 12 or len(s) > 240:
        return False
    return bool(_RE_WISH.search(s))

def _clean_wish(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^(it|they|i|we|you)\s+(should|could|would|wish|hope|need|want)\s+", "", s, flags=re.IGNORECASE)
    if s:
        s = s[0].upper() + s[1:]
    return s.rstrip(".,;:!? ").strip()

def _wish_signature(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if len(t) > 2 and t not in {"the", "that", "with", "this", "have", "more", "some", "would", "could", "should", "please"}]
    return " ".join(sorted(set(tokens))[:5])

def _aggregate_customer_wishes(per_review: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pulls user-expressed feature requests, suggestions, and unmet hopes from across reviews.
    Sources, in order of preference:
      1) per_review[].classification.Suggestion  (LLM-extracted phrases)
      2) per_review[].expectations               (regex 'wish/hope/should/could' sentences)
      3) sentences matching wish-verbs scanned from original text as a fallback
    Groups similar wishes via a token-set signature; returns ranked list.
    """
    buckets: Dict[str, Dict[str, Any]] = {}

    def _bump(text: str, original_quote: str, source: str):
        cleaned = _clean_wish(text)
        if not cleaned or len(cleaned) < 10:
            return
        sig = _wish_signature(cleaned)
        if not sig:
            return
        if sig not in buckets:
            buckets[sig] = {"wish": cleaned, "count": 0, "samples": [], "sources": {}}
        b = buckets[sig]
        b["count"] += 1
        if len(cleaned) > len(b["wish"]) * 0.7 and len(cleaned) < 160:
            b["wish"] = cleaned
        if original_quote and len(b["samples"]) < 2:
            q = (original_quote or "")[:240]
            if q and q not in b["samples"]:
                b["samples"].append(q)
        b["sources"][source] = b["sources"].get(source, 0) + 1

    for r in per_review:
        original = (r.get("original") or "").strip()
        cls = r.get("classification") or {}
        for sug in (cls.get("Suggestion") or []):
            if isinstance(sug, str):
                _bump(sug, original, "llm_suggestion")
        for exp in (r.get("expectations") or []):
            if isinstance(exp, str):
                _bump(exp, original, "regex_expectation")
        if r.get("review_category") in ("Suggestion", "Complaint"):
            for sent in _RE_SENTENCE_SPLIT.split(original):
                sent = sent.strip()
                if _is_wish_sentence(sent):
                    _bump(sent, original, "text_wish_verb")

    ranked = sorted(buckets.values(), key=lambda b: (-b["count"], -len(b["wish"])))
    return [
        {"wish": b["wish"], "count": int(b["count"]), "samples": b["samples"], "sources": b["sources"]}
        for b in ranked[:8]
    ]

# -------------------- Fast low-value comment pre-filter --------------------
# Regex/heuristics ONLY — runs BEFORE any transformer or LLM call to cheaply
# drop noise (emoji, one-word reactions, video reactions, bare device lists,
# ultra-short comments). Runs identically for all depth modes.
_PREFILTER_MIN_WORDS = 10
_RE_TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_RE_VIDEO_REACTION = re.compile(
    r"(this video|that video|your video|the video|great video|amazing video|nice video|"
    r"love this video|loved the video|like and subscribe|hit the like|smash (that|the) like|"
    r"please subscribe|first comment|^first[\s!.]*$|who('?s| is) (watching|here)|"
    r"notification (squad|gang)|early (squad|gang)|great breakdown|great review video)", re.I)
_RE_OPINION = re.compile(
    r"(love|hate|lik(e|ed|ing)|dislik|great|good|bad|terribl|awful|amazing|awesome|worst|best|"
    r"prefer|recommend|disappoint|impress|worth|expensiv|cheap|overpric|comfortab|"
    r"heavy|light|sharp|blurr|laggy|smooth|broke|broken|crash|glitch|batter|comfort|"
    r"qualit|return|refund|buy|bought|use|using|wear|fits?|feel|work|"
    r"problem|issue|wish|hope|should|need|too (big|small|heavy|expensive))", re.I)
_RE_PRODUCT_NAME = re.compile(
    r"\b(iphone|ipad|mac\s?book|imac|apple|airpods|vision\s?pro|samsung|galaxy|pixel|"
    r"oneplus|xiaomi|huawei|sony|bose|quest|oculus|meta|playstation|ps5|xbox|nintendo|"
    r"switch|gopro|dji|kindle|surface|tesla|garmin|fitbit|rokid|xreal|hololens|valve|vive|htc)\b", re.I)
_RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF️‍]+")
_PREFILTER_FILLER = {
    "lol", "lmao", "lmfao", "rofl", "fr", "fr fr", "frfr", "bruh", "bro", "based",
    "w", "l", "facts", "real", "fax", "goated", "fire", "cap", "no cap", "yikes",
    "oof", "rip", "same", "wow", "nice", "cool", "ok", "okay", "yep", "nah", "first",
}


def _prefilter_low_value(text: str) -> Optional[str]:
    """Return a drop-reason if this comment is low-value noise, else None."""
    t = (text or "").strip()
    if not t:
        return "empty"
    low = t.lower().strip(" .!?,")
    if low in _PREFILTER_FILLER:
        return "reaction"
    # emoji-only: strip emoji, then any remaining non-word chars; nothing left → drop
    if not re.sub(r"[\s\W_]+", "", _RE_EMOJI.sub("", t)):
        return "emoji_only"
    has_opinion = bool(_RE_OPINION.search(t))
    # video reaction (timestamp or video-meta phrase) with no product opinion
    if (_RE_TIMESTAMP.search(t) or _RE_VIDEO_REACTION.search(t)) and not has_opinion:
        return "video_reaction"
    # bare device/product list (5+ distinct product names, no opinion)
    if len({m.group(0).lower() for m in _RE_PRODUCT_NAME.finditer(t)}) >= 5 and not has_opinion:
        return "product_list"
    # too short for any insight
    if len(t.split()) < _PREFILTER_MIN_WORDS:
        return "too_short"
    return None


# -------------------- CORE, pure function (used by pipeline) ----------------
def analyze_core(
    reviews: List[str],
    *,
    query: Optional[str] = None,
    strictness: Optional[str] = None,
    meta: Optional[List[Dict[str, Any]]] = None,
    product_intelligence: Optional[Any] = None,
    quick_mode: bool = False,
) -> Dict[str, Any]:
    """
    Pure analyzer. Safe to call in-process from the pipeline.

    Args:
      reviews:    list of comment strings to analyze
      query:      optional product/search hint to steer universal relevance scoring
      strictness: 'ultra' | 'normal' | 'low' (defaults to env STRICTNESS)
      meta:       optional parallel list of per-review metadata dicts.
      product_intelligence: optional ProductIntelligence describing the product.
      quick_mode: ACCEPTED FOR BACK-COMPAT BUT UNUSED — it gates nothing here.
                  analyze_core runs ALL its layers identically regardless of this
                  flag. (Quick mode's speedup happens upstream in stream.py, which
                  skips the per-comment deep-classify gate; analysis itself is the
                  same for every depth.)
    """
    if not isinstance(reviews, list) or not all(isinstance(r, str) for r in reviews):
        raise ValueError("`reviews` must be a list of strings.")

    # Compact product context + category, derived once and threaded into the
    # summary narrator, ABSA, and the solution/roadmap generator.
    product_context, product_category = _intel_context_and_category(product_intelligence)

    # ---------- Universal product-relevance prefilter (NEW) ----------
    used_strictness = (strictness or DEFAULT_STRICTNESS)
    kept_indices = list(range(len(reviews)))
    dropped_summary: Dict[str, int] = {}
    terms_used: List[str] = []

    # strictness="off" SKIPS this query-similarity prefilter entirely. Callers that
    # already gate product-relevance upstream (e.g. the stream pipeline's deep_classify
    # tiers) pass "off" so genuine comments that simply don't echo the product name
    # ("the display is sharp") aren't dropped here.
    if filter_comments and RelevanceConfig and str(used_strictness).lower() != "off":
        try:
            kept_indices, dropped_summary, terms_used = filter_comments(
                comments=reviews,
                query=(query or ""),
                embedder=embedder,
                config=RelevanceConfig.for_strictness(used_strictness),
            )
        except Exception:
            kept_indices = list(range(len(reviews)))  # fail-open
            dropped_summary = {}
            terms_used = []
    kept_reviews = [reviews[i] for i in kept_indices]
    # Re-index meta in parallel with kept_reviews so per_review can attach correctly
    kept_meta: List[Dict[str, Any]] = []
    if isinstance(meta, list) and len(meta) == len(reviews):
        kept_meta = [(meta[i] or {}) for i in kept_indices]
    else:
        kept_meta = [{} for _ in kept_reviews]

    # ---------- Fast low-value pre-filter (regex; before any transformer/LLM) ----------
    # Drops emoji/one-word reactions, video reactions, bare device lists, and
    # ultra-short comments cheaply so they never reach the (expensive) per-review
    # transformer/LLM path. Fail-safe: never filter down to an empty set.
    if kept_reviews:
        _pf_r, _pf_m, _pf_dropped = [], [], {}
        for _r, _m in zip(kept_reviews, kept_meta):
            _reason = _prefilter_low_value(_r)
            if _reason:
                _pf_dropped[_reason] = _pf_dropped.get(_reason, 0) + 1
            else:
                _pf_r.append(_r); _pf_m.append(_m)
        _n_drop = len(kept_reviews) - len(_pf_r)
        if _pf_r and _n_drop:
            logging.getLogger("insightmesh.prefilter").info(
                "[prefilter] dropped %d/%d low-value comments: %s",
                _n_drop, len(kept_reviews),
                ", ".join(f"{k}={v}" for k, v in sorted(_pf_dropped.items())))
            for _k, _v in _pf_dropped.items():
                dropped_summary[f"prefilter_{_k}"] = dropped_summary.get(f"prefilter_{_k}", 0) + _v
            kept_reviews, kept_meta = _pf_r, _pf_m
        elif not _pf_r:
            logging.getLogger("insightmesh.prefilter").info(
                "[prefilter] all %d comments matched filters; keeping unfiltered (fail-safe)",
                len(kept_reviews))

    # If everything dropped, return meta + empty payloads (no crash)
    if not kept_reviews:
        return {
            "meta": {
                "input_count": len(reviews),
                "kept_count": 0,
                "dropped_count": len(reviews),
                "dropped_summary": dropped_summary,
                "strictness": used_strictness,
                "terms_used": terms_used[:30],
            },
            "per_review": [],
            "overview": {"average_sentiment": None, "mood_index": None, "stars": {}, "top_keyphrases": [], "clusters": [], "canonical_clusters": []},
            "executive_summary": {"totals_by_category": {}, "top_reasons_overall": {}, "top_aspects": []},
            "action_items": [],
            "topics_debug": {"mode": "no_docs_after_prefilter"}
        }

    # ---------- Main analysis (on kept reviews only) ----------
    results: List[Dict[str, Any]] = []
    texts_for_clustering: List[str] = []

    # ---------- PRE-BATCH transformer inference for speed ----------
    # Instead of 50 individual forward passes per model, batch them all at once.
    # On GPU this is 10-50x faster; on CPU it's still 3-5x faster due to
    # vectorized operations and reduced Python loop overhead.
    _precomputed_sentiments: Dict[int, Dict[str, Any]] = {}
    _precomputed_emotions: Dict[int, Dict[str, Any]] = {}
    _precomputed_zeroshot: Dict[int, Any] = {}

    # [timing] diagnostic accumulators (analyze_core hot path)
    _t_understand_total = 0.0
    _n_understand = 0
    _t_zsfb_total = 0.0
    _n_zsfb = 0

    _batch_texts = [(i, (r or "").strip()) for i, r in enumerate(kept_reviews) if (r or "").strip()]
    if _batch_texts:
        _bt_indices, _bt_strs = zip(*_batch_texts)
        _bt_list = list(_bt_strs)
        _t_batch0 = time.perf_counter()

        # Batch sentiment (nlptown BERT — small, fast)
        try:
            _sent_batch = sentiment_pipe(_bt_list, batch_size=32, truncation=True, max_length=512)
            for i, s in zip(_bt_indices, _sent_batch):
                _precomputed_sentiments[i] = s[0] if isinstance(s, list) else s
        except Exception:
            pass

        # Batch emotion (j-hartmann distilroberta)
        try:
            _emo_pipe = _get_emotion_pipe()
            if _emo_pipe:
                _emo_batch = _emo_pipe(_bt_list, batch_size=32, truncation=True, max_length=512)
                for i, scores in zip(_bt_indices, _emo_batch):
                    if isinstance(scores, list) and scores:
                        all_scores = {item["label"]: round(float(item["score"]), 4) for item in scores}
                        top = max(scores, key=lambda x: float(x.get("score", 0)))
                        _precomputed_emotions[i] = {
                            "label": top["label"],
                            "score": round(float(top["score"]), 4),
                            "all": all_scores,
                        }
        except Exception:
            pass

        # Batch zero-shot (BART-large-MNLI — the biggest bottleneck per review)
        try:
            _zs_batch = zero_shot(_bt_list, CANDIDATE_LABELS, multi_label=True, batch_size=8)
            # zero_shot returns a single dict for 1 input, list of dicts for multiple
            if isinstance(_zs_batch, dict):
                _zs_batch = [_zs_batch]
            for i, zs in zip(_bt_indices, _zs_batch):
                _precomputed_zeroshot[i] = zs
        except Exception as e:
            _stage_log.warning("[timing] batch zero-shot FAILED -> per-review fallback storm: %s", e)

        _stage_log.info("[timing] batch_transformers: %.1fs (precomputed sent=%d emo=%d zs=%d of %d texts)",
                        time.perf_counter() - _t_batch0,
                        len(_precomputed_sentiments), len(_precomputed_emotions),
                        len(_precomputed_zeroshot), len(_bt_list))

    _t_loop0 = time.perf_counter()
    for idx, raw in enumerate(kept_reviews):
        text = (raw or "").strip()
        if not text:
            continue

        # ===== SMART UNDERSTANDING (LLM-primary) =====
        # One cached JSON call that natively handles romanized/code-mixed text
        # (Hinglish etc.). When confident, it overrides the transformer outputs
        # below. Returns None when no LLM backend is available -> pure fallback.
        understanding = None
        _within_understanding_cap = (MAX_UNDERSTANDING_PER_RUN <= 0) or (idx < MAX_UNDERSTANDING_PER_RUN)
        # Video title (when present) lets the LLM separate product feedback from
        # video reactions ("great video!" vs "great display").
        _src_title = ""
        if idx < len(kept_meta) and isinstance(kept_meta[idx], dict):
            _src_title = kept_meta[idx].get("source_title") or ""
        # LLM understanding runs for ALL depths (no quick_mode guard). Cost is bounded
        # by MAX_UNDERSTANDING_PER_RUN, not by depth mode.
        if USE_SMART_UNDERSTANDING and understand_review is not None and _within_understanding_cap:
            try:
                _t_u = time.perf_counter()
                understanding = understand_review(text, product=(query or ""), source_title=_src_title)
                _t_understand_total += time.perf_counter() - _t_u
                _n_understand += 1
            except Exception:
                understanding = None

        # a) Language detection + translation
        # Use the smart detector so romanized Hindi isn't mislabeled as English.
        try:
            if detect_language_smart is not None:
                lang = detect_language_smart(text, detect)
            else:
                lang = detect(text)
        except Exception:
            lang = "unknown"

        # Prefer the LLM's language read when it gave one.
        if understanding and understanding.get("language"):
            lang = understanding["language"]

        translated_text: Optional[str] = None
        is_romanized_indic = bool(looks_romanized_indic and looks_romanized_indic(text)) or \
            (isinstance(lang, str) and lang.lower().endswith("-latn"))

        if understanding and understanding.get("english"):
            # Trust the LLM gloss over the hallucination-prone opus-mt model.
            translated_text = understanding["english"]
            # Analyze on the clean English gloss for everything downstream.
            if lang not in ("en", "unknown"):
                text = translated_text
        elif lang not in ("en", "unknown") and not is_romanized_indic:
            # Only run opus-mt on text it can actually handle (real non-Latin
            # scripts). NEVER on romanized Indic text -> that's what produced the
            # "I'm sorry I'm sorry" hallucinations that poisoned the pipeline.
            try:
                toks = tokenizer(text, return_tensors="pt", truncation=True)
                gen  = seq2seq_model.generate(**toks)
                translated_text = tokenizer.decode(gen[0], skip_special_tokens=True)
                text = translated_text
            except Exception:
                translated_text = None
        # else: romanized Indic with no LLM gloss -> leave text as-is, do NOT
        # translate (better to keep the original than to hallucinate).

        # b) Sentiment
        if idx in _precomputed_sentiments:
            sent = _precomputed_sentiments[idx]
        else:
            sent = sentiment_pipe(text)[0]
        # Override with the LLM's sentiment read when confident. The transformer
        # ran on possibly-misread text; the LLM judged actual intent.
        # NOTE: the actual stars<->category reconciliation happens in (d.1) below,
        # AFTER the category is settled, so the two can never contradict. Here we
        # only take the LLM stars when clearly confident.
        if understanding and understanding.get("stars_label") and understanding.get("confidence", 0) >= 0.45:
            sent = {
                "label": understanding["stars_label"],
                "score": float(understanding.get("confidence", 0.7)),
            }
        # Remember whether the surviving stars came from the transformer (not the
        # LLM). Transformer stars on short/ambiguous English are unreliable and
        # must NEVER be shown next to a contradicting LLM category (the
        # "5 stars on a Complaint" bug). We reconcile them in (d.1).
        _stars_from_llm = bool(
            understanding and understanding.get("stars_label")
            and understanding.get("confidence", 0) >= 0.45
        )

        # b.1) Emotion (real classifier; falls back to neutral if disabled/failed)
        if idx in _precomputed_emotions:
            emotion_obj = _precomputed_emotions[idx]
        else:
            emotion_obj = _classify_emotion(text)
        # Prefer the LLM's emotion read for code-mixed text, where the English-only
        # emotion model is unreliable. Build the same {label, score, all} shape.
        if understanding and understanding.get("emotion"):
            llm_emo = understanding["emotion"]
            llm_conf = float(understanding.get("confidence", 0.6))
            # Always trust the LLM emotion for non-English / romanized text; for
            # English, only override when the local model returned neutral/empty.
            non_english = (lang not in ("en", "unknown"))
            local_weak = (not emotion_obj.get("label")) or emotion_obj.get("label") == "neutral" or float(emotion_obj.get("score") or 0) < 0.4
            if non_english or local_weak:
                emotion_obj = {
                    "label": llm_emo,
                    "score": round(llm_conf, 4),
                    "all": {llm_emo: round(llm_conf, 4)},
                }

        # c) Phrase extraction — Praise/Complaint/Suggestion/Prediction
        # Uses the unified LLM client (Ollama → OpenAI → heuristic). When no LLM is
        # available, the empty `classification` dict triggers the star-based fallback below.
        classification = {c: [] for c in ("Praise","Complaint","Suggestion","Prediction")}
        if not SKIP_PHRASE_EXTRACTION and idx < MAX_GPT_PER_REVIEW:
            llm_backend = llm_client.available_backend()
            if llm_backend != "none":
                prompt = (
                    "Extract exact sub-phrases for Praise, Complaint, Suggestion, and Prediction from this review.\n"
                    "Return a raw JSON mapping each category to a list of short, actionable phrases (no single-word junk).\n"
                    "Keep phrases <=12 words, lowercase is fine. Empty lists are OK.\n"
                    "Example: {\"Praise\":[\"great battery life\"],\"Complaint\":[\"phantom braking on highway\"],\"Suggestion\":[\"add a heads-up display\"],\"Prediction\":[]}\n"
                    f"Review: \"{text}\""
                )
                parsed = llm_client.chat_json(
                    [{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=300,
                )
                if isinstance(parsed, dict):
                    for k in classification.keys():
                        arr = parsed.get(k, [])
                        if isinstance(arr, list):
                            cleaned = [p for p in (_normalize_reason(str(x)) for x in arr) if p]
                            classification[k] = _dedupe_keep_order(cleaned)[:4]

        # d) Category scores
        counts = {k: len(v) for k, v in classification.items()}
        total  = sum(counts.values())
        if total > 0:
            scores   = {k: round(counts[k] / total, 3) for k in counts}
            category = max(scores, key=scores.get)
        else:
            lbl, sc = sent.get("label"), float(sent.get("score", 0))
            if lbl in ("1 star", "2 stars"):
                category = "Complaint"
            elif lbl in ("4 stars", "5 stars"):
                category = "Praise"
            else:
                category = "Complaint" if sc < 0.4 else ("Praise" if sc > 0.6 else "Neutral")
            scores = {k: 0.0 for k in ("Praise","Complaint","Suggestion","Prediction")}
            scores[category] = 1.0

        # d.1) LLM category OVERRIDE (highest authority).
        # The LLM read the writer's actual intent across languages, so its category
        # call beats both the phrase-count heuristic and the star-based fallback.
        # This is what fixes "praise tagged as complaint" on Hinglish reviews.
        if understanding and understanding.get("category") and understanding.get("confidence", 0) >= 0.5:
            category = understanding["category"]
            # Reflect the decision in the score vector so downstream weighting agrees.
            scores = {k: 0.0 for k in ("Praise", "Complaint", "Suggestion", "Prediction")}
            if category in scores:
                scores[category] = round(float(understanding.get("confidence", 1.0)), 3)

        # d.2) STARS<->CATEGORY RECONCILIATION (single source of truth).
        # The cardinal rule of "honest by default": the star rating and the
        # category must NEVER contradict each other. A review can't be a
        # "Complaint" while showing 5 stars. This block guarantees consistency.
        #
        # The bug this fixes: the transformer (nlptown) returns "5 stars" for
        # almost any short/ambiguous English fragment. When the LLM then tagged
        # the review "Complaint" via the category path above, the transformer's
        # bogus 5-star label survived in `sent` -> every complaint rendered 5
        # stars while the overall average was 2.1. Self-contradiction.
        #
        # Policy:
        #   - If the stars currently come from the transformer (not the LLM),
        #     they are untrustworthy. Derive stars FROM the settled category.
        #   - Even if stars came from the LLM, if they blatantly disagree with
        #     the category (e.g. Complaint but >=4 stars), pull them into the
        #     category's valid range so the UI is never self-contradictory.
        _cat_to_stars = {
            "Praise": "5 stars",
            "Complaint": "2 stars",
            "Suggestion": "3 stars",
            "Prediction": "3 stars",
            "Neutral": "3 stars",
        }
        _cat_now = _canon_cat(category)
        _cur_star_num = STAR_TO_NUM.get(sent.get("label"), None)

        def _stars_contradict_category(cat: str, star_num: Optional[int]) -> bool:
            if star_num is None:
                return True
            if cat == "Complaint":
                return star_num >= 4      # a complaint can't be 4-5 stars
            if cat == "Praise":
                return star_num <= 2      # praise can't be 1-2 stars
            if cat in ("Suggestion", "Prediction", "Neutral"):
                return star_num <= 1 or star_num >= 5  # these sit in the middle
            return False

        # TOTAL reconciliation policy (no gaps):
        #   1) Keep the stars ONLY when they came from a confident LLM read AND
        #      they agree with the settled category. This is the one trusted path.
        #   2) In EVERY other case — transformer stars, low-confidence LLM stars,
        #      missing stars, OR any star/category contradiction — derive the
        #      stars FROM the category. The category is the higher-confidence
        #      signal (LLM intent or phrase-count majority), so deriving from it
        #      guarantees the pair can NEVER contradict on screen.
        #
        # Why this is stricter than before: the previous version had an implicit
        # "else: do nothing" branch that let *non-contradicting* transformer
        # stars survive (e.g. a junk fragment scored "5 stars" by nlptown landing
        # in a Praise bucket). Those uniform 5-star artifacts are exactly what
        # produced "every review shows ★★★★★". We now refuse to display ANY
        # star value we didn't either (a) get confidently from the LLM in
        # agreement with the category, or (b) derive from the category itself.
        _trust_llm_stars = (
            _stars_from_llm
            and _cur_star_num is not None
            and not _stars_contradict_category(_cat_now, _cur_star_num)
        )
        if not _trust_llm_stars:
            reconciled = _cat_to_stars.get(_cat_now, "3 stars")
            sent = {
                "label": reconciled,
                # keep a modest confidence — this is a derived, not measured, value
                "score": float(sent.get("score", 0.6) or 0.6),
            }
            sent["_reconciled_from_category"] = True

        # e) Zero-shot topics (stricter threshold)
        if idx in _precomputed_zeroshot:
            zs = _precomputed_zeroshot[idx]
        else:
            _t_z = time.perf_counter()
            zs = zero_shot(text, CANDIDATE_LABELS, multi_label=True)
            _t_zsfb_total += time.perf_counter() - _t_z
            _n_zsfb += 1
        topic_labels = [label for label, score in zip(zs["labels"], zs["scores"]) if score >= ZS_THRESHOLD]
        if not topic_labels:
            topic_labels = ["Other"]

        # f) Keyphrases (MMR to reduce redundancy) + normalize
        raw_kps = [kw for kw, _ in kw_model.extract_keywords(
            text, keyphrase_ngram_range=(1, 2), stop_words="english", top_n=5, use_mmr=True, diversity=0.6
        )]
        keyphrases = [p for p in (_normalize_reason(k) for k in raw_kps) if p]

        # g) NER (works; with blank('en') there will simply be no ents)
        ents = []
        if hasattr(nlp, "pipe"):
            try:
                doc = nlp(text)
                ents = [{"text": ent.text, "label": ent.label_} for ent in getattr(doc, "ents", [])]
            except Exception:
                ents = []

        # h) Expectations
        expectations = re.findall(r"\b(?:wish|hope|should|could)\b.*?[.?!]", text, flags=re.IGNORECASE)

        # i) Quality score (blended with sentiment confidence)
        try:
            q = quality_score(raw, sentiment_confidence=float(sent.get("score", 0) or 0))
        except Exception:
            q = 0.5

        # j) Attach optional per-review metadata (timestamps/score/author/platform)
        m = kept_meta[idx] if idx < len(kept_meta) else {}
        if not isinstance(m, dict):
            m = {}

        results.append({
            "original": raw,
            "translated_text": translated_text,
            "language": lang,
            "sentiment": sent.get("label"),
            "sentiment_score": round(float(sent.get("score", 0)), 3),
            "emotion": emotion_obj.get("label"),
            "emotion_score": emotion_obj.get("score", 0.0),
            "emotion_all": emotion_obj.get("all", {}),
            "quality": q,
            "is_shallow": is_shallow(raw),
            "classification": classification,
            "classification_scores": scores,
            "review_category": category,
            "topic_labels": topic_labels,
            "keyphrases": keyphrases,
            "entities": ents,
            "expectations": expectations,
            # Smart-understanding signals (None when no LLM / low confidence).
            # `understanding_reason` is a clean English phrase used to seed cluster
            # naming so we never again get garbage labels from hallucinated text.
            "understanding_reason": (understanding or {}).get("reason") or None,
            "polarity": (understanding or {}).get("polarity"),
            "is_relevant": (understanding or {}).get("is_relevant", True),
            "is_sarcastic_llm": (understanding or {}).get("is_sarcastic", False),
            "understanding_source": (understanding or {}).get("source") or None,
            # Metadata fields surface so the frontend can build time-series + provenance
            "published_at": m.get("published_at"),
            "author": m.get("author"),
            "score": m.get("score"),
            "like_count": m.get("like_count"),
            "platform": m.get("platform"),
            "source_id": m.get("video_id") or m.get("post_id"),
            "subreddit": m.get("subreddit"),
            # Advanced layers — filled in batch below
            "sarcasm": None,
            "buyer_intent": None,
            "intent_compared_to": None,
        })
        texts_for_clustering.append(text)

    # ---- Advanced per-review layers: sarcasm + buyer intent (batched) -----
    # These run AFTER the main per-review loop so we can batch the calls cheaply.
    if results:
        try:
            texts_for_advanced = [r.get("translated_text") or r.get("original") or "" for r in results]
        except Exception:
            texts_for_advanced = []

        # Sarcasm batch — only runs the transformer when prefilter triggers,
        # so this is near-free for clean datasets.
        if detect_sarcasm_batch and texts_for_advanced:
            try:
                sarcasm_results = detect_sarcasm_batch(texts_for_advanced)
                for r, sr in zip(results, sarcasm_results):
                    if sr and sr.get("is_sarcastic"):
                        r["sarcasm"] = sr
                        # If high-confidence sarcasm, adjust the stored sentiment so
                        # downstream aggregates aren't fooled by "GREAT idea" praise.
                        if adjust_for_sarcasm and sr.get("adjustment") in ("flip", "soften"):
                            original_star = STAR_TO_NUM.get(r.get("sentiment"), 3)
                            new_star = adjust_for_sarcasm(original_star, sr)
                            if new_star != original_star:
                                # Reverse map back to label string
                                inv = {1: "1 star", 2: "2 stars", 3: "3 stars", 4: "4 stars", 5: "5 stars"}
                                r["sentiment_pre_sarcasm"] = r.get("sentiment")
                                r["sentiment"] = inv.get(new_star, r.get("sentiment"))
            except Exception:
                pass

        # Buyer intent batch — pure regex, very fast
        if classify_intent_batch and texts_for_advanced:
            try:
                intents = classify_intent_batch(texts_for_advanced)
                for r, it in zip(results, intents):
                    if not it:
                        continue
                    label = it.get("intent") or "UNKNOWN"
                    r["buyer_intent"] = label
                    if it.get("compared_to"):
                        r["intent_compared_to"] = it["compared_to"]
            except Exception:
                pass


    _stage_log.info("[timing] per_review_loop: %.1fs | understand(LLM)=%.1fs n=%d | zeroshot_fallback(CPU)=%.1fs n=%d",
                    time.perf_counter() - _t_loop0, _t_understand_total, _n_understand,
                    _t_zsfb_total, _n_zsfb)

    # ---- Reviewer credibility scoring (pure computation, no LLM) ----
    # Runs after the per-review loop (so text/platform/emotion signals are settled)
    # and BEFORE clustering/overview. Attaches `_credibility` to each review and
    # rolls up credibility-weighted sentiment metrics for the overview. Fail-open.
    credibility_metrics: Optional[Dict[str, Any]] = None
    if score_credibility is not None:
        try:
            for review in results:
                review["_credibility"] = score_credibility(review, results)
            if compute_weighted_metrics is not None:
                credibility_metrics = compute_weighted_metrics(results)
        except Exception as _e:
            _stage_log.warning("[credibility] skipped (%s)", _e)
            credibility_metrics = None

    # ---- Canonical clustering (preferred) or fallback to BERTopic ----------
    topics_debug: Optional[Dict[str, Any]] = None
    canonical_blocks: List[Dict[str, Any]] = []  # for overview

    if texts_for_clustering:
        if canonical_clusters and ClusterConfig:
            # ===== Preferred: universal canonical clustering =====
            cc = canonical_clusters(texts_for_clustering, embedder, ClusterConfig())
            labels = cc.get("labels", [])
            clusters = cc.get("clusters", [])
            topics_debug = {"mode": "canonical", **(cc.get("debug") or {})}

            # Map label -> cluster meta
            label_to_meta = {c["id"]: c for c in clusters}

            # Attach cluster data to each review
            total_docs = len(texts_for_clustering)
            for i, res in enumerate(results):
                cid = int(labels[i]) if i < len(labels) else 0
                res["cluster_id"] = cid
                meta = label_to_meta.get(cid)
                if meta:
                    # humanize the cluster reason once more if possible
                    pretty_reason = _normalize_reason(meta.get("canonical_reason") or "") or (meta.get("canonical_reason") or "")
                    res["canonical_reason"] = pretty_reason
                    # For back-compat theme list
                    res["cluster_topic"] = [pretty_reason] if pretty_reason else []
                    res["cluster_score"] = float(meta.get("centroid_sim_mean") or 0.0)
                else:
                    res["canonical_reason"] = ""
                    res["cluster_topic"] = []
                    res["cluster_score"] = 0.0

            # Build top canonical clusters summary
            for meta in clusters:
                size = int(meta.get("size", 0))
                share = round((size / max(1, total_docs)) * 100.0, 1)
                reason_raw = meta.get("canonical_reason") or "General issue / suggestion"
                reason_pretty = _normalize_reason(reason_raw) or reason_raw
                quotes = meta.get("quotes", [])[:2]
                canonical_blocks.append({
                    "cluster_id": int(meta.get("id")),
                    "reason": reason_pretty,
                    "count": size,
                    "share_%": share,
                    "support": float(meta.get("support", 0.0)),
                    "centroid_sim_mean": float(meta.get("centroid_sim_mean", 0.0)),
                    "quotes": quotes,
                })

            # Sort: bigger first, then cohesion
            canonical_blocks.sort(key=lambda d: (-d["count"], -d["centroid_sim_mean"]))

            # ------ Clean cluster names from LLM reasons (NEW) ------
            # The embedding-derived `canonical_reason` can be noisy/garbage when the
            # input text was code-mixed (this produced labels like "Sorry, Gulab,
            # Jamun"). When we have clean per-review LLM `understanding_reason`
            # phrases, pick the most representative one PER cluster as the label,
            # and split mixed clusters by dominant category so a praise-heavy and a
            # complaint-heavy bucket don't share one misleading name.
            try:
                reasons_by_cluster: Dict[int, Counter] = {}
                cats_by_cluster: Dict[int, Counter] = {}
                for r in results:
                    cid = int(r.get("cluster_id", 0))
                    ur = (r.get("understanding_reason") or "").strip()
                    if ur:
                        reasons_by_cluster.setdefault(cid, Counter())[ur] += 1
                    cat = _canon_cat(r.get("review_category"))
                    cats_by_cluster.setdefault(cid, Counter())[cat] += 1

                # Vague/tone-based reasons must not become cluster names. Reuse the
                # same blocklist as cluster.py's _canonical_phrase so this override
                # path can't reintroduce labels like "Mixed devices mentioned, ...".
                _VAGUE_WORDS = ("mixed", "various", "general", "unclear",
                                "miscellaneous", "diverse", "multiple")
                def _reason_is_vague(s: str) -> bool:
                    return any(w in (s or "").lower() for w in _VAGUE_WORDS)

                for cb in canonical_blocks:
                    cid = int(cb["cluster_id"])
                    rc = reasons_by_cluster.get(cid)
                    if rc:
                        # Most common clean, NON-vague reason becomes the human label.
                        # If every candidate is vague, keep the guarded embedding label
                        # (cb["reason"], already vetted by _canonical_phrase).
                        best_reason = next(
                            (r for r, _ in rc.most_common() if not _reason_is_vague(r)),
                            None,
                        )
                        if best_reason:
                            pretty = _normalize_reason(best_reason) or best_reason
                            if pretty and not _reason_is_vague(pretty):
                                cb["reason"] = pretty
                    # Tag the cluster's dominant category + praise/complaint shares so
                    # the UI can separate "what customers love" from "top complaints"
                    # instead of dumping praise clusters into the complaints list.
                    cc = cats_by_cluster.get(cid)
                    if cc:
                        _tot = sum(cc.values()) or 1
                        cb["dominant_category"] = cc.most_common(1)[0][0]
                        cb["complaint_share"] = round(cc.get("Complaint", 0) / _tot, 3)
                        cb["praise_share"] = round(cc.get("Praise", 0) / _tot, 3)

                # Propagate the cleaned reason back onto each review's canonical_reason
                cid_to_reason = {int(cb["cluster_id"]): cb.get("reason", "") for cb in canonical_blocks}
                for r in results:
                    cid = int(r.get("cluster_id", 0))
                    if cid in cid_to_reason and cid_to_reason[cid]:
                        r["canonical_reason"] = cid_to_reason[cid]
                        r["cluster_topic"] = [cid_to_reason[cid]]
            except Exception:
                pass

            # ------ Attach solutions to canonical clusters (NEW) ------
            if generate_solutions and ClusterInput and canonical_blocks:
                # Intent counts per cluster
                intent_by_cid: Dict[int, Dict[str, int]] = {}
                for r in results:
                    cid = int(r.get("cluster_id", 0))
                    cat = str(r.get("review_category", "Neutral"))
                    if cid not in intent_by_cid:
                        intent_by_cid[cid] = {"Praise":0,"Complaint":0,"Suggestion":0,"Prediction":0,"Neutral":0}
                    if cat not in intent_by_cid[cid]:
                        intent_by_cid[cid][cat] = 0
                    intent_by_cid[cid][cat] += 1

                # Build inputs (NOW passes size to feed solution confidence)
                cluster_inputs = []
                for cb in canonical_blocks:
                    cid = int(cb["cluster_id"])
                    cluster_inputs.append(ClusterInput(
                        cluster_id=cid,
                        reason=str(cb.get("reason", "")),
                        quotes=list(cb.get("quotes", []) or []),
                        support=float(cb.get("support", 0.0)),
                        centroid_sim_mean=float(cb.get("centroid_sim_mean", 0.0)),
                        intent_counts=intent_by_cid.get(
                            cid,
                            {"Praise":0,"Complaint":0,"Suggestion":0,"Prediction":0,"Neutral":0}
                        ),
                        size=int(cb.get("count", 0))  # <-- new
                    ))

                sols = generate_solutions(
                    cluster_inputs,
                    query=(query or None),
                    product_context=(product_context or None),
                    embedder=embedder,
                    openai_client=client,
                    rag_docs_dirs=RAG_DOCS_DIRS or None
                )

                # Attach
                for cb, sol in zip(canonical_blocks, sols):
                    cb["solution"] = sol

            # ------ Severity scoring on each cluster (NEW) ------
            if score_cluster_severity:
                for cb in canonical_blocks:
                    try:
                        sev = score_cluster_severity(cb.get("reason", ""), cb.get("quotes", []) or [])
                        cb["severity"] = sev
                    except Exception:
                        pass

        else:
            # ===== Fallback: BERTopic themes (legacy) =====
            embeddings = embedder.encode(texts_for_clustering, convert_to_numpy=True)
            fitted_tm, topics, probs, topics_debug = _fit_topics_safe(topic_model, texts_for_clustering, embeddings)
            counts = Counter(topics)
            for i, res in enumerate(results):
                res["cluster_id"] = int(topics[i])
                if fitted_tm is not None:
                    try:
                        topic_words = fitted_tm.get_topic(topics[i]) or []
                        # normalize the top words into a readable label
                        label = _normalize_reason(" / ".join([w for w, _ in topic_words][:5])) or "General"
                        res["cluster_topic"] = [label] if label else []
                        score = probs[i][topics[i]] if isinstance(probs[i], (list, tuple)) else 1.0
                        res["cluster_score"] = float(score)
                    except Exception:
                        res["cluster_topic"] = []
                        res["cluster_score"] = 1.0
                else:
                    res["cluster_topic"] = []
                    res["cluster_score"] = 1.0
            # Legacy cluster summary
            for cid, cnt in counts.items():
                label = next((r["cluster_topic"] for r in results if r["cluster_id"] == cid), [])
                pretty = _normalize_reason(" / ".join(label)) or (" / ".join(label) if label else "General")
                canonical_blocks.append({
                    "cluster_id": int(cid),
                    "reason": pretty,
                    "count": int(cnt),
                    "share_%": round((cnt / max(1, len(texts_for_clustering))) * 100.0, 1),
                    "support": 0.0,
                    "centroid_sim_mean": 0.0,
                    "quotes": []
                })
    else:
        # No texts (shouldn't happen after keep), but keep shape
        for res in results:
            res["cluster_id"]    = 0
            res["cluster_topic"] = []
            res["cluster_score"] = 0.0
        topics_debug = {"mode": "no_texts"}

    # ---- Optional: generate cluster-level suggestions (NEW) ----
    cluster_suggestions: List[Dict[str, Any]] = []
    if suggestions_for_clusters and canonical_blocks:
        try:
            cfg = SuggestionConfig() if SuggestionConfig else None
            cluster_suggestions = suggestions_for_clusters(canonical_blocks, cfg=cfg)
        except Exception:
            cluster_suggestions = []

    # ---- Overview
    df = pd.DataFrame(results) if results else pd.DataFrame()
    average_stars = _avg_star(results) if results else None
    top_phrases = (
        df["keyphrases"].explode().value_counts().head(5).index.tolist()
        if not df.empty else []
    )
    star_breakdown = _collect_star_breakdown(results)
    mood = _mood_index(results)

    # Back-compat “clusters” summary (legacy)
    cluster_summary: List[Dict[str, Any]] = []
    if not df.empty and "cluster_id" in df:
        counts = df["cluster_id"].value_counts().to_dict()
        for cid, cnt in counts.items():
            theme = next((r.get("cluster_topic", []) for r in results if r.get("cluster_id") == cid), [])
            cluster_summary.append({"theme": theme, "count": int(cnt)})

    # ---- Executive Summary (reasons + aspects)
    executive = _aggregate_reasons(results)

    # ---- Action items (LLM-grounded when available; heuristic when not)
    action_items: List[Dict[str, Any]] = []
    if not SKIP_ACTION_ITEMS and executive.get("top_aspects"):
        llm_backend = llm_client.available_backend()
        if llm_backend != "none":
            ctx_block = f"PRODUCT CONTEXT (interpret every theme in THIS domain):\n{product_context}\n\n" if product_context else ""
            prompt_insights = (
                "You are a product-strategy assistant. Given these feedback clusters and aspect summaries, "
                "produce 3 prioritized improvement actions per theme.\n\n"
                f"{ctx_block}"
                f"Clusters: {canonical_blocks or cluster_summary}\n"
                f"Aspects: {executive.get('top_aspects')}\n\n"
                "Keep every action realistic for THIS product's domain — never use software jargon "
                "(latency, caching, feature flags) unless the product is clearly software.\n"
                "Return raw JSON like: {\"actions\": [{\"theme\": \"Battery\", \"priority\": 1, \"item\": \"...\"}, ...]}"
            )
            parsed = llm_client.chat_json(
                [
                    {"role": "system", "content": "You generate prioritized product action items per theme."},
                    {"role": "user", "content": prompt_insights},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
                action_items = parsed["actions"]
            else:
                action_items = _heuristic_actions(executive)
        else:
            action_items = _heuristic_actions(executive)

    _overview = _enrich_overview({
            "average_sentiment": average_stars,
            "_query_hint": query,  # private hint for narrator (popped before render)
            "mood_index": mood,
            "stars": star_breakdown,
            "top_keyphrases": top_phrases,
            "clusters": cluster_summary,
            "canonical_clusters": canonical_blocks,
            "cluster_suggestions": cluster_suggestions,
            "customer_wishes": _aggregate_customer_wishes(results),
            "language_distribution": _count_languages(results),
            "emotion_mix": _category_to_emotion_mix(results),
            "sentiment_over_time": _compute_sentiment_over_time(results),
            "astroturf_signals": (detect_astroturf(results) if detect_astroturf else {"flag": False, "summary": "Module unavailable", "suspicious_clusters": [], "repeat_authors": []}),
            "next_version_roadmap": (build_next_version_roadmap(canonical_blocks) if build_next_version_roadmap else []),
            "what_users_love": (what_users_love(results) if what_users_love else []),
            # Aspect-Based Sentiment Analysis (NEW)
            "aspect_sentiment": (analyze_aspects(results, query=query, product_category=product_category) if analyze_aspects else {"domain": None, "aspects": []}),
            # Buyer Intent rollup (NEW)
            "buyer_intent_summary": (aggregate_intents([
                {"intent": r.get("buyer_intent"), "compared_to": r.get("intent_compared_to")} for r in results
            ]) if aggregate_intents else {"distribution": [], "compared_products": [], "decision_health": {}}),
            # Sarcasm prevalence (NEW)
            "sarcasm_stats": {
                "flagged_count": sum(1 for r in results if (r.get("sarcasm") or {}).get("is_sarcastic")),
                "total": len(results),
            },
            # Risk register — critical/safety issues separate from the roadmap
            "risk_register": (build_risk_register(canonical_blocks) if build_risk_register else []),
            # Reviewer Credibility Intelligence — credibility-weighted sentiment (NEW)
            **({"credibility_intelligence": credibility_metrics} if credibility_metrics else {}),
        }, per_review=results, product_context=product_context, quick_mode=quick_mode)

    # NOTE: the Per-Insight Evidence Engine + Intelligence Synthesizer + Smart
    # Summary now run INSIDE _enrich_overview (in that order), so the executive
    # summary can consume the cross-section insights and adaptive brief. They are
    # no longer invoked here. (kept_meta is unused by enrich_with_evidence.)

    # ---- Advanced Intelligence Pack (runs AFTER the self-corrector) ----
    # 1) Competitive intelligence (one LLM call), 2) deal-breaker detection (pure
    # regex, free), 3) purchase advisor (one LLM call — Customer view only). All
    # three fail-open: an error leaves the dashboard rendering without that card.
    try:
        _pi = product_intelligence
        _pi_dict = (_pi.to_dict() if hasattr(_pi, "to_dict")
                    else (_pi if isinstance(_pi, dict) else {}))
        _competitors = list(_pi_dict.get("key_competitors") or [])
        _aspects = list(_pi_dict.get("direct_aspects") or []) \
            + list(_pi_dict.get("ecosystem_aspects") or []) \
            or list(_pi_dict.get("expected_feedback_categories") or [])

        if extract_competitive_intel is not None:
            comp_intel = extract_competitive_intel(results, query, _competitors, _aspects)
            if comp_intel and comp_intel.get("total_comparisons", 0) > 0:
                _overview["competitive_intelligence"] = comp_intel

        if detect_dealbreakers is not None:
            dealbreakers = detect_dealbreakers(results, query, _competitors)
            if dealbreakers and dealbreakers.get("total_dealbreakers", 0) > 0:
                _overview["dealbreakers"] = dealbreakers

        if generate_purchase_advice is not None:
            advice = generate_purchase_advice(_overview, results, query, _pi_dict)
            if advice:
                _overview["purchase_advice"] = advice
    except Exception as _e:
        _stage_log.warning("[advanced_intel] skipped (%s)", _e)

    return {
        "meta": {
            "input_count": len(reviews),
            "kept_count": len(kept_reviews),
            "dropped_count": len(reviews) - len(kept_reviews),
            "dropped_summary": dropped_summary,
            "strictness": used_strictness,
            "terms_used": terms_used[:30],
        },
        "per_review": results,
        "overview": _overview,
        "executive_summary": {
            "totals_by_category": executive.get("totals_by_category", {}),
            "top_reasons_overall": executive.get("top_reasons_overall", {}),
            "top_aspects": executive.get("top_aspects", [])  # [{aspect, mentions, share_of_reviews, by_category:[{category,count,reasons,quotes}]}]
        },
        "action_items": action_items,
        "topics_debug": topics_debug,
        "llm_backend": llm_client.available_backend(),
    }

# -------------------- FastAPI route (thin wrapper) --------------------------
@router.post(
    "/analyze",
    response_model=Dict[str, Any],
    summary="Universal Review Analyzer + Advanced Insights",
    description=(
        "Accepts JSON payload `{ \"reviews\": [\"...\", ...], \"query\": \"optional hint\", \"strictness\": \"ultra|normal|low\" }` and returns:\n"
        "• `meta`: counts + dropped_summary + terms_used + strictness\n"
        "• `per_review`: detailed analysis of each kept review (+ canonical_reason per item)\n"
        "• `overview`: sentiment, keyphrases, legacy clusters, and NEW canonical_clusters (reason/count/share/support/quotes/solution) "
        "plus `cluster_suggestions` derived from canonical clusters\n"
        "• `executive_summary`: totals, reasons by category/aspect, representative quotes\n"
        "• `action_items`: prioritized improvements per theme"
    )
)
def analyze_reviews(payload: ReviewsInput) -> JSONResponse:
    if not isinstance(payload.reviews, list) or not all(isinstance(r, str) for r in payload.reviews):
        raise HTTPException(status_code=400, detail="`reviews` must be a list of strings.")
    try:
        out = analyze_core(payload.reviews, query=payload.query, strictness=payload.strictness, meta=payload.meta)
        return JSONResponse(out)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyzer error: {e}")

@router.get("/analyze/_ping")
def analyze_ping():
    return {"ok": True}

__all__ = [
    "analyze_core",
    "aggregate_reasons",
    "heuristic_actions",
    "router",
]
