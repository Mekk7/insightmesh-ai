# backend/api/endpoints/forecast.py
# Lazy Prophet import (so missing dependency won't crash import),
# automatic role detection, frequency inference, and guarded horizons.

import os
from io import BytesIO
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

from backend.utils.column_guesser import guess_columns

router = APIRouter()


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(n)))


@router.get("/predict/_ping")
def forecast_ping():
    try:
        # Lazy import to report availability
        from prophet import Prophet  # noqa: F401
        available = True
    except Exception:
        available = False
    return {"ok": True, "prophet_available": available}


@router.post("/predict")
def predict_forecast(
    file: UploadFile = File(...),
    periods: int = 30,
    freq: str = "",                 # optional manual frequency override (e.g., "D", "W", "MS")
    return_history: bool = False,   # include last 30 rows of history for plotting context
):
    """
    Upload any CSV of sales data.
    Auto-detect date & sales columns, fit Prophet, and return next `periods`.
    """
    # Lazy import Prophet so the API stays up even if the dep isn't installed
    try:
        from prophet import Prophet
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Prophet is not installed. Please `pip install prophet` on the server to enable forecasting."}
        )

    try:
        # 1) Load CSV robustly
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

        # 2) Save raw input (optional but handy for traceability)
        os.makedirs("backend/data/raw", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(ch for ch in (file.filename or "upload.csv") if ch.isalnum() or ch in ("-","_"))[:80] or "upload.csv"
        raw_path = os.path.join("backend", "data", "raw", f"{os.path.splitext(safe_name)[0]}_{timestamp}.csv")
        with open(raw_path, "wb") as f:
            f.write(raw_bytes)

        # 3) Detect columns and normalize
        roles = guess_columns(df)
        date_col  = roles.get("date")
        sales_col = roles.get("sales")
        if not date_col or not sales_col:
            return JSONResponse(status_code=400, content={"error": "Could not detect date and/or sales column.", "roles": roles})

        df[date_col]  = pd.to_datetime(df[date_col], errors="coerce")
        df[sales_col] = pd.to_numeric(df[sales_col], errors="coerce")

        # Drop NA and aggregate duplicates by date (common in transactional data)
        clean = (
            df[[date_col, sales_col]]
            .dropna()
            .groupby(date_col, as_index=False)[sales_col].sum()
            .sort_values(date_col)
        )

        if clean.shape[0] < 5:
            return JSONResponse(
                status_code=400,
                content={"error": "Not enough cleaned rows to train a forecast (need ≥ 5).", "rows_kept": int(clean.shape[0])}
            )

        prophet_df = clean.rename(columns={date_col: "ds", sales_col: "y"})

        # 4) Fit model
        model = Prophet()  # add seasonality overrides as needed
        model.fit(prophet_df)

        # 5) Infer frequency (or use override)
        inferred = pd.infer_freq(prophet_df["ds"])
        if not inferred:
            # fallback: guess by median diff
            try:
                diffs = prophet_df["ds"].diff().dropna()
                delta = diffs.median()
                inferred = (
                    "D" if delta.days <= 2 else
                    "W" if delta.days <= 10 else
                    "MS" if delta.days <= 45 else
                    "QS" if delta.days <= 120 else
                    "A"
                )
            except Exception:
                inferred = "D"

        use_freq = (freq.strip().upper() or inferred)

        horizon = _clamp(periods, 1, 365)  # safety clamp
        future = model.make_future_dataframe(periods=horizon, freq=use_freq)
        forecast = model.predict(future)

        # 6) Prepare output (only the future horizon)
        fc_future = forecast.tail(horizon).copy()
        fc_future["ds"] = fc_future["ds"].dt.strftime("%Y-%m-%dT%H:%M:%S")
        output = fc_future[["ds", "yhat", "yhat_lower", "yhat_upper"]].to_dict(orient="records")

        payload = {
            "message": "Forecast generated successfully.",
            "guessed_columns": roles,
            "inferred_frequency": inferred,
            "used_frequency": use_freq,
            "forecast_periods": horizon,
            "forecast": output,
            "raw_path": raw_path,
            "train_rows": int(prophet_df.shape[0]),
            "train_start": prophet_df["ds"].iloc[0].strftime("%Y-%m-%dT%H:%M:%S"),
            "train_end": prophet_df["ds"].iloc[-1].strftime("%Y-%m-%dT%H:%M:%S"),
        }

        if return_history:
            tail = forecast.head(len(prophet_df)).tail(30).copy()
            tail["ds"] = tail["ds"].dt.strftime("%Y-%m-%dT%H:%M:%S")
            payload["history_tail"] = tail[["ds", "yhat", "yhat_lower", "yhat_upper"]].to_dict(orient="records")

        return JSONResponse(payload)

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
