# backend/insight/reasons/cluster.py
# Universal, reason-first clustering with singleton rescue + better labels.

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter, defaultdict

import numpy as np

# Optional clustering backends
try:
    import hdbscan  # type: ignore
except Exception:
    hdbscan = None

try:
    from sklearn.cluster import AgglomerativeClustering  # type: ignore
    from sklearn.metrics import pairwise_distances  # type: ignore
except Exception:
    AgglomerativeClustering = None
    pairwise_distances = None

# Optional TF-IDF for canonical phrase extraction
try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
except Exception:
    TfidfVectorizer = None

# Optional spaCy noun-chunks (best-effort)
try:
    import spacy  # type: ignore
    try:
        _NLP = spacy.load("en_core_web_sm")
    except Exception:
        _NLP = spacy.blank("en")
except Exception:
    _NLP = None

# -------------------- Config --------------------

@dataclass
class ClusterConfig:
    # Core clustering knobs
    use_hdbscan: bool = True
    min_cluster_size: int = 3
    min_samples: Optional[int] = None  # for HDBSCAN
    # Agglomerative fallback (cosine distance threshold)
    agglom_distance_threshold: float = 0.35
    # Greedy fallback (cosine similarity threshold for assignment)
    greedy_sim_threshold: float = 0.60
    # Merge behavior
    join_small_into_big: bool = True
    join_centroid_sim_threshold: float = 0.92  # cosine similarity (was 0.86 — too aggressive, merged distinct topics)
    # Label shaping / phrase mining
    max_label_len: int = 110
    ngram_min: int = 1
    ngram_max: int = 3
    ngram_min_df: int = 2
    top_terms_per_cluster: int = 5
    # Optional cap
    max_clusters: Optional[int] = None
    # Rescue logic when too many singletons
    rescue_if_singletons_ratio_ge: float = 0.6
    rescue_agglom_distance_threshold: float = 0.5  # looser than normal
    rescue_greedy_sim_threshold: float = 0.55

# -------------------- Text utils --------------------

_WS_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[A-Za-z0-9#@][A-Za-z0-9_\-']+")

STOP = set("""
a an the and or but if then else when while for with from into onto to of by on at in out up down over under
is are was were be been being do does did doing can could should would may might must will wont don't doesnt
this that these those here there where who whom whose which what why how very much more most less least same
you your yours me my mine we our ours they them their theirs he him his she her hers it its i
""".split())

EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]+", flags=re.UNICODE
)

def _norm(t: str) -> str:
    return _WS_RE.sub(" ", (t or "").strip())

def _strip_emoji(t: str) -> str:
    return EMOJI_RE.sub("", t or "")

def _tokens(t: str) -> List[str]:
    return [tok for tok in TOKEN_RE.findall(_norm(t).lower())]

def _filter_stop(ts: List[str]) -> List[str]:
    return [t for t in ts if t not in STOP and len(t) > 1 and not t.isdigit()]

def _ngrams(ts: List[str], n_min: int, n_max: int) -> List[str]:
    out: List[str] = []
    for n in range(n_min, n_max + 1):
        if n > len(ts): break
        for i in range(len(ts) - n + 1):
            out.append(" ".join(ts[i:i+n]))
    return out

def _titleish(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip(" .,:;!?\"'()[]{}")).strip()
    if not s: return s
    # Keep acronyms as-is; title-case the rest lightly
    words = s.split()
    out = []
    for w in words[:10]:  # keep short
        if w.isupper():
            out.append(w)
        else:
            out.append(w.capitalize() if len(w) > 3 else w.lower())
    return " ".join(out)

# -------------------- Cue/Heuristic label mining --------------------

COMPLAINT_PATS = [
    r"(?:does\s*not|doesn't|dont|doesnt)\s+(?:work|show|load|connect|support)\b(.*)",
    r"(?:not\s+working|won't|cant|can't|cannot|failed|fails|failure|crash(?:es)?|error)\b(.*)",
    r"(?:need|needs|require|requires|missing)\s+(.*)",
    r"(?:too\s+slow|lag|laggy|jerky|noisy|inaccurate|unstable)(.*)",
]
SUGGESTION_PATS = [
    r"(?:should|please\s+add|feature\s+request|would\s+love|could\s+you|let\s+us|add|allow|enable)\s+(.*)"
]

