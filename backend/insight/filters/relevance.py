# backend/insight/filters/relevance.py
from __future__ import annotations
import os, re, math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Iterable, Any
from collections import Counter

# Optional deps (graceful fallbacks)
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except Exception:
    TfidfVectorizer = None

try:
    from langdetect import detect as _lang_detect
except Exception:
    _lang_detect = None

try:
    import numpy as np
except Exception:
    np = None

# --------- Config & Results ---------
@dataclass
class RelevanceConfig:
    strictness: str = os.getenv("STRICTNESS", "ultra")  # ultra|normal|low
    min_tokens: int = 6
    max_nonalpha_ratio: float = 0.6
    min_semantic: float = 0.60   # cosine (only if embedder provided)
    min_lexical: float = 0.35    # jaccard-ish of n-grams
    require_semantic_or_lexical: bool = True
    allow_personal_if_product_terms: bool = False

    @staticmethod
    def for_strictness(level: str) -> "RelevanceConfig":
        level = (level or "ultra").lower()
        if level == "ultra":
            return RelevanceConfig(strictness="ultra", min_tokens=6, max_nonalpha_ratio=0.55,
                                   min_semantic=0.62, min_lexical=0.38, require_semantic_or_lexical=True)
        if level == "normal":
            return RelevanceConfig(strictness="normal", min_tokens=5, max_nonalpha_ratio=0.65,
                                   min_semantic=0.56, min_lexical=0.30, require_semantic_or_lexical=True)
        # low
        return RelevanceConfig(strictness="low", min_tokens=4, max_nonalpha_ratio=0.7,
                               min_semantic=0.48, min_lexical=0.24, require_semantic_or_lexical=False)

@dataclass
class RelevanceResult:
    keep: bool
    reason: str
    semantic: Optional[float]
    lexical: float
    lang: Optional[str]
    flags: Dict[str, bool]

