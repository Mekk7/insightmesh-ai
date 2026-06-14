# backend/insight/coverage/__init__.py
from .coverage_map import CoverageMap, DEFAULT_COVERAGE_CONFIG, build_coverage_map

__all__ = ["CoverageMap", "DEFAULT_COVERAGE_CONFIG", "build_coverage_map"]