# Broad, product-agnostic label heuristics
HEUR_LABELS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(crash|error|fail|broken|bug)\b", re.I), "Crashes / errors"),
    (re.compile(r"\b(slow|lag|latency|stutter|delay)\b", re.I), "Performance lag"),
    (re.compile(r"\b(connect|disconnect|offline|network|wifi|bluetooth)\b", re.I), "Connectivity / offline"),
    (re.compile(r"\b(price|expensive|cost|value)\b", re.I), "Pricing / value"),
    (re.compile(r"\b(ui|ux|button|menu|navigation|confusing|hard to|difficult)\b", re.I), "Usability / UX"),
    (re.compile(r"\b(voice|siri|alexa|assistant)\b", re.I), "Voice control reliability"),
    (re.compile(r"\b(handle|latch|manual|override|door|lock)\b", re.I), "Manual controls / overrides"),
    (re.compile(r"\b(freeze|frozen|icy|cold|weather)\b", re.I), "Cold weather resilience"),
    (re.compile(r"\b(rent|rental|guest|borrow)\b", re.I), "Rental / guest access"),
    (re.compile(r"\b(battery|charge|charging)\b", re.I), "Battery / charging"),
    (re.compile(r"\b(camera|photo|video)\b", re.I), "Camera / media"),
    (re.compile(r"\b(display|screen|brightness)\b", re.I), "Display / screen"),
]

def _heuristic_label(texts: List[str]) -> Optional[str]:
    blob = " ".join(texts or [])[:4000]
    for pat, name in HEUR_LABELS:
        if pat.search(blob):
            return name
    return None

def _reason_candidates(text: str, ngram_min=1, ngram_max=3) -> List[str]:
    t = _strip_emoji(_norm(text))
    tl = t.lower()

    cands: List[str] = []
    for pat in COMPLAINT_PATS + SUGGESTION_PATS:
        for m in re.finditer(pat, tl, flags=re.IGNORECASE):
            frag = m.group(1) if m.groups() else ""
            frag = (frag or "").strip(" .,:;!?\"'()[]{}")
            if frag and 3 <= len(frag) <= 120:
                cands.append(frag)

    # Noun chunks (spaCy) — short, salient
    if _NLP is not None and t:
        try:
            doc = _NLP(t)
            for np in getattr(doc, "noun_chunks", []):
                s = re.sub(r"\s+", " ", np.text.strip())
                if 3 <= len(s) <= 80:
                    cands.append(s)
        except Exception:
            pass

    # Light n-gram salience fallback
    ts = _filter_stop(_tokens(tl))
    ngs = _ngrams(ts, ngram_min, ngram_max)
    ng_counts = Counter(ngs)
    for ng, cnt in ng_counts.most_common(5):
        if cnt >= 2 and len(ng) > 2:
            cands.append(ng)

    # dedupe keep-order + clean
    seen, out = set(), []
    for c in cands:
        k = c.lower()
        if k not in seen:
            out.append(c); seen.add(k)
    return out[:8]

def _clean_label(label: str) -> str:
    s = _strip_emoji(label or "")
    s = re.sub(r"[\"“”'`]+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .,:;!?")
    # Kill labels that are mostly stopwords/noise
    toks = _filter_stop(_tokens(s))
    if len(toks) == 0 or (len(toks) == 1 and len(toks[0]) <= 3):
        return ""
    # Avoid low-signal fragments
    if len(toks) < 2:
        return ""
    return _titleish(s)[:110]

# -------------------- Canonical phrase extraction --------------------