# --------- Text utils ---------
_WS_RE = re.compile(r"\s+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

def normalize_text(t: str) -> str:
    return _WS_RE.sub(" ", (t or "").strip())

def tokenize(t: str) -> List[str]:
    t = normalize_text(t).lower()
    toks = re.findall(r"[a-zA-Z0-9#@][a-zA-Z0-9_\-']+", t)
    return toks

def token_ratio_nonalpha(t: str) -> float:
    if not t: return 1.0
    total = len(t)
    alnum = len(_ALNUM_RE.findall(t))
    return 1.0 - (alnum / max(1, total))

def detect_language(t: str) -> Optional[str]:
    if not t or not _lang_detect: return None
    try:
        return _lang_detect(t)
    except Exception:
        return None

# --------- Auto-lexicon (universal) ---------
def build_auto_lexicon(query: str, comments: List[str], top_k_terms: int = 30) -> List[str]:
    """
    Universal: derive domain terms from the query and the comment corpus.
    - No product hard-coding.
    - Uses TF-IDF n-grams if sklearn available, else frequency heuristics.
    """
    seeds: List[str] = []
    q = (query or "").lower().strip()
    if q:
        seeds.extend([w for w in tokenize(q) if len(w) > 2])

    corpus = [normalize_text(c).lower() for c in comments if c and len(c) >= 8]
    if not corpus:
        return seeds[:top_k_terms]

    if TfidfVectorizer is not None:
        try:
            vec = TfidfVectorizer(ngram_range=(1,3), min_df=2, max_features=2000)
            X = vec.fit_transform(corpus)
            # Use average TF-IDF per term to pick salient features
            means = X.mean(axis=0)
            means = means.A1 if hasattr(means, "A1") else means
            terms = vec.get_feature_names_out()
            ranked = sorted(zip(terms, means), key=lambda x: x[1], reverse=True)
            terms_top = [t for t, _ in ranked[:top_k_terms]]
            # Filter out super-generic words
            terms_top = [t for t in terms_top if not re.fullmatch(r"(the|and|for|with|that|have|this|from|your|you|are|was|were|has|had|can|could|would|should)", t)]
            return list(dict.fromkeys(seeds + terms_top))[:top_k_terms]
        except Exception:
            pass

    # Fallback: frequency of n-grams (very light)
    counts = Counter()
    for doc in corpus:
        toks = tokenize(doc)
        for n in (1,2,3):
            for i in range(len(toks)-n+1):
                ng = " ".join(toks[i:i+n])
                counts[ng] += 1
    commons = [k for k, v in counts.most_common(200) if v >= 2]
    commons = [k for k in commons if not re.fullmatch(r"(the|and|for|with|that|have|this|from|your|you|are|was|were|has|had|can|could|would|should)", k)]
    return list(dict.fromkeys(seeds + commons))[:top_k_terms]

# --------- Scoring ---------
def lexical_overlap_score(text: str, terms: List[str]) -> float:
    if not text or not terms:
        return 0.0
    t = normalize_text(text).lower()
    # Exact phrase hits (weighted)
    phrase_hits = sum(1 for ph in terms if len(ph) > 3 and ph in t)
    # Token Jaccard
    s_text = set(tokenize(t))
    s_terms = set([tok for ph in terms for tok in tokenize(ph)])
    jacc = len(s_text & s_terms) / max(1, len(s_text | s_terms))
    return min(1.0, 0.15 * phrase_hits + jacc)

def semantic_similarity(embedder: Any, text: str, query: str) -> Optional[float]:
    if embedder is None or np is None:
        return None
    try:
        vecs = embedder.encode([text, f"This message discusses features, issues, or suggestions about: {query}"],
                               convert_to_numpy=True, normalize_embeddings=True)
        a, b = vecs[0], vecs[1]
        return float(np.dot(a, b))
    except Exception:
        return None

# --------- Noise flags ---------
PERSONAL_PAT = re.compile(r"\b(i think|i feel|my opinion|elon|ceo|politics|meme|lol|lmao|haha|bro|dude|ngl|idgaf|tbh)\b", re.I)

def noise_flags(text: str) -> Dict[str, bool]:
    t = (text or "")
    return {
        "maybe_personal": bool(PERSONAL_PAT.search(t)),
        "short": len(tokenize(t)) < 6,
        "high_nonalpha": token_ratio_nonalpha(t) > 0.65,
    }

# --------- Decision ---------
def product_relevance(
    text: str,
    query: str,
    lexicon_terms: List[str],
    embedder: Any = None,
    config: Optional[RelevanceConfig] = None
) -> RelevanceResult:
    cfg = config or RelevanceConfig.for_strictness(os.getenv("STRICTNESS","ultra"))
    t = normalize_text(text)
    lang = detect_language(t)
    flags = noise_flags(t)

    if len(tokenize(t)) < cfg.min_tokens:
        return RelevanceResult(False, "too_short", None, 0.0, lang, flags)

    if token_ratio_nonalpha(t) > cfg.max_nonalpha_ratio:
        return RelevanceResult(False, "too_noisy", None, 0.0, lang, flags)

    lex = lexical_overlap_score(t, lexicon_terms)
    sem = semantic_similarity(embedder, t, query)

    # Personal/meme: drop unless product terms present
    if flags["maybe_personal"] and lex < max(0.25, cfg.min_lexical * 0.8):
        return RelevanceResult(False, "personal_or_meme", sem, lex, lang, flags)

    decisions: List[Tuple[bool, str]] = []

    if sem is not None:
        decisions.append((sem >= cfg.min_semantic, f"sem:{sem:.2f}>={cfg.min_semantic:.2f}"))
    decisions.append((lex >= cfg.min_lexical, f"lex:{lex:.2f}>={cfg.min_lexical:.2f}"))

    if cfg.require_semantic_or_lexical:
        keep = any(ok for ok, _ in decisions)
        reason = " OR ".join([r for ok, r in decisions if ok]) or "below_thresholds"
    else:
        # low strictness: allow pass unless clearly junk
        keep = (sem or 0) >= (cfg.min_semantic - 0.08) or lex >= (cfg.min_lexical - 0.1)
        reason = "lenient_pass" if keep else "below_thresholds"

    return RelevanceResult(keep, reason, sem, lex, lang, flags)

# --------- Batch filter ---------
def filter_comments(
    comments: List[str],
    query: str,
    embedder: Any = None,
    config: Optional[RelevanceConfig] = None
) -> Tuple[List[int], Dict[str, int], List[str]]:
    """
    Returns:
      - kept_indices: indices in original list to keep
      - dropped_summary: counts by reason
      - terms_used: auto-lexicon terms that were used
    """
    cfg = config or RelevanceConfig.for_strictness(os.getenv("STRICTNESS","ultra"))
    lexicon_terms = build_auto_lexicon(query, comments)
    kept, dropped = [], Counter()
    for i, c in enumerate(comments):
        res = product_relevance(c or "", query, lexicon_terms, embedder, cfg)
        if res.keep:
            kept.append(i)
        else:
            dropped[res.reason] += 1
    return kept, dict(dropped), lexicon_terms
