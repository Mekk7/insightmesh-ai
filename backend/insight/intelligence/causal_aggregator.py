# backend/insight/intelligence/causal_aggregator.py
# Causal Chain Aggregation.
#
# The deep classifier extracts a `causal_chain` per review — an ordered list of
# steps ["cause", "effect", "consequence", ...]. This module groups those chains
# across reviews by semantic similarity so we can surface the causal paths that
# MANY independent reviewers report (e.g. "battery drain → after firmware update",
# confirmed by 6 reviewers), not one-off anecdotes.
#
# Pure computation. No LLM, no network. Uses the shared all-MiniLM-L6-v2 embedder
# when provided; degrades to token-overlap so it stays unit-testable.

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _norm_steps(chain: Any) -> List[str]:
    """Coerce a raw causal_chain into a clean list of step strings (>=2 steps)."""
    if not isinstance(chain, list):
        return []
    steps = [str(s).strip() for s in chain if isinstance(s, (str, int, float)) and str(s).strip()]
    return steps


def _chain_text(steps: List[str]) -> str:
    return " → ".join(steps)


def _tokens(s: str) -> set:
    return set(_TOKEN_RE.findall((s or "").lower()))


def _token_sim(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def aggregate_causal_findings(
    causal_chains: List[Any],
    embedder: Optional[Any] = None,
    top_k: int = 5,
    similarity: float = 0.62,
) -> List[Dict[str, Any]]:
    """Group causal chains by semantic similarity and rank by how many independent
    reviewers report each path.

    Args:
      causal_chains: list of chains; each chain is a list of step strings
                     (as produced per-review by the deep classifier and collected
                     into deep_signals["causal_chains"]).
      embedder:      optional SentenceTransformer; falls back to token overlap.
      top_k:         max findings to return.
      similarity:    cosine/Jaccard threshold to treat two chains as the same path.

    Returns: up to top_k findings, each:
      {summary, root_cause, effect, chain, confirmations, examples}
      sorted by `confirmations` (independent reviewer count) descending.
    """
    chains: List[List[str]] = []
    for ch in (causal_chains or []):
        steps = _norm_steps(ch)
        if len(steps) >= 2:  # need at least cause -> effect to be a "chain"
            chains.append(steps)
    if not chains:
        return []

    texts = [_chain_text(c) for c in chains]
    n = len(texts)

    emb = None
    if embedder is not None and np is not None:
        try:
            emb = embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        except Exception:
            emb = None

    # Greedy single-pass clustering by similarity to a running centroid (embedding)
    # or to any existing member (token overlap).
    groups: List[Dict[str, Any]] = []  # {"members": [idx], "centroid": vec|None}
    for i in range(n):
        best_g, best_s = -1, 0.0
        for gi, g in enumerate(groups):
            if emb is not None:
                s = float(np.dot(emb[i], g["centroid"]))
            else:
                s = max(_token_sim(texts[i], texts[m]) for m in g["members"])
            if s > best_s:
                best_s, best_g = s, gi
        if best_g >= 0 and best_s >= similarity:
            g = groups[best_g]
            g["members"].append(i)
            if emb is not None:
                v = emb[g["members"]].mean(axis=0)
                nrm = float(np.linalg.norm(v)) or 1.0
                g["centroid"] = v / nrm
        else:
            groups.append({"members": [i], "centroid": (emb[i] if emb is not None else None)})

    findings: List[Dict[str, Any]] = []
    for g in groups:
        members = g["members"]
        # Representative chain = the longest (most complete) in the group.
        rep = max(members, key=lambda m: len(chains[m]))
        rep_chain = chains[rep]
        findings.append({
            "summary": _chain_text(rep_chain),
            "root_cause": rep_chain[0],
            "effect": rep_chain[-1],
            "chain": rep_chain,
            "confirmations": len(members),     # independent reviewers on this path
            "examples": [texts[m] for m in members[:3]],
        })

    findings.sort(key=lambda f: (-f["confirmations"], -len(f["chain"])))
    return findings[:top_k]
