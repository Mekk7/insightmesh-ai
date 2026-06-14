# backend/utils/file_loader.py
"""
Reserved for future file-loading helpers (multi-format readers).

Currently, CSV/XLSX/TSV loading is done inline in each endpoint that needs it
(see categorize.py::_read_any and understand.py). When we need shared logic
for, say, Parquet/JSON/multi-sheet Excel, put it here.
"""