def _llm_cluster_label(texts: List[str], max_len: int) -> Optional[str]:
    """Ask the LLM for a short, accurate theme label grounded in the actual
    reviews. This replaces the tech-biased keyword heuristics as the primary
    path — so a cluster of Tesla strut/seat complaints is named for what it
    actually is, not stamped 'Usability / UX' because someone said 'confusing'.
    Returns None when no LLM backend is available."""
    try:
        from backend.utils import llm as _llm
        if _llm.available_backend() == "none":
            return None
    except Exception:
        return None
    sample = [(_strip_emoji(t) or "").strip()[:240] for t in (texts or []) if t and t.strip()][:12]
    if not sample:
        return None
    import json as _json
    prompt = (
        "Below are customer comments that were grouped into one theme. "
        "Give a SHORT, specific label (2-5 words) naming the PRODUCT ASPECT or ISSUE they discuss. "
        "Name the label after what the comments are ABOUT (e.g. 'Price concerns', 'Display quality', "
        "'Battery life issues', 'Comfort and weight', 'Content availability'), NOT after the "
        "comment STYLE or tone. "
        "NEVER use words like 'Mixed', 'Various', 'General', 'Unclear', or 'Miscellaneous' in the label. "
        "If comments touch multiple topics, pick the DOMINANT one that appears most often. "
        "If comments are sarcastic, name what the sarcasm is ABOUT (e.g. 'Ecosystem lock-in concerns' "
        "not 'Sarcastic comments about devices'). "
        "Return ONLY a JSON object: {\"label\": \"...\"}\n\n"
        f"Comments:\n{_json.dumps(sample, ensure_ascii=False)}"
    )
    try:
        out = _llm.chat_json([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=60)
        if isinstance(out, dict):
            lbl = (out.get("label") or "").strip()
            # Reject vague/mixed labels — force fallback to TF-IDF or heuristics
            _BAD_LABEL_WORDS = {"mixed", "various", "general", "unclear", "miscellaneous", "diverse", "multiple"}
            if lbl and len(lbl) <= max_len and not any(w in lbl.lower() for w in _BAD_LABEL_WORDS):
                return lbl
    except Exception:
        pass
    return None


def _canonical_phrase(texts: List[str], top_k: int, max_len: int) -> str:
    texts = [t for t in (texts or []) if t and t.strip()]
    if not texts:
        return "General issue / suggestion"

    # Blocklist for vague labels — applied to ALL labeling paths (LLM, TF-IDF, heuristic)
    _VAGUE_WORDS = {"mixed", "various", "general", "unclear", "miscellaneous", "diverse", "multiple"}
    def _is_vague(label: str) -> bool:
        return any(w in label.lower() for w in _VAGUE_WORDS)

    # LLM-first: an accurate, content-grounded label beats keyword guessing.
    llm_label = _llm_cluster_label(texts, max_len)
    if llm_label and not _is_vague(llm_label):
        return llm_label[:max_len]

    # TF-IDF across cluster (good when multi-doc) — data-driven, product-agnostic.
    if TfidfVectorizer is not None and len(texts) > 1:
        try:
            vec = TfidfVectorizer(
                ngram_range=(1, 3),
                stop_words=list(STOP),
                min_df=1,
                max_features=2000,
                strip_accents="unicode"
            )
            X = vec.fit_transform(texts)
            means = X.mean(axis=0)
            means = means.A1 if hasattr(means, "A1") else np.asarray(means).ravel()
            terms = vec.get_feature_names_out()
            idx = np.argsort(-means)[: max(1, top_k)]
            chosen = [terms[i] for i in idx]
            phrase = ", ".join(chosen[:3]).strip(", ")
            cl = _clean_label(phrase)
            if cl and not _is_vague(cl):
                return cl[:max_len]
        except Exception:
            pass

    # Heuristic keyword label (LAST resort — tech-biased, so only if nothing else worked)
    h = _heuristic_label(texts)
    if h:
        return h[:max_len]

    # Fallback: cue-based + n-grams + noun-chunks weighted
    bag = Counter()
    for s in texts:
        for j, cand in enumerate(_reason_candidates(s)[:6]):
            w = 6 - j
            cl = _clean_label(cand)
            if cl and not _is_vague(cl):
                bag[cl] += w
    if bag:
        return next(iter(bag.most_common(1)))[0][:max_len]

    # Last resort: most common tokens
    cnt: Dict[str, int] = {}
    for s in texts:
        for tok in _filter_stop(_tokens(s)):
            cnt[tok] = cnt.get(tok, 0) + 1
    top = [w for w, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))][: max(1, top_k)]
    return (", ".join(top[:3]) if top else "General issue / suggestion")[:max_len]

# -------------------- Vector helpers --------------------

def _mean_vec(arrs: List[np.ndarray]) -> np.ndarray:
    if len(arrs) == 1:
        v = arrs[0]
    else:
        v = np.vstack(arrs).mean(axis=0)
    n = np.linalg.norm(v) or 1.0
    return v / n

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-12
    return float(np.dot(a, b) / denom)

