# backend/insight/rag/retriever.py
"""
Per-Review RAG Retriever.

When the user asks a question about the report ("what do reviewers say about
battery in cold weather?", "is the carrying case actually cheap?"), this module:

  1. Embeds the question with the same sentence-transformer used at analyze time
  2. Embeds every per-review comment (cached per-report so it only runs once)
  3. Picks the top-K most semantically similar reviews
  4. Returns them with full provenance (platform, author, date, similarity)
  5. The caller (ask.py) feeds those into an LLM prompt and asks for an answer
     WITH inline citation markers like [#3], [#7]

Why this matters:
  - Every answer the user reads is grounded in real reviews they can click
    through to read. No more "trust me" responses from a generic LLM.
  - The dashboard becomes interrogatable. "Why is comfort 2.9 stars?"
    becomes a question with 5 cited answers, not a black-box number.
  - Foundation for Phase B.2 (clickable evidence) and B.3 (comparison view).

Design notes:
  - Embeddings are cached IN-MEMORY keyed by report identity (query_used +
    review_count + a sample hash). For a free-tier project that's fine; we
    don't want a vector DB dependency yet.
  - Falls back gracefully when sentence-transformers isn't loaded (uses
    TF-IDF cosine).
  - Returns SAFE quote-length excerpts (each review trimmed to ~400 chars)
    so prompts stay small even with K=8.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("insightmesh.rag")

try:
    import numpy as np
except Exception:
    np = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except Exception:
    TfidfVectorizer = None
    cosine_similarity = None

# Module-level cache of embedded reports.
# key = sha1(query + len + first-20-original-hashes), value = (texts, vectors, ids)
_EMBED_CACHE: Dict[str, Tuple[List[str], Any, List[int]]] = {}
_CACHE_MAX_REPORTS = 8  # LRU-ish bound


def _report_fingerprint(per_review: List[Dict[str, Any]], product: str) -> str:
    """Stable identifier for a report so we cache its embeddings once."""
    parts = [product or "", str(len(per_review))]
    for r in per_review[:20]:
        s = (r.get("original") or "")[:60]
        parts.append(hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()[:8])
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _evict_if_needed():
    if len(_EMBED_CACHE) > _CACHE_MAX_REPORTS:
        # Drop the oldest by insertion (dict ordering)
        oldest = next(iter(_EMBED_CACHE))
        _EMBED_CACHE.pop(oldest, None)


def _texts_from_reviews(per_review: List[Dict[str, Any]]) -> List[str]:
    """Build the corpus we'll embed. Prefer translated_text (always English) then original."""
    out: List[str] = []
    for r in per_review:
        t = (r.get("translated_text") or r.get("original") or "").strip()
        # Trim aggressively — long quotes don't add semantic signal past ~600 chars
        if len(t) > 600:
            t = t[:600]
        out.append(t)
    return out


def _embed_corpus(texts: List[str], embedder: Any) -> Optional[Any]:
    """Try sentence-transformers first, fall back to TF-IDF. Returns matrix or None."""
    if embedder is not None and np is not None:
        try:
            vecs = embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            return vecs
        except Exception as e:
            log.debug("[rag] sentence-transformer encode failed: %s", e)

    if TfidfVectorizer is None:
        return None
    try:
        vec = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=4000,
            stop_words="english",
            min_df=1,
            max_df=0.95,
            sublinear_tf=True,
        )
        return vec.fit_transform(texts), vec  # tuple signals TF-IDF mode
    except Exception:
        return None


def _embed_query(question: str, corpus_state: Any, embedder: Any) -> Optional[Any]:
    """Embed the user question using the same scheme as the corpus."""
    # TF-IDF mode: corpus_state is (matrix, vectorizer)
    if isinstance(corpus_state, tuple) and len(corpus_state) == 2:
        _matrix, vec = corpus_state
        try:
            return vec.transform([question])
        except Exception:
            return None
    # Sentence-transformer mode
    if embedder is not None and np is not None:
        try:
            return embedder.encode([question], convert_to_numpy=True, normalize_embeddings=True)
        except Exception:
            return None
    return None


def _similarities(query_vec: Any, corpus_state: Any) -> Optional[List[float]]:
    """Cosine sims of one query against N corpus rows."""
    if isinstance(corpus_state, tuple) and len(corpus_state) == 2:
        matrix, _ = corpus_state
        if cosine_similarity is None:
            return None
        sims = cosine_similarity(query_vec, matrix)[0]
        return sims.tolist()
    # Sentence-transformer mode: dot product (already normalized)
    if np is None:
        return None
    try:
        sims = (corpus_state @ query_vec.T).ravel()
        return sims.tolist()
    except Exception:
        return None


