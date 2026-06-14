"""
EvidenceStore — retrieval layer for the debate engine.

WHY
---
The old debate dumped <=24 raw comment strings into one prompt. That doesn't
scale (breaks past a few dozen reviews) and ignores everything the analyzer
already computed (category, aspect, severity, sarcasm, intent). This store
embeds EVERY relevant review together with its signals, then lets each debate
agent RETRIEVE the evidence most relevant to its stance — real RAG, grounded in
the full analysis, universal across products.

DESIGN
------
- Backend-agnostic. v1 uses an in-memory numpy cosine index (zero new deps:
  the app already ships sentence-transformers + numpy). The public interface
  (`add_reviews`, `retrieve`) is intentionally small so swapping in
  Chroma/FAISS/Pinecone later is a single-file change when million-scale matters.
- Embedder is INJECTED (any callable: List[str] -> array (n,d)). In production we
  pass the analyzer's existing `embedder.encode`. In tests we pass a stub. No
  hard import of sentence-transformers here, so this module never breaks import.
- Each item keeps rich metadata so retrieval can be FILTERED by stance:
    stance="for"     -> bias toward praise / high stars / positive polarity
    stance="against" -> bias toward complaints / low stars / high severity
    stance="neutral" -> pure semantic similarity
- Sarcastic items are down-weighted (a sarcastic "great" isn't real praise).
- Never raises; returns [] on any failure so the debate degrades gracefully.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np

log = logging.getLogger("insightmesh.evidence_store")

STAR_TO_NUM = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}

EmbedFn = Callable[[List[str]], Any]  # returns array-like (n, d)


def _canon_cat(label: Optional[str]) -> str:
    if not label:
        return "Neutral"
    l = str(label).strip().lower()
    for c in ("praise", "complaint", "suggestion", "prediction", "neutral"):
        if l.startswith(c):
            return c.capitalize()
    return "Neutral"


def _severity_num(sev: Any) -> float:
    """Map a severity blob/label to 0..1. Tolerant of shapes the analyzer emits."""
    if sev is None:
        return 0.0
    if isinstance(sev, (int, float)):
        return max(0.0, min(1.0, float(sev)))
    if isinstance(sev, dict):
        for k in ("score", "value", "level_num"):
            if isinstance(sev.get(k), (int, float)):
                return max(0.0, min(1.0, float(sev[k])))
        sev = sev.get("level") or sev.get("label")
    m = {"critical": 1.0, "severe": 0.9, "high": 0.8, "medium": 0.5,
         "moderate": 0.5, "low": 0.25, "minor": 0.2, "none": 0.0}
    return m.get(str(sev).strip().lower(), 0.0)


class EvidenceStore:
    """In-memory cosine retrieval index over reviews + their computed signals."""

    def __init__(self, embed_fn: EmbedFn):
        self._embed = embed_fn
        self._vecs: Optional[np.ndarray] = None  # (N, d) L2-normalized
        self._items: List[Dict[str, Any]] = []   # parallel metadata

    # ---------------- build ----------------
    def add_reviews(self, per_review: List[Dict[str, Any]]) -> int:
        """Embed and index every usable review with its computed signals.
        Returns the number of items indexed. Citation ids (`n`) are 1-based and
        stable, so agents can reference [#n] and the UI can resolve it."""
        texts: List[str] = []
        metas: List[Dict[str, Any]] = []
        for r in (per_review or []):
            if r.get("is_relevant") is False:
                continue
            text = (r.get("translated_text") or r.get("original") or "").strip()
            if len(text) < 8:
                continue
            stars = STAR_TO_NUM.get(r.get("sentiment"))
            cat = _canon_cat(r.get("review_category"))
            # Product insight from the deep classifier: what this comment reveals
            # about the product (even if it's superficially about a game/app/etc).
            product_insight = (r.get("deep") or {}).get("product_insight")
            metas.append({
                "n": len(metas) + 1,           # citation id, 1-based
                "text": text[:500],
                "stars": stars,
                "category": cat,
                "theme": r.get("canonical_reason") or None,
                "platform": r.get("platform") or None,
                "polarity": r.get("polarity"),
                "is_sarcastic": bool(r.get("is_sarcastic_llm") or (r.get("sarcasm") or {}).get("is_sarcastic")),
                "severity": _severity_num(r.get("severity")),
                "quality": float(r.get("quality") or 0.0),
                "buyer_intent": r.get("buyer_intent"),
                "product_insight": product_insight,
            })
            # Embed the comment + its product insight so an ecosystem comment that
            # carries hardware/perf intelligence is retrievable by product-aspect
            # queries, not just by its surface (game) wording.
            embed_text = text[:500]
            if product_insight and product_insight.get("insight"):
                embed_text = f"{embed_text}\n[reveals: {product_insight['insight'][:200]}]"
            texts.append(embed_text)

        if not texts:
            return 0
        try:
            arr = np.asarray(self._embed(texts), dtype=np.float32)
            if arr.ndim != 2:
                arr = arr.reshape(len(texts), -1)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        except Exception as e:
            log.warning("[evidence_store] embedding failed: %s", e)
            return 0

        self._vecs = arr
        self._items = metas
        return len(metas)

    @property
    def size(self) -> int:
        return len(self._items)

    def all_items(self) -> List[Dict[str, Any]]:
        """Full indexed pool (for the UI citation map + Judge reference)."""
        return list(self._items)

    # ---------------- retrieve ----------------
    def retrieve(self, query: str, *, stance: str = "neutral", k: int = 6) -> List[Dict[str, Any]]:
        """Return up to k items most relevant to `query`, re-ranked by stance.
        stance in {"for","against","neutral"}. Never raises."""
        if self._vecs is None or not self._items:
            return []
        try:
            q = np.asarray(self._embed([query or ""]), dtype=np.float32)
            if q.ndim != 2:
                q = q.reshape(1, -1)
            q = q[0]
            qn = np.linalg.norm(q)
            if qn:
                q = q / qn
            sims = self._vecs @ q  # cosine similarity, (N,)
        except Exception as e:
            log.warning("[evidence_store] query embed failed: %s", e)
            return []

        scored = []
        for idx, item in enumerate(self._items):
            score = float(sims[idx])
            score += self._stance_bonus(item, stance)
            if item.get("is_sarcastic"):
                score -= 0.15  # sarcasm is unreliable evidence
            score += 0.05 * float(item.get("quality") or 0.0)
            scored.append((score, item))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [it for _, it in scored[:k]]

    @staticmethod
    def _stance_bonus(item: Dict[str, Any], stance: str) -> float:
        cat = item.get("category")
        stars = item.get("stars")
        sev = float(item.get("severity") or 0.0)
        if stance == "for":
            b = 0.0
            if cat == "Praise":
                b += 0.20
            if isinstance(stars, int) and stars >= 4:
                b += 0.10
            if cat == "Complaint":
                b -= 0.10
            return b
        if stance == "against":
            b = 0.0
            if cat == "Complaint":
                b += 0.20
            if isinstance(stars, int) and stars <= 2:
                b += 0.10
            b += 0.20 * sev          # severe issues are the strongest "against"
            if cat == "Praise":
                b -= 0.10
            return b
        return 0.0
