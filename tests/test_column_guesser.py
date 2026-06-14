# tests/test_column_guesser.py
"""Tests for backend/utils/column_guesser.py — auto role detection on CSVs."""
from __future__ import annotations

import os

import pandas as pd
import pytest

# Disable semantic backend by default — purely heuristic tests are fast and
# don't require downloading sentence-transformers in CI.
os.environ.setdefault("CG_USE_SEMANTICS", "0")

from backend.utils.column_guesser import (  # noqa: E402  (env must be set first)
    _is_boolean,
    _likely_identifier,
    _looks_like_id,
    _unique_ratio,
    guess_columns,
    is_date_column,
    is_numeric_column,
)


class TestPrimitives:
    def test_is_date_column_iso(self):
        s = pd.Series(["2024-01-01", "2024-02-15", "2024-03-20", "2024-04-10", "2024-05-05"])
        assert is_date_column(s)

    def test_is_date_column_us_format(self):
        s = pd.Series(["01/15/2024", "02/20/2024", "03/25/2024", "04/30/2024", "05/05/2024"])
        assert is_date_column(s)

    def test_is_date_column_rejects_words(self):
        s = pd.Series(["red", "blue", "green", "yellow", "purple"])
        assert not is_date_column(s)

    def test_is_numeric_column(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert is_numeric_column(s)

    def test_is_numeric_column_string_numbers(self):
        s = pd.Series(["100", "200", "300", "400"])
        assert is_numeric_column(s)

    def test_is_numeric_column_rejects_strings(self):
        s = pd.Series(["apple", "banana", "cherry"])
        assert not is_numeric_column(s)

    def test_is_boolean(self):
        assert _is_boolean(pd.Series([True, False, True]))
        assert _is_boolean(pd.Series(["yes", "no", "yes"]))
        assert _is_boolean(pd.Series([0, 1, 0, 1]))
        assert not _is_boolean(pd.Series([1, 2, 3, 4, 5]))

    def test_looks_like_id(self):
        assert _looks_like_id("user_id")
        assert _looks_like_id("order_id")
        assert _looks_like_id("uuid")
        assert not _looks_like_id("product")

    def test_likely_identifier_by_uniqueness(self):
        # 100% unique → looks ID-ish
        s = pd.Series([f"row_{i}" for i in range(100)])
        assert _likely_identifier(s)
        # repeating values → not an identifier
        s2 = pd.Series(["A"] * 50 + ["B"] * 50)
        assert not _likely_identifier(s2)


class TestGuessColumns:
    def test_canonical_sales_csv(self):
        df = pd.DataFrame({
            "date":     pd.date_range("2024-01-01", periods=10).strftime("%Y-%m-%d"),
            "product":  ["Model Y"] * 5 + ["Model 3"] * 5,
            "revenue":  [100, 200, 150, 300, 250, 400, 350, 500, 450, 600],
        })
        roles = guess_columns(df)
        assert roles["date"] == "date"
        assert roles["sales"] == "revenue"
        assert roles["product"] == "product"

    def test_alternative_column_names(self):
        df = pd.DataFrame({
            "order_date": pd.date_range("2024-01-01", periods=8).strftime("%Y-%m-%d"),
            "item_name":  ["A", "B", "C", "D", "A", "B", "C", "D"],
            "total_amount": [10, 20, 30, 40, 50, 60, 70, 80],
        })
        roles = guess_columns(df)
        assert roles["date"] == "order_date"
        assert roles["sales"] == "total_amount"
        assert roles["product"] == "item_name"

    def test_avoids_id_as_product(self):
        df = pd.DataFrame({
            "order_id": [f"ord_{i}" for i in range(20)],
            "product":  ["A"] * 10 + ["B"] * 10,
            "amount":   range(20),
            "date":     pd.date_range("2024-01-01", periods=20).strftime("%Y-%m-%d"),
        })
        roles = guess_columns(df)
        assert roles["product"] == "product"

    def test_no_date_column(self):
        df = pd.DataFrame({
            "product": ["A", "B", "C"],
            "amount":  [1, 2, 3],
        })
        roles = guess_columns(df)
        assert roles["date"] is None

    def test_no_sales_column(self):
        df = pd.DataFrame({
            "date":    pd.date_range("2024-01-01", periods=5).strftime("%Y-%m-%d"),
            "product": ["X", "Y", "Z", "X", "Y"],
        })
        roles = guess_columns(df)
        assert roles["sales"] is None
        # date and product should still be found
        assert roles["date"] == "date"
        assert roles["product"] == "product"

    def test_empty_dataframe_returns_nones(self):
        df = pd.DataFrame()
        roles = guess_columns(df)
        assert roles == {"date": None, "sales": None, "product": None, "diagnostics": {}}

    def test_diagnostics_present(self):
        df = pd.DataFrame({
            "date":    pd.date_range("2024-01-01", periods=5).strftime("%Y-%m-%d"),
            "product": ["X"] * 3 + ["Y"] * 2,
            "amount":  [1, 2, 3, 4, 5],
        })
        roles = guess_columns(df)
        assert "diagnostics" in roles
        diag = roles["diagnostics"]
        assert "heuristic" in diag
        assert "final" in diag
