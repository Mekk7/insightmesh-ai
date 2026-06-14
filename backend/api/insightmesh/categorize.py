# backend/api/insightmesh/categorize.py (hardened: lazy OpenAI, robust pathing, tolerant to missing sales)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
import pandas as pd
from transformers import pipeline
from openai import OpenAI
import json
import os
from dotenv import load_dotenv

# ——— 1) Env (no hard fail at import) ———
load_dotenv()
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_llm: Optional[OpenAI] = OpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

# Zero-shot classifier for categories
cat_pipe = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")
CANDIDATE_CATS = [
    "Automotive", "Electronics", "Software", "Home", "Fashion", "Beauty", "Food", "Travel"
]

# ——— 2) Schemas ———
class CategorizeInput(BaseModel):
    filepath: str
    # Pydantic v2: silently ignore extra fields so callers can pass through
    # legacy keys without breaking. (Was `class Config: extra = Extra.ignore` in v1.)
    model_config = ConfigDict(extra="ignore")

class CategorizeOutput(BaseModel):
    top_products: List[str]
    categories:   List[str]
    search_tags:  List[str]

# ——— 3) Helpers ———
def _project_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    backend_dir = os.path.abspath(os.path.join(here, "..", ".."))          # backend/api
    backend_dir = os.path.abspath(os.path.join(backend_dir, ".."))         # backend
    project_root = os.path.abspath(os.path.join(backend_dir, ".."))        # project root
    return project_root

def _resolve_path(filepath: str) -> str:
    if os.path.isabs(filepath):
        return filepath
    root = _project_root()
    backend_dir = os.path.join(root, "backend")
    candidates = [
        os.path.join(root, filepath),
        os.path.join(backend_dir, filepath),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

def _read_any(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        if ext in {".tsv"}:
            return pd.read_csv(path, sep="\t")
        # fallback: let pandas guess, robust to mixed separators
        return pd.read_csv(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read dataset at '{path}': {e}")

def _is_date_like(s: pd.Series) -> bool:
    try:
        parsed = pd.to_datetime(s, errors="coerce")
        return parsed.notna().mean() >= 0.8
    except Exception:
        return False

def _sanitize_products(values: List[str]) -> List[str]:
    # Drop date-like strings, trim, unique while preserving order
    seen = set()
    clean = []
    for v in values:
        t = str(v).strip()
        if not t:
            continue
        try:
            parsed = pd.to_datetime([t], errors="coerce")
            if parsed.notna().all():
                continue
        except Exception:
            pass
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(t)
    return clean

def _make_search_terms(items: List[str], limit: int = 5) -> List[str]:
    """
    Produce lowercase search-friendly terms (no '#', no punctuation).
    """
    tags: List[str] = []
    for x in items:
        t = str(x).strip().lower()
        t = t.replace("&", " and ")
        term = " ".join(ch for ch in re.sub(r"[^a-z0-9\- ]+", " ", t).split())  # keep words/dashes
        if term and term not in tags:
            tags.append(term[:40])
        if len(tags) >= limit:
            break
    return tags

import re

# ——— 4) Service ———
def categorize_service(filepath: str) -> dict:
    path = _resolve_path(filepath)
    df = _read_any(path)

    if df.empty:
        raise HTTPException(status_code=400, detail="Dataset is empty.")

    # column guessing
    from backend.utils.column_guesser import guess_columns
    roles = guess_columns(df)
    prod_col = roles.get("product")
    sales_col = roles.get("sales")

    if not prod_col or prod_col not in df.columns:
        raise HTTPException(status_code=400, detail=f"Could not infer a product column. Detected roles: {roles}")

    if _is_date_like(df[prod_col]):
        raise HTTPException(
            status_code=400,
            detail=f"Detected product column '{prod_col}' appears to be a date. Please rename columns or adjust data."
        )

    # Compute top products by sales if we have a valid numeric sales column; else by frequency
    top_products_raw: List[str] = []
    if sales_col and sales_col in df.columns:
        df[sales_col] = pd.to_numeric(df[sales_col], errors="coerce")
        if df[sales_col].notna().any():
            top_products_raw = (
                df.groupby(prod_col)[sales_col]
                  .sum(min_count=1)
                  .sort_values(ascending=False)
                  .head(5)
                  .index.astype(str)
                  .tolist()
            )
        else:
            sales_col = None  # invalid numeric -> fallback to frequency

    if not top_products_raw:
        top_products_raw = (
            df[prod_col].astype(str)
              .value_counts(dropna=True)
              .head(5)
              .index
              .tolist()
        )

    top_products = _sanitize_products(top_products_raw)[:3]

    # Categories via zero-shot over product names (robust to empty list)
    basis = ", ".join(top_products) if top_products else str(prod_col)
    try:
        zs = cat_pipe(basis, CANDIDATE_CATS, multi_label=True)
        categories = [lab for lab, sc in zip(zs["labels"], zs["scores"]) if sc >= 0.35][:5]
    except Exception:
        categories = []

    # Search terms: products + categories; LLM can top up if too few and key is present
    search_terms = _make_search_terms(top_products + categories, limit=5)

    if len(search_terms) < 3 and _llm is not None:
        prompt = (
            f"Products: {top_products}. Categories: {categories}. "
            f"Return 5 concise, platform-friendly search terms as a JSON array of strings, lowercase, no punctuation."
        )
        try:
            resp = _llm.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            text = (resp.choices[0].message.content or "").strip().strip("`\n ")
            llm_terms = json.loads(text)
            if isinstance(llm_terms, list):
                llm_terms = _make_search_terms([str(x) for x in llm_terms], limit=5)
                # keep uniqueness while preserving order
                seen = set()
                merged = []
                for t in (search_terms + llm_terms):
                    if t not in seen:
                        seen.add(t); merged.append(t)
                search_terms = merged[:5]
        except Exception:
            pass

    return {
        "top_products": top_products,
        "categories": categories,
        "search_tags": search_terms  # plain terms (no '#') → better for our query builders
    }

# ——— 5) Router ———
router = APIRouter()

@router.get("/categorize/_ping")
def categorize_ping():
    return {
        "ok": True,
        "openai_configured": bool(_OPENAI_API_KEY),
    }

@router.post(
    "/categorize",
    response_model=CategorizeOutput,
    summary="Categorize & Tag Products (clean, tolerant to missing sales)"
)
async def categorize_endpoint(payload: CategorizeInput):
    return categorize_service(payload.filepath)