def retrieve_evidence(
    *,
    question: str,
    per_review: List[Dict[str, Any]],
    product: str = "",
    embedder: Any = None,
    top_k: int = 6,
    min_similarity: float = 0.10,
) -> List[Dict[str, Any]]:
    """
    Returns the top-K reviews most relevant to `question`, with full provenance.

    Each result dict:
      {
        "rank": 1,                  # 1-indexed; used as citation marker [#1]
        "review_index": 17,         # index into per_review
        "similarity": 0.78,
        "text": "Trimmed quote...",
        "sentiment": "2 stars",
        "category": "Complaint",
        "language": "en",
        "platform": "reddit",
        "author": "user123",
        "published_at": "2025-04-12T...",
        "source_id": "abc123",
        "url": null,               # frontend can synthesize one if needed
      }
    """
    if not question or not per_review:
        return []

    texts = _texts_from_reviews(per_review)
    if not any(texts):
        return []

    # Cache embeddings per report
    fp = _report_fingerprint(per_review, product)
    cached = _EMBED_CACHE.get(fp)
    if cached is None:
        corpus_state = _embed_corpus(texts, embedder)
        if corpus_state is None:
            log.debug("[rag] no embedding backend available")
            return _keyword_fallback(question, per_review, texts, top_k)
        _EMBED_CACHE[fp] = (texts, corpus_state, list(range(len(per_review))))
        _evict_if_needed()
        cached = _EMBED_CACHE[fp]

    _texts, corpus_state, ids = cached
    query_vec = _embed_query(question, corpus_state, embedder)
    if query_vec is None:
        return _keyword_fallback(question, per_review, texts, top_k)

    sims = _similarities(query_vec, corpus_state)
    if not sims:
        return _keyword_fallback(question, per_review, texts, top_k)

    # Rank and pick top-K above the similarity floor
    ranked = sorted(range(len(sims)), key=lambda i: -sims[i])
    out: List[Dict[str, Any]] = []
    for rank_pos, idx in enumerate(ranked):
        if len(out) >= top_k:
            break
        s = sims[idx]
        if s < min_similarity:
            break
        r = per_review[idx]
        snippet = (r.get("translated_text") or r.get("original") or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400].rsplit(" ", 1)[0] + "..."
        out.append({
            "rank": len(out) + 1,
            "review_index": idx,
            "similarity": round(float(s), 3),
            "text": snippet,
            "sentiment": r.get("sentiment"),
            "category": r.get("review_category"),
            "language": r.get("language"),
            "platform": r.get("platform"),
            "author": r.get("author"),
            "published_at": r.get("published_at"),
            "source_id": r.get("source_id"),
        })
    return out


def _keyword_fallback(
    question: str,
    per_review: List[Dict[str, Any]],
    texts: List[str],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Last-resort keyword-overlap ranker when no embeddings are available."""
    q_tokens = set(re.findall(r"[a-z]{3,}", (question or "").lower()))
    if not q_tokens:
        return []
    scored = []
    for i, t in enumerate(texts):
        toks = set(re.findall(r"[a-z]{3,}", t.lower()))
        if not toks:
            continue
        score = len(q_tokens & toks) / max(1, len(q_tokens))
        if score > 0:
            scored.append((i, score))
    scored.sort(key=lambda x: -x[1])
    out: List[Dict[str, Any]] = []
    for i, score in scored[:top_k]:
        r = per_review[i]
        snippet = (r.get("translated_text") or r.get("original") or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400].rsplit(" ", 1)[0] + "..."
        out.append({
            "rank": len(out) + 1,
            "review_index": i,
            "similarity": round(score, 3),
            "text": snippet,
            "sentiment": r.get("sentiment"),
            "category": r.get("review_category"),
            "language": r.get("language"),
            "platform": r.get("platform"),
            "author": r.get("author"),
            "published_at": r.get("published_at"),
            "source_id": r.get("source_id"),
        })
    return out


def build_evidence_block(evidence: List[Dict[str, Any]]) -> str:
    """Render an evidence list as a numbered prompt section the LLM can cite from."""
    if not evidence:
        return "(no relevant reviews found)"
    lines = []
    for e in evidence:
        meta_bits = []
        if e.get("sentiment"):
            meta_bits.append(e["sentiment"])
        if e.get("platform"):
            meta_bits.append(e["platform"])
        meta = " · ".join(meta_bits) if meta_bits else ""
        prefix = f"[#{e['rank']}]"
        suffix = f"  ({meta})" if meta else ""
        lines.append(f"{prefix} {e['text']}{suffix}")
    return "\n".join(lines)
