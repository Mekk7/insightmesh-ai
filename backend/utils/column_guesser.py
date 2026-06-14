# backend/utils/column_guesser.py
"""
Advanced, robust column role detector for InsightMesh.
- Strict heuristics + (optional) semantic matching (MiniLM)
- Guards against selecting date columns as product or sales
- Graceful fallback when sentence-transformers isn't available
- Smarter scoring for product/sales candidates (cardinality & name hints)
"""

import os
import re
import pandas as pd
from dateutil.parser import parse as date_parse
from typing import List, Dict, Optional

# ---------------- Config ----------------
SEMANTIC_THRESHOLD   = float(os.getenv("CG_SEMANTIC_THRESHOLD", "0.60"))
NUMERIC_THRESHOLD    = float(os.getenv("CG_NUMERIC_THRESHOLD", "0.95"))
DATE_PARSE_SAMPLES   = int(os.getenv("CG_DATE_PARSE_SAMPLES", "5"))
USE_SEMANTICS        = os.getenv("CG_USE_SEMANTICS", "1") in {"1", "true", "True"}

ROLE_KEYWORDS = {
    "date": [
        "date", "time", "timestamp", "order_date", "purchase_date",
        "datetime", "created_at", "updated_at", "day", "week", "month", "year"
    ],
    "sales": [
        "sales", "revenue", "amount", "total", "value", "price", "cost",
        "quantity", "units_sold", "qty", "gmv", "turnover", "orders"
    ],
    "product": [
        "product", "item", "sku", "title", "name", "description",
        "category", "brand", "model", "asin", "mpn"
    ],
}

# Lightweight name-hint scoring (cheap, runs before semantics)
NAME_HINTS = {
    "date":    ROLE_KEYWORDS["date"],
    "sales":   ROLE_KEYWORDS["sales"],
    "product": ROLE_KEYWORDS["product"],
}

# Columns that should never be picked as product/sales if avoidable
NEVER_PRODUCT_LIKE = {
    "id", "order_id", "user_id", "customer_id", "transaction_id",
    "uid", "uuid", "guid"
}
NEVER_SALES_LIKE = {"zipcode", "zip", "postal_code"}

# ---------------- Lazy semantic backend ----------------
_embedder = None
_util = None
_kw_cache: Dict[str, "any"] = {}  # role -> keyword embedding tensor

def _maybe_load_semantics():
    """Lazy import + model init; return (embedder, util) or (None, None) if disabled/unavailable."""
    global _embedder, _util
    if not USE_SEMANTICS:
        return None, None
    if _embedder is not None and _util is not None:
        return _embedder, _util
    try:
        from sentence_transformers import SentenceTransformer, util
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
        _util = util
        return _embedder, _util
    except Exception:
        _embedder = None
        _util = None
        return None, None

def _kw_emb(role: str):
    """Encode and cache keyword embeddings per role."""
    emb, _ = _maybe_load_semantics()
    if emb is None:
        return None
    if role not in _kw_cache:
        _kw_cache[role] = emb.encode(ROLE_KEYWORDS[role], convert_to_tensor=True)
    return _kw_cache[role]

# ---------------- Heuristics ----------------
def _try_parse_date(val: str) -> bool:
    try:
        date_parse(val)
        return True
    except Exception:
        return False

