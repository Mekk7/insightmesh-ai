# backend/insight/product_intelligence/__init__.py
from backend.insight.product_intelligence.generator import (
    generate_product_intelligence,
    ProductIntelligence,
)

__all__ = ["generate_product_intelligence", "ProductIntelligence"]