# -------------------- Fallback greedy clustering --------------------

def _cluster_greedy(emb: np.ndarray, sim_threshold: float) -> List[int]:
    labels: List[int] = [-1] * len(emb)
    centroids: List[np.ndarray] = []
    cid = -1
    for i, v in enumerate(emb):
        if cid < 0:
            cid = 0
            labels[i] = 0
            centroids.append(v)
            continue
        sims = [float(np.dot(v, c)) for c in centroids]  # vectors are normalized
        j = int(np.argmax(sims)) if sims else -1
        if j >= 0 and sims[j] >= sim_threshold:
            labels[i] = j
            centroids[j] = _mean_vec([centroids[j], v])
        else:
            labels[i] = len(centroids)
            centroids.append(v)
    return labels

# -------------------- Outlier/small cluster merge --------------------

def _merge_small_clusters(labels: List[int], emb: np.ndarray, cfg: ClusterConfig) -> List[int]:
    by_c: Dict[int, List[int]] = defaultdict(list)
    for i, c in enumerate(labels):
        if c >= 0:
            by_c[c].append(i)
    if not by_c:
        return labels

    centroids: Dict[int, np.ndarray] = {c: _mean_vec([emb[i] for i in idxs]) for c, idxs in by_c.items()}
    sizes = {c: len(idxs) for c, idxs in by_c.items()}
    bigs = sorted(sizes, key=lambda k: sizes[k], reverse=True)

    new_labels = labels[:]
    for c, idxs in by_c.items():
        if sizes[c] >= max(2, cfg.min_cluster_size):
            continue
        # nearest big cluster
        best_c, best_sim = c, -1.0
        for bc in bigs:
            if bc == c:
                continue
            sim = float(np.dot(centroids[c], centroids[bc]))
            if sim > best_sim:
                best_sim, best_c = sim, bc
        if best_sim >= cfg.join_centroid_sim_threshold:
            for i in idxs:
                new_labels[i] = best_c
    return new_labels

def _singleton_ratio(labels: List[int]) -> float:
    sizes = Counter([l for l in labels if l >= 0])
    if not sizes: return 1.0
    singletons = sum(1 for _, sz in sizes.items() if sz == 1)
    return singletons / max(1, len(sizes))

# -------------------- Public API --------------------