def is_date_column(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(DATE_PARSE_SAMPLES)
    if sample.empty:
        return False
    count = sum(1 for v in sample if _try_parse_date(v))
    return (count / len(sample)) >= 0.8

def is_numeric_column(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False
    numerics = pd.to_numeric(non_null, errors="coerce").notnull().sum()
    return (numerics / len(non_null)) >= NUMERIC_THRESHOLD

def _name_hint_score(colname: str, hints: List[str]) -> int:
    """Simple substring score to bias obvious matches (case-insensitive)."""
    name = str(colname).lower()
    return sum(1 for h in hints if h in name)

def _looks_like_id(colname: str) -> bool:
    name = str(colname).strip().lower()
    return name in NEVER_PRODUCT_LIKE or name.endswith("_id")

def _is_boolean(series: pd.Series) -> bool:
    non_null = series.dropna().astype(str).str.strip().str.lower().unique()
    if len(non_null) <= 2 and set(non_null) <= {"0","1","true","false","yes","no"}:
        return True
    return False

def _unique_ratio(series: pd.Series) -> float:
    non_null = series.dropna()
    n = len(non_null)
    return (float(non_null.nunique()) / n) if n else 0.0

def _avg_len(series: pd.Series) -> float:
    try:
        return float(series.dropna().astype(str).str.len().mean() or 0.0)
    except Exception:
        return 0.0

def _likely_identifier(series: pd.Series) -> bool:
    """Heuristic for ID-ish columns even if not named like *_id."""
    ur = _unique_ratio(series)
    if ur >= 0.98:
        return True
    # many long, symbol-heavy tokens → also id-ish
    non_null = series.dropna().astype(str)
    longish = (non_null.str.len() > 18).mean() if len(non_null) else 0.0
    if ur >= 0.9 and longish >= 0.3:
        return True
    return False

# ---------------- Semantics ----------------
def semantic_guess(columns: List[str]) -> Dict[str, Optional[str]]:
    emb, util = _maybe_load_semantics()
    if emb is None:
        return {"date": None, "sales": None, "product": None}

    col_emb = emb.encode(columns, convert_to_tensor=True)
    out: Dict[str, Optional[str]] = {}

    for role in ("date", "sales", "product"):
        kw_emb = _kw_emb(role)
        if kw_emb is None:
            out[role] = None
            continue
        scores = util.cos_sim(col_emb, kw_emb).mean(dim=1)
        best_idx = int(scores.argmax().item())
        out[role] = columns[best_idx]
    return out

def _semantic_confidence(colname: str, role: str) -> float:
    emb, util = _maybe_load_semantics()
    if emb is None:
        return 0.0
    col_vec = emb.encode(colname, convert_to_tensor=True)
    kw_vec = _kw_emb(role)
    if kw_vec is None:
        return 0.0
    sim = util.cos_sim(col_vec, kw_vec).mean().item()
    return float(sim)

# ---------------- Candidate scoring ----------------
def _score_sales(df: pd.DataFrame, col: str) -> float:
    s = df[col]
    if not is_numeric_column(s): return -1.0
    if is_date_column(s): return -1.0
    if _is_boolean(s): return -1.0
    name_score = _name_hint_score(col, NAME_HINTS["sales"])
    # prefer columns with enough distinct values (avoid almost-constant)
    ur = _unique_ratio(s)
    distinct_bonus = 1.0 if ur >= 0.1 else 0.0
    return name_score * 2.0 + distinct_bonus

def _score_product(df: pd.DataFrame, col: str) -> float:
    s = df[col]
    if is_numeric_column(s): return -1.0
    if is_date_column(s): return -1.0
    if _is_boolean(s): return -1.0
    if _looks_like_id(col): return -1.0
    if _likely_identifier(s): return -1.0

    name_score = _name_hint_score(col, NAME_HINTS["product"])
    ur = _unique_ratio(s)
    avglen = _avg_len(s)

    # Prefer medium-to-high cardinality (but not near-unique ID-ish)
    card_score = 0.0
    if 0.15 <= ur <= 0.95:
        card_score = 2.0
    elif 0.05 <= ur < 0.15:
        card_score = 1.0
    elif ur > 0.95:
        card_score = -1.0

    # Avoid extremely short tokens on average (e.g., codes)
    len_penalty = -0.5 if avglen < 3 else 0.0

    return name_score * 2.0 + card_score + len_penalty

# ---------------- Public API ----------------
def guess_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """
    Returns:
      {
        'date': <col>,
        'sales': <col>,
        'product': <col>,
        'diagnostics': { 'heuristic': {...}, 'semantic': {...}, 'final': {...} }
      }
    """
    cols_raw = list(df.columns)
    columns = [str(c) for c in cols_raw]  # normalize
    if not columns:
        return {"date": None, "sales": None, "product": None, "diagnostics": {}}

    # ---------- Heuristic pass ----------
    # 1) Date: any column that parses like dates
    heur_date = next((c for c in columns if is_date_column(df[c])), None)

    # 2) Sales: score numeric candidates, avoid date/boolean/zipcode-like
    sales_candidates = [
        c for c in columns
        if (c not in NEVER_SALES_LIKE)
        and is_numeric_column(df[c])
        and not is_date_column(df[c])
        and not _is_boolean(df[c])
    ]
    if sales_candidates:
        heur_sales = max(sales_candidates, key=lambda c: (_score_sales(df, c), _name_hint_score(c, NAME_HINTS["sales"])))
    else:
        heur_sales = None

    # 3) Product: score text-ish candidates, avoid ids/booleans/dates
    product_candidates = [
        c for c in columns
        if (not is_numeric_column(df[c]))
        and (not is_date_column(df[c]))
        and (not _is_boolean(df[c]))
        and (not _looks_like_id(c))
        and (not _likely_identifier(df[c]))
    ]
    if product_candidates:
        heur_product = max(product_candidates, key=lambda c: (_score_product(df, c), _name_hint_score(c, NAME_HINTS["product"])))
    else:
        heur_product = None

    # Name-hint bias (only if guards pass)
    name_bias_date = max(columns, key=lambda c: _name_hint_score(c, NAME_HINTS["date"]))
    if _name_hint_score(name_bias_date, NAME_HINTS["date"]) > 0 and is_date_column(df[name_bias_date]):
        heur_date = heur_date or name_bias_date

    name_bias_sales = max(columns, key=lambda c: _name_hint_score(c, NAME_HINTS["sales"]))
    if _name_hint_score(name_bias_sales, NAME_HINTS["sales"]) > 0 \
       and is_numeric_column(df[name_bias_sales]) and not is_date_column(df[name_bias_sales]) and not _is_boolean(df[name_bias_sales]):
        heur_sales = heur_sales or name_bias_sales

    name_bias_product = max(columns, key=lambda c: _name_hint_score(c, NAME_HINTS["product"]))
    if _name_hint_score(name_bias_product, NAME_HINTS["product"]) > 0 \
       and (not is_numeric_column(df[name_bias_product])) and (not is_date_column(df[name_bias_product])) \
       and (not _looks_like_id(name_bias_product)) and (not _is_boolean(df[name_bias_product])) \
       and (not _likely_identifier(df[name_bias_product])):
        heur_product = heur_product or name_bias_product

    heur = {"date": heur_date, "sales": heur_sales, "product": heur_product}

    # ---------- Semantic pass (optional) ----------
    sem = semantic_guess(columns)

    final: Dict[str, Optional[str]] = {}
    for role in ("date", "sales", "product"):
        h = heur.get(role)
        s = sem.get(role)

        choice = None
        if h and s and (h == s):
            choice = h
        else:
            # use semantic if confident and doesn't violate guards
            if s:
                conf = _semantic_confidence(s, role)
                if conf >= SEMANTIC_THRESHOLD:
                    choice = s
            # else fall back to heuristic
            if not choice:
                choice = h or s

        # Guardrails
        if choice:
            if role == "product":
                if is_date_column(df[choice]) or _looks_like_id(choice) or _is_boolean(df[choice]) or _likely_identifier(df[choice]):
                    choice = None
            if role == "sales":
                if (not is_numeric_column(df[choice])) or is_date_column(df[choice]) or _is_boolean(df[choice]):
                    choice = None

        final[role] = choice

    return {
        "date": final.get("date"),
        "sales": final.get("sales"),
        "product": final.get("product"),
        "diagnostics": {
            "heuristic": heur,
            "semantic": sem,
            "final": final,
        },
    }
