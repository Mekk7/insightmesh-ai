"""
Smart review sampler — pick the most information-dense, topic-diverse subset of
comments BEFORE analysis. Pure heuristics + word-overlap diversity. No LLM, no
embeddings; runs in milliseconds.

Replaces "first N that survive dedup" with "best N for maximum insight coverage":
instead of analyzing the N longest/first comments, it greedily selects high-value
comments that also cover DIFFERENT topics, so the dashboard reflects the breadth
of feedback rather than a redundant pile of the same complaint.

Target count comes from the depth preset (Quick 15 / Balanced 40 / Deep 80).
"""
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Set

log = logging.getLogger("insightmesh.smart_sampler")

_WORD_RE = re.compile(r"[a-z0-9']+")

# Tiny stopword set — enough to make word-overlap similarity meaningful without
# pulling in a dependency. Keeps content words (nouns/verbs/adjectives).
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "is", "it", "its",
    "this", "that", "these", "those", "with", "for", "as", "at", "by", "be", "been",
    "are", "was", "were", "i", "you", "he", "she", "they", "we", "my", "me", "your",
    "so", "if", "then", "than", "too", "very", "just", "not", "no", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "im", "ive", "dont",
    "really", "like", "get", "got", "one", "all", "out", "up", "about", "from", "what",
}

# Topic buckets — used both as a feature signal (more topics → more specific) and to
# report selection coverage in the log line.
_TOPIC_PATTERNS = {
    "price":       r"(\$\s?\d|\d+\s?(dollars|usd|bucks)|expensive|overpriced|worth\s+it|not\s+worth|cheap|afford|\bcosts?\b|price)",
    "display":     r"(display|screen|resolution|\bfov\b|field of view|passthrough|clarity|pixel|visual|blurr?y|sharp)",
    "comfort":     r"(comfort|weight|heavy|light|strap|fit|wear|wearing|nausea|headache|face|forehead)",
    "battery":     r"(battery|charge|charging|\bpower\b|drain|hours? of use|dies?)",
    "content":     r"(content|\bapps?\b|games?|movies?|software|ecosystem|library|streaming|netflix)",
    "comparison":  r"(compared to|better than|worse than|\bvs\b|\bversus\b|unlike|switched from|quest|meta|samsung|index|psvr|hololens)",
    "audio":       r"(audio|sound|speakers?|microphones?|\bmic\b|spatial)",
    "performance": r"(lag|latency|\bfps\b|refresh|stutter|smooth|crash|bug|glitch|freeze|overheat)",
    "setup":       r"(setup|set up|install|pairing|account|sign in|onboarding|tutorial)",
}
_TOPIC_RE = {k: re.compile(v, re.I) for k, v in _TOPIC_PATTERNS.items()}

_COMPARISON_RE = _TOPIC_RE["comparison"]
_PRICE_RE = _TOPIC_RE["price"]
_FEATURE_RE = re.compile(
    r"(battery|display|screen|camera|speaker|comfort|weight|price|app|software|build|"
    r"resolution|passthrough|\bfov\b|field of view|lens|strap|fit|audio|tracking|latency|refresh)",
    re.I,
)
_PERSONAL_RE = re.compile(
    r"(i (bought|use|used|own|owned|returned|tried|wear|wore|have|had|got|tested)|"
    r"i'?ve (been|used|had|owned|tried)|my (headset|unit|device|pair|experience)|"
    r"mine (has|is|was|came|broke)|in my experience|for me\b)",
    re.I,
)

# How strongly to penalize similarity to already-picked reviews (0 = ignore diversity,
# 1 = diversity dominates). 0.6 favors high-value picks while still spreading topics.
_DIVERSITY_WEIGHT = 0.6


def _tokens(text: str) -> Set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOP}


def _similarity(a: Set[str], b: Set[str]) -> float:
    """Jaccard overlap of content-word sets (0..1). Fast, no embeddings."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _info_score(text: str, word_count: int) -> float:
    """Estimated information value of a single comment (0..1), pure heuristics."""
    score = min(0.35, word_count / 120.0)                 # length (caps at 120 words)
    if _COMPARISON_RE.search(text):
        score += 0.2                                       # comparisons are gold
    if _PRICE_RE.search(text):
        score += 0.15
    if _FEATURE_RE.search(text):
        score += 0.15
    if _PERSONAL_RE.search(text):
        score += 0.15
    return min(1.0, score)


def _topics(text: str) -> Set[str]:
    return {k for k, rx in _TOPIC_RE.items() if rx.search(text)}


def smart_sample(items: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    """Select up to `target` comments maximizing information value AND topic diversity.

    Greedy: take the highest-value comment, then repeatedly take the comment that best
    trades off its own value against redundancy (word overlap) with what's already
    picked. Pure computation; logs the selected topic coverage. Returns `items`
    unchanged when there's nothing to trim (<= target candidates or target <= 0).
    """
    n = len(items)
    if target <= 0 or n <= target:
        return items

    scored: List[Dict[str, Any]] = []
    for it in items:
        text = (it.get("text") or it.get("original") or "")
        toks = _tokens(text)
        wc = len(text.split())
        scored.append({
            "item": it, "tokens": toks,
            "score": _info_score(text, wc), "topics": _topics(text),
            "max_sim": 0.0,
        })

    # Seed with the single highest-value comment.
    scored.sort(key=lambda s: s["score"], reverse=True)
    selected = [scored.pop(0)]
    for cand in scored:
        cand["max_sim"] = _similarity(cand["tokens"], selected[0]["tokens"])

    # Greedily add the best value-minus-redundancy candidate; update redundancy
    # incrementally against only the newly added pick (keeps it O(target * candidates)).
    while scored and len(selected) < target:
        best_i = max(range(len(scored)),
                     key=lambda i: scored[i]["score"] - _DIVERSITY_WEIGHT * scored[i]["max_sim"])
        pick = scored.pop(best_i)
        selected.append(pick)
        for cand in scored:
            sim = _similarity(cand["tokens"], pick["tokens"])
            if sim > cand["max_sim"]:
                cand["max_sim"] = sim

    coverage: Counter = Counter()
    for s in selected:
        for t in s["topics"]:
            coverage[t] += 1
    cov_str = " ".join(f"{k}({c})" for k, c in coverage.most_common()) or "general"
    log.info("[smart_sampler] selected %d from %d candidates, coverage: %s",
             len(selected), n, cov_str)

    return [s["item"] for s in selected]
