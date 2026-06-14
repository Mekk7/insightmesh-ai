# backend/api/endpoints/understand.py
# Robust CSV "understanding": role detection, preview, time window inference,
# optional HTML profiling (ydata_profiling), and deterministic outputs.

import os
from io import BytesIO
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

from backend.utils.column_guesser import guess_columns

# ydata_profiling is heavy; import lazily and fail soft
try:
    from ydata_profiling import ProfileReport  # type: ignore
except Exception:  # pragma: no cover
    ProfileReport = None  # fallback later

router = APIRouter()

# Tunables (env-overridable)
PROFILE_MAX_ROWS = int(os.getenv("PROFILE_MAX_ROWS", "5000"))   # cap rows in HTML profile
GENERATE_PROFILE = os.getenv("GENERATE_PROFILE", "1") in {"1", "true", "True"}  # toggle profiling


def _safe_filename(name: str) -> str:
    root, _ = os.path.splitext(name or "upload.csv")
    root = "".join(ch for ch in root if ch.isalnum() or ch in ("-", "_"))[:80] or "upload"
    return root + ".csv"


def _infer_time_granularity(sorted_series: pd.Series) -> str:
    """Return 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly' (best-effort)."""
    try:
        diffs = sorted_series.diff().dropna().dt.days
        if diffs.empty:
            return "unknown"
        med = float(diffs.median())
        if med <= 2:
            return "daily"
        if med <= 10:
            return "weekly"
        if med <= 45:
            return "monthly"
        if med <= 120:
            return "quarterly"
        return "yearly"
    except Exception:
        return "unknown"


@router.get("/upload/_ping")
def understand_ping():
    return {
        "ok": True,
        "profiling_enabled": GENERATE_PROFILE,
        "profiling_available": ProfileReport is not None,
        "profile_row_cap": PROFILE_MAX_ROWS,
    }


@router.post("/upload")
def understand_upload(file: UploadFile = File(...)):
    try:
        # 1) Load DataFrame (robust decode, no huge memory spikes)
        raw_bytes = file.file.read()
        if not raw_bytes:
            return JSONResponse(status_code=400, content={"error": "Empty file."})

        buf = BytesIO(raw_bytes)
        try:
            df = pd.read_csv(buf, encoding="utf-8", on_bad_lines="skip", low_memory=False)
        except UnicodeDecodeError:
            buf.seek(0)
            df = pd.read_csv(buf, encoding="latin-1", on_bad_lines="skip", low_memory=False)

        if df.empty:
            return JSONResponse(status_code=400, content={"error": "CSV contains no rows."})

        # 2) Save raw CSV (normalized filename)
        os.makedirs("backend/data/raw", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_filename = _safe_filename(file.filename or "upload.csv")
        raw_path = os.path.join("backend", "data", "raw", f"{os.path.splitext(raw_filename)[0]}_{timestamp}.csv")
        # Save the original bytes to preserve exact input
        with open(raw_path, "wb") as f:
            f.write(raw_bytes)

        # 3) Column Role Detection & Normalization
        roles = guess_columns(df)
        date_c  = roles.get("date")
        sales_c = roles.get("sales")
        prod_c  = roles.get("product")

        # Normalize parsed columns if they exist
        if date_c in df.columns:
            df[date_c] = pd.to_datetime(df[date_c], errors="coerce")
        else:
            date_c = None
        if sales_c in df.columns:
            df[sales_c] = pd.to_numeric(df[sales_c], errors="coerce")
        else:
            sales_c = None

        # Ensure product is distinct and valid
        if prod_c not in df.columns or prod_c in {date_c, sales_c}:
            prod_c = next((c for c in df.columns if c not in {date_c, sales_c}), None)

        # 4) Generate profiling report (safe + optional)
        report_path = None
        if GENERATE_PROFILE and ProfileReport is not None:
            os.makedirs("backend/data/processed", exist_ok=True)
            # sample to keep profiling snappy
            sample_df = df.head(PROFILE_MAX_ROWS).copy()
            try:
                profile = ProfileReport(sample_df, title="InsightMesh AI - Data Understanding", explorative=True)
                report_path = os.path.join("backend", "data", "processed", f"profile_{timestamp}.html")
                profile.to_file(report_path)
            except Exception:
                # soft-fail profiling; continue with JSON output
                report_path = None

        # 5) Build metadata summary
        summary = [
            {
                "column": col,
                "dtype": str(df[col].dtype),
                "nulls": int(df[col].isnull().sum()),
                "unique": int(df[col].nunique()),
            }
            for col in df.columns
        ]

        # 6) Time window inference (if date col present)
        time_from = time_to = granularity = None
        if date_c:
            d = df[date_c].dropna().sort_values()
            if not d.empty:
                time_from = d.iloc[0].to_pydatetime().astimezone(timezone.utc).isoformat()
                time_to   = d.iloc[-1].to_pydatetime().astimezone(timezone.utc).isoformat()
                granularity = _infer_time_granularity(d)

        # 7) Safe preview of the 3 key columns (if present)
        preview_cols = [c for c in [date_c, sales_c, prod_c] if c]
        preview_df = df.loc[:, preview_cols].head(5).copy() if preview_cols else pd.DataFrame()
        if date_c and date_c in preview_df.columns and str(preview_df[date_c].dtype).startswith("datetime64"):
            preview_df[date_c] = preview_df[date_c].dt.strftime("%Y-%m-%dT%H:%M:%S")
        preview = preview_df.to_dict(orient="records")

        # 8) Respond
        return JSONResponse({
            "message": "Data processed successfully.",
            "rows": int(df.shape[0]),
            "columns": list(df.columns),
            "column_summary": summary,
            "guessed_columns": {
                "date": date_c, "sales": sales_c, "product": prod_c,
                "diagnostics": roles.get("diagnostics")
            },
            "inferred_time_window": {
                "time_from": time_from,
                "time_to": time_to,
                "granularity": granularity
            },
            "preview": preview,
            "report_path": report_path,
            "raw_path": raw_path,
            "profiling": {
                "enabled": GENERATE_PROFILE,
                "available": ProfileReport is not None
            }
        })

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
