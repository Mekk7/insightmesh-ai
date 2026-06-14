# backend/insight/coverage/coverage_map.py
"""
Coverage Map — investigation state for the coverage-driven, self-expanding scraper.

The model investigates a product like a human researcher:

  1. EXPECTATION   — start from the product's "expected feedback map" (categories a
     product like this COULD receive feedback on), from Product Intelligence.
  2. DEEP CATEGORIES — each category holds SUB-PROBLEMS discovered from comments
     ("battery" → fast discharge, heating while charging, degradation, …).
  3. OPEN DISCOVERY — an insight that fits NO expected category CREATES a new one;
     surprises are welcomed and tracked, never discarded.
  4. SATURATION    — a category is "understood" once new comments cluster with what's
     already known (high embedding similarity) and stop adding new sub-problems.
  5. COVERAGE      — after each round we know which categories are well-covered, thin,
     gaps (expected but zero), or freshly discovered → which drives the next round's
     targeted, gap-filling searches.

Embedding is OPTIONAL. With an embedder (anything exposing `.encode([texts])`),
category assignment and sub-problem splitting use cosine similarity. Without one, a
token-overlap fallback keeps everything working — and unit-testable — with no model.

This is data in / data out and never raises on a single bad insight; it's safe to
drive from the streaming pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("insightmesh.coverage")

# All thresholds live here and are overridable via SCRAPER_CONFIG["coverage"].
DEFAULT_COVERAGE_CONFIG: Dict[str, Any] = {
    "max_rounds": 6,                 # hard cap on investigation rounds
    "thin_threshold": 5,             # < this many mentions → "thin"
    "well_covered_threshold": 8,     # >= this → "well covered"
    "saturation_min_mentions": 6,    # need this many before saturation can be declared
    "category_match_similarity": 0.45,  # insight↔category sim to join an existing category
    "subproblem_similarity": 0.60,   # insight↔existing sub-problem sim to merge vs. create new
    "coverage_stop_fraction": 0.70,  # stop when this fraction of EXPECTED categories are covered
    "max_gap_queries_per_round": 4,  # cap targeted gap searches issued per round
}

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "this", "that",
    "with", "for", "problem", "problems", "issue", "issues", "general", "review", "reviews",
    "product", "thing", "things", "about", "very",
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1 and t not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cosine(u, v) -> float:
    try:
        import numpy as np
        u = np.asarray(u, dtype=float)
        v = np.asarray(v, dtype=float)
        nu = float(np.linalg.norm(u))
        nv = float(np.linalg.norm(v))
        if nu == 0.0 or nv == 0.0:
            return 0.0
        return float(np.dot(u, v) / (nu * nv))
    except Exception:
        return 0.0


class _SubProblem:
    __slots__ = ("label", "count", "vec", "sentiment", "quotes")

    def __init__(self, label: str, vec=None):
        self.label = label
        self.count = 1
        self.vec = vec
        self.sentiment = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
        self.quotes: List[str] = []


class _Category:
    def __init__(self, name: str, expected: bool, vec=None, discovered_round: int = 0):
        self.name = name
        self.expected = expected
        self.vec = vec
        self.mentions = 0
        self.subs: List[_SubProblem] = []
        self.sentiment = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
        self.discovered_round = discovered_round
        # per-round bookkeeping for saturation
        self._new_subs_round = 0
        self._mentions_round = 0
        self.rounds_no_new = 0  # consecutive rounds with mentions but no new sub-problem


class CoverageMap:
    """Grows an investigation map of feedback categories + sub-problems from the deep
    classifier's product_insights, and reports coverage to drive gap-filling searches."""

    def __init__(self, expected_categories: Optional[List[str]] = None, embedder=None,
                 config: Optional[Dict[str, Any]] = None):
        self.cfg = {**DEFAULT_COVERAGE_CONFIG, **(config or {})}
        self.embedder = embedder
        self.round = 0
        self.cats: Dict[str, _Category] = {}
        self.new_categories_round: List[str] = []
        for c in (expected_categories or []):
            if _norm(c):
                self._ensure_category(c, expected=True)

    # ---- embedding helpers (no-op without an embedder) ----
    def _embed(self, text: str):
        if not self.embedder or not text:
            return None
        try:
            return self.embedder.encode([text])[0]
        except Exception:
            return None

    def _sim(self, vec, text: str, other_vec, other_text: str) -> float:
        if vec is not None and other_vec is not None:
            return _cosine(vec, other_vec)
        return _jaccard(_tokens(text), _tokens(other_text))

    # ---- map construction ----
    def _ensure_category(self, name: str, expected: bool) -> _Category:
        key = _norm(name)
        if key in self.cats:
            return self.cats[key]
        cat = _Category(
            name=name.strip(),
            expected=expected,
            vec=self._embed(name.replace("/", " ")),
            discovered_round=self.round,
        )
        self.cats[key] = cat
        if not expected and self.round > 0:
            self.new_categories_round.append(cat.name)
        return cat

    def _match_category(self, aspect: str, insight: str):
        """Best existing category for this insight, or (None, vec) to trigger discovery."""
        text = f"{aspect} {insight}".strip()
        vec = self._embed(text)
        best: Optional[_Category] = None
        best_score = 0.0
        for cat in self.cats.values():
            score = self._sim(vec, text, cat.vec, cat.name)
            # token overlap on the aspect alone is a strong signal too
            score = max(score, _jaccard(_tokens(aspect), _tokens(cat.name)))
            if score > best_score:
                best_score, best = score, cat
        if best is not None and best_score >= self.cfg["category_match_similarity"]:
            return best, vec
        return None, vec

    def _assign_subproblem(self, cat: _Category, insight: str, vec, sentiment: str, quote: str) -> bool:
        """Merge into the nearest sub-problem, or create a new one. Returns is_new."""
        best: Optional[_SubProblem] = None
        best_score = 0.0
        for sp in cat.subs:
            score = self._sim(vec, insight, sp.vec, sp.label)
            if score > best_score:
                best_score, best = score, sp
        if best is not None and best_score >= self.cfg["subproblem_similarity"]:
            best.count += 1
            if sentiment in best.sentiment:
                best.sentiment[sentiment] += 1
            if quote and len(best.quotes) < 3 and quote not in best.quotes:
                best.quotes.append(quote)
            return False
        sp = _SubProblem(label=insight[:90], vec=vec)
        if sentiment in sp.sentiment:
            sp.sentiment[sentiment] += 1
        if quote:
            sp.quotes.append(quote)
        cat.subs.append(sp)
        return True

    # ---- round lifecycle ----
    def start_round(self) -> int:
        self.round += 1
        self.new_categories_round = []
        for cat in self.cats.values():
            cat._new_subs_round = 0
            cat._mentions_round = 0
        return self.round

    def ingest(self, insights: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest a batch of product_insight dicts {aspect, insight, sentiment, quote,...}.
        Call start_round() first. Returns this round's delta."""
        added = 0
        for pi in insights or []:
            if not isinstance(pi, dict):
                continue
            insight = (pi.get("insight") or "").strip()
            if not insight:
                continue
            aspect = (pi.get("aspect") or "general").strip()
            sentiment = (pi.get("sentiment") or "neutral").strip().lower()
            if sentiment not in ("positive", "negative", "neutral", "mixed"):
                sentiment = "neutral"
            quote = (pi.get("quote") or "").strip()[:200]

            cat, vec = self._match_category(aspect, insight)
            if cat is None:
                cat = self._ensure_category(aspect, expected=False)
                if cat.vec is None:
                    cat.vec = vec
            cat.mentions += 1
            cat._mentions_round += 1
            if sentiment in cat.sentiment:
                cat.sentiment[sentiment] += 1
            if self._assign_subproblem(cat, insight, vec, sentiment, quote):
                cat._new_subs_round += 1
            added += 1

        # Update saturation counters: a category that got fresh mentions this round
        # but produced NO new sub-problem is converging on "understood".
        for cat in self.cats.values():
            if cat._mentions_round > 0 and cat._new_subs_round == 0:
                cat.rounds_no_new += 1
            elif cat._new_subs_round > 0:
                cat.rounds_no_new = 0

        delta = {
            "round": self.round,
            "mentions_added": added,
            "new_categories": list(self.new_categories_round),
            "new_subproblems": sum(c._new_subs_round for c in self.cats.values()),
        }
        return delta

    # ---- assessment ----
    def _state(self, cat: _Category) -> str:
        if cat.mentions == 0:
            return "gap"
        if cat.mentions >= self.cfg["well_covered_threshold"]:
            return "well_covered"
        if cat.mentions >= self.cfg["saturation_min_mentions"] and cat.rounds_no_new >= 1:
            return "saturated"
        if cat.mentions < self.cfg["thin_threshold"]:
            return "thin"
        return "developing"

    def _is_covered(self, cat: _Category) -> bool:
        return self._state(cat) in ("well_covered", "saturated", "developing")

    def assess(self) -> Dict[str, Any]:
        buckets: Dict[str, List[str]] = {
            "gap": [], "thin": [], "developing": [], "saturated": [], "well_covered": [],
        }
        for cat in self.cats.values():
            buckets[self._state(cat)].append(cat.name)
        expected = [c for c in self.cats.values() if c.expected]
        covered = [c for c in expected if self._is_covered(c)]
        frac = round(len(covered) / len(expected), 3) if expected else 1.0
        return {
            **buckets,
            "n_categories": len(self.cats),
            "expected_total": len(expected),
            "expected_covered": len(covered),
            "coverage_fraction": frac,
            "new_categories_last_round": list(self.new_categories_round),
        }

    def expansion_targets(self) -> List[str]:
        """Gap (zero) categories first, then thin ones — the topics to hunt next round.
        Saturated/developing/well-covered categories are deliberately NOT re-pulled."""
        gaps, thins = [], []
        for cat in self.cats.values():
            st = self._state(cat)
            if st == "gap":
                gaps.append(cat.name)
            elif st == "thin":
                thins.append(cat.name)
        return (gaps + thins)[: self.cfg["max_gap_queries_per_round"]]

    def is_done(self) -> bool:
        a = self.assess()
        return (
            a["coverage_fraction"] >= self.cfg["coverage_stop_fraction"]
            and not a["new_categories_last_round"]
            and not [c for c in self.cats.values() if self._state(c) == "gap"]
        )

    # ---- reporting ----
    def report(self) -> Dict[str, Any]:
        """Structured coverage + an honest, human-readable summary."""
        def _entry(cat: _Category) -> Dict[str, Any]:
            top = sorted(cat.subs, key=lambda s: -s.count)[:4]
            return {
                "category": cat.name,
                "mentions": cat.mentions,
                "state": self._state(cat),
                "expected": cat.expected,
                "discovered_round": cat.discovered_round,
                "sentiment": dict(cat.sentiment),
                "sub_problems": [{"label": s.label, "count": s.count, "sentiment": dict(s.sentiment)} for s in top],
            }

        cats_sorted = sorted(self.cats.values(), key=lambda c: -c.mentions)
        categories = [_entry(c) for c in cats_sorted]

        well = [c for c in cats_sorted if self._state(c) in ("well_covered", "saturated")]
        thin = [c for c in cats_sorted if self._state(c) == "thin"]
        gaps = [c for c in cats_sorted if self._state(c) == "gap"]
        discovered = [c for c in cats_sorted if (not c.expected and c.mentions > 0)]

        def _with_subs(cat: _Category) -> str:
            subs = sorted(cat.subs, key=lambda s: -s.count)[:2]
            labels = [s.label.split(":")[0][:32] for s in subs]
            return f"{cat.name} (incl. {' + '.join(labels)})" if labels else cat.name

        parts: List[str] = [f"Investigated {len(self.cats)} feedback categories."]
        if well:
            parts.append("Well-covered: " + ", ".join(_with_subs(c) for c in well[:5]) + ".")
        if thin:
            parts.append("Thin: " + ", ".join(f"{c.name} ({c.mentions} mention{'s' if c.mentions != 1 else ''})" for c in thin[:4]) + ".")
        if discovered:
            parts.append("Discovered unexpected: " + ", ".join(f"{c.name} ({c.mentions} owner{'s' if c.mentions != 1 else ''})" for c in discovered[:4]) + ".")
        if gaps:
            parts.append("Couldn't find enough on: " + ", ".join(c.name for c in gaps[:5]) + ".")

        return {
            "summary": " ".join(parts),
            "assessment": self.assess(),
            "rounds": self.round,
            "categories": categories,
            "discovered_categories": [c.name for c in discovered],
            "gaps": [c.name for c in gaps],
        }

    def log_state(self, where: str = "") -> None:
        """One-line per-round log so the investigation can be watched unfold."""
        a = self.assess()
        snapshot = sorted(
            ((c.name, c.mentions, len(c.subs), self._state(c)) for c in self.cats.values()),
            key=lambda x: -x[1],
        )
        pretty = " | ".join(f"{n}:{m}m/{s}sp[{st}]" for n, m, s, st in snapshot[:12])
        log.info(
            "[coverage] round=%d %s covered=%d/%d (%.0f%%) new_cats=%s :: %s",
            self.round, where, a["expected_covered"], a["expected_total"],
            100 * a["coverage_fraction"], a["new_categories_last_round"], pretty,
        )


def build_coverage_map(product_intel: Any, embedder=None, config: Optional[Dict[str, Any]] = None) -> CoverageMap:
    """Construct a CoverageMap seeded from a ProductIntelligence's expected feedback
    map (falling back to its direct_aspects, then to nothing)."""
    expected: List[str] = []
    if product_intel is not None:
        try:
            expected = list(getattr(product_intel, "expected_feedback_categories", None) or [])
            if not expected:
                expected = list(getattr(product_intel, "direct_aspects", None) or [])
        except Exception:
            expected = []
    return CoverageMap(expected_categories=expected, embedder=embedder, config=config)