def canonical_clusters(
    texts: List[str],
    embedder: Any,
    config: Optional[ClusterConfig] = None
) -> Dict[str, Any]:
    cfg = config or ClusterConfig()
    texts = [t for t in (texts or []) if t and t.strip()]
    if not texts or embedder is None:
        reason = _canonical_phrase(texts, cfg.top_terms_per_cluster, cfg.max_label_len)
        return {
            "labels": [0] * len(texts),
            "clusters": [{
                "id": 0,
                "size": len(texts),
                "canonical_reason": reason,
                "support": 0.5,
                "centroid_sim_mean": 1.0 if texts else 0.0,
                "quotes": texts[:2]
            }],
            "debug": {"mode": "degenerate", "n_docs": len(texts)}
        }

    # Encode (normalized)
    emb = embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    N = int(emb.shape[0])

    # Small-N shortcut
    if N <= 2:
        centroid = _mean_vec([emb[i] for i in range(N)])
        sims = [float(np.dot(emb[i], centroid)) for i in range(N)]
        reason = _canonical_phrase(texts, cfg.top_terms_per_cluster, cfg.max_label_len)
        return {
            "labels": [0] * N,
            "clusters": [{
                "id": 0,
                "size": N,
                "canonical_reason": reason,
                "support": round(min(0.95, 0.6 + 0.2 * (N - 1)), 2),
                "centroid_sim_mean": float(np.mean(sims)) if sims else 0.0,
                "quotes": texts[:2]
            }],
            "debug": {"mode": "small_n", "n_docs": N}
        }

    # ---- Initial clustering backend ----
    labels: List[int]
    mode = "greedy"
    if cfg.use_hdbscan and hdbscan is not None:
        try:
            min_cs = max(2, min(cfg.min_cluster_size, N))
            min_s  = None if cfg.min_samples is None else max(1, min(cfg.min_samples, N))
            X = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
            cl = hdbscan.HDBSCAN(
                min_cluster_size=min_cs,
                min_samples=min_s,
                metric="euclidean",
                prediction_data=True,
                approx_min_span_tree=True,
            )
            labels = cl.fit_predict(X).tolist()
            mode = "hdbscan"
        except Exception:
            labels = _cluster_greedy(emb, cfg.greedy_sim_threshold)
            mode = "greedy_fallback"
    elif AgglomerativeClustering is not None and pairwise_distances is not None:
        try:
            D = pairwise_distances(emb, metric="cosine")
            # sklearn >= 1.4 uses `metric=` instead of deprecated `affinity=`
            agg = AgglomerativeClustering(
                metric="precomputed",
                linkage="average",
                distance_threshold=cfg.agglom_distance_threshold,
                n_clusters=None,
            )
            labels = agg.fit_predict(D).tolist()
            mode = "agglomerative"
        except TypeError:
            # Older sklearn (<1.2) still uses affinity=
            try:
                D = pairwise_distances(emb, metric="cosine")
                agg = AgglomerativeClustering(
                    affinity="precomputed",
                    linkage="average",
                    distance_threshold=cfg.agglom_distance_threshold,
                    n_clusters=None,
                )
                labels = agg.fit_predict(D).tolist()
                mode = "agglomerative_legacy"
            except Exception:
                labels = _cluster_greedy(emb, cfg.greedy_sim_threshold)
                mode = "greedy_fallback"
        except Exception:
            labels = _cluster_greedy(emb, cfg.greedy_sim_threshold)
            mode = "greedy_fallback"
    else:
        labels = _cluster_greedy(emb, cfg.greedy_sim_threshold)
        mode = "greedy"

    # ---- Collapse rescue: HDBSCAN found no structure on a batch big enough
    #      to have some. On small batches (e.g. 15 reviews spanning comfort /
    #      sound / build / price / app) HDBSCAN frequently labels everything as
    #      noise (-1); the reindex step below then folds every -1 into a SINGLE
    #      cluster, so a diverse corpus reports just one theme. When that happens
    #      we re-cluster with agglomerative at a target count so distinct topics
    #      stay separate. Only triggers off the HDBSCAN path — greedy/agglomerative
    #      already split by similarity and shouldn't be force-split when uniform. ----
    def _n_real_clusters(lbls: List[int]) -> int:
        return len({l for l in lbls if l >= 0})

    if "hdbscan" in mode and _n_real_clusters(labels) <= 1 and N >= 6:
        # ~one cluster per 5-8 docs (so 85 reviews → ~10-17 topics), bounded to
        # a sensible range. The post-merge step below folds back any that turn out
        # near-identical, so erring slightly high here is safe.
        target_k = max(3, min(12, round(N / 5)))
        if AgglomerativeClustering is not None and pairwise_distances is not None:
            try:
                D = pairwise_distances(emb, metric="cosine")
                try:
                    agg = AgglomerativeClustering(
                        metric="precomputed", linkage="average", n_clusters=target_k
                    )
                except TypeError:
                    agg = AgglomerativeClustering(
                        affinity="precomputed", linkage="average", n_clusters=target_k
                    )
                labels = agg.fit_predict(D).tolist()
                mode = f"{mode}+collapse_rescue_agglomerative_k{target_k}"
            except Exception:
                labels = _cluster_greedy(emb, cfg.rescue_greedy_sim_threshold)
                mode = f"{mode}+collapse_rescue_greedy"
        else:
            labels = _cluster_greedy(emb, cfg.rescue_greedy_sim_threshold)
            mode = f"{mode}+collapse_rescue_greedy"

    # ---- Rescue: too many singletons? Re-cluster looser ----
    if any(l >= 0 for l in labels):
        if _singleton_ratio(labels) >= cfg.rescue_if_singletons_ratio_ge:
            try:
                if AgglomerativeClustering is not None and pairwise_distances is not None:
                    D = pairwise_distances(emb, metric="cosine")
                    # sklearn >= 1.4 uses `metric=` instead of deprecated `affinity=`
                    try:
                        agg = AgglomerativeClustering(
                            metric="precomputed",
                            linkage="average",
                            distance_threshold=cfg.rescue_agglom_distance_threshold,
                            n_clusters=None,
                        )
                    except TypeError:
                        agg = AgglomerativeClustering(
                            affinity="precomputed",
                            linkage="average",
                            distance_threshold=cfg.rescue_agglom_distance_threshold,
                            n_clusters=None,
                        )
                    labels = agg.fit_predict(D).tolist()
                    mode = f"{mode}+rescue_agglomerative"
                else:
                    labels = _cluster_greedy(emb, cfg.rescue_greedy_sim_threshold)
                    mode = f"{mode}+rescue_greedy"
            except Exception:
                pass

    # ---- Merge small/outlier clusters ----
    if cfg.join_small_into_big and any(l >= 0 for l in labels):
        labels = _merge_small_clusters(labels, emb, cfg)

    # Reindex to [0..K-1]
    if any(l == -1 for l in labels):
        # Instead of giving each noise its own id (bad UX),
        # map all remaining -1 to the nearest existing centroid if possible;
        # otherwise place them into a single "misc" cluster 0.
        pos_ids = sorted(set([l for l in labels if l >= 0]))
        if pos_ids:
            cents = {c: _mean_vec([emb[i] for i, l in enumerate(labels) if l == c]) for c in pos_ids}
            for i, l in enumerate(labels):
                if l == -1:
                    best, best_sim = None, -1.0
                    for c, cv in cents.items():
                        sim = float(np.dot(emb[i], cv))
                        if sim > best_sim:
                            best, best_sim = c, sim
                    labels[i] = best if best is not None else 0
        else:
            labels = [0] * len(labels)

    unique_ids: List[int] = []
    remap: Dict[int, int] = {}
    for l in labels:
        if l not in remap:
            remap[l] = len(unique_ids)
            unique_ids.append(l)
    labels = [remap[l] for l in labels]

    # Optional cap
    if cfg.max_clusters is not None and len(set(labels)) > cfg.max_clusters:
        while len(set(labels)) > cfg.max_clusters:
            clusters = list(set(labels))
            sizes = {c: labels.count(c) for c in clusters}
            k_small = min(clusters, key=lambda c: sizes[c])
            cents = {c: _mean_vec([emb[i] for i, l in enumerate(labels) if l == c]) for c in clusters}
            target = max([c for c in clusters if c != k_small], key=lambda c: float(np.dot(cents[k_small], cents[c])))
            labels = [target if l == k_small else l for l in labels]

    # ---- Build cluster metadata ----
    clusters_out: List[Dict[str, Any]] = []
    for cid in sorted(set(labels)):
        idxs = [i for i, l in enumerate(labels) if l == cid]
        size = len(idxs)
        texts_c = [texts[i] for i in idxs]
        centroid = _mean_vec([emb[i] for i in idxs])
        sims = [float(np.dot(emb[i], centroid)) for i in idxs]
        sim_mean = float(np.mean(sims)) if sims else 0.0

        reason = _canonical_phrase(texts_c, cfg.top_terms_per_cluster, cfg.max_label_len)
        support = float(min(0.95, 0.35 + 0.45 * sim_mean + 0.2 * (1 - math.exp(-size / 4.0))))

        # quotes: medoid + diverse
        if idxs:
            medoid = max(idxs, key=lambda i: float(np.dot(emb[i], centroid)))
        else:
            medoid = -1
        quotes = []
        if 0 <= medoid < len(texts):
            quotes.append(texts[medoid])
        if len(idxs) > 1:
            far = min(idxs, key=lambda i: float(np.dot(emb[i], centroid)))
            if far != medoid:
                quotes.append(texts[far])
        quotes = quotes[:2]

        clusters_out.append({
            "id": int(cid),
            "size": int(size),
            "canonical_reason": reason,
            "support": round(support, 3),
            "centroid_sim_mean": round(sim_mean, 3),
            "quotes": quotes,
        })

    clusters_out.sort(key=lambda d: (-d["size"], -d["centroid_sim_mean"], d["id"]))

    return {
        "labels": labels,
        "clusters": clusters_out,
        "debug": {
            "mode": mode,
            "n_docs": len(texts),
            "n_clusters": len(clusters_out),
            "min_cluster_size": cfg.min_cluster_size,
            "join_small_into_big": cfg.join_small_into_big,
            "join_centroid_sim_threshold": cfg.join_centroid_sim_threshold,
        }
    }
