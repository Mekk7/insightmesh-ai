"""
Product Intelligence Generator — teaches the system what a product IS.

THE PROBLEM
-----------
When you search "PS5", YouTube returns comments about the console AND about
games, services, accessories, console wars, and memes. The model doesn't know
which comments are about the PS5 hardware vs. GTA 6 bugs vs. PlayStation Plus
pricing. Everything gets blended into one score, so game bugs drag down the
console rating. That's like blaming the TV because a movie was bad.

THE SOLUTION
------------
ONE LLM call at the start of the pipeline generates a structured "product
intelligence" that tells every downstream layer what this product IS:

  - Category + segment (gaming console, $500 premium)
  - Direct aspects (hardware, controller, UI, loading, noise, build, price)
  - Ecosystem aspects (games, PSN, PS Plus — related but scored separately)
  - Peripheral aspects (headset compatibility, TV requirements)
  - Expected price tier (sets the expectation anchor for gap analysis)
  - Known variants/versions (PS5 Original, Digital, Slim, Pro)
  - Key competitors (Xbox Series X, Nintendo Switch)
  - Lifecycle cues to watch for (durability after X months, battery degradation)

This context is then passed to the deep classifier, which uses it to:
  - Score each review as DIRECT / ECOSYSTEM / TANGENTIAL / OFF_TOPIC
  - Detect expectation gaps anchored to the price tier
  - Identify version/variant references
  - Map causal chains within the product's domain
  - Understand competitive migration in context

CACHING
-------
The ontology is cached per product name (lowercased, stripped). A PS5 search
generates the ontology once; subsequent runs reuse it. The cache is in-memory
(dies on restart) which is fine — regeneration is one fast LLM call.

DESIGN
------
- One LLM call, ~300 tokens out. Fastest step in the pipeline.
- Never raises. Returns a minimal fallback ontology on any failure.
- Pure data in / data out. No side effects.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

log = logging.getLogger("insightmesh.product_intelligence")

# In-memory cache: normalized product name -> ProductIntelligence
_cache: Dict[str, "ProductIntelligence"] = {}


@dataclass
class ProductIntelligence:
    """Structured understanding of what the product IS."""
    product: str = ""
    category: str = ""                           # "gaming console", "over-ear headphones"
    segment: str = ""                            # "premium", "budget", "mid-range"
    price_tier: str = ""                         # "$400-500", "free", etc.
    direct_aspects: List[str] = field(default_factory=list)    # hardware, build, battery...
    ecosystem_aspects: List[str] = field(default_factory=list) # games, services, app store...
    peripheral_aspects: List[str] = field(default_factory=list)# accessories, cables, cases...
    not_relevant: List[str] = field(default_factory=list)      # console wars, memes, unrelated news
    known_versions: List[str] = field(default_factory=list)    # PS5 Original, PS5 Slim, PS5 Pro
    key_competitors: List[str] = field(default_factory=list)   # Xbox Series X, Nintendo Switch
    lifecycle_cues: List[str] = field(default_factory=list)    # "check durability after 6mo"
    expectation_anchors: List[str] = field(default_factory=list) # "at $500, buyers expect premium build"
    # The EXPECTED FEEDBACK MAP: categories of feedback a product like this COULD
    # receive (PS5 → controller, electrical/power, build, heating/cooling, storage,
    # disc drive, software/UI, connectivity, fan noise). A STARTING expectation for
    # the coverage-driven scraper, NOT a fixed checklist — the map grows as comments
    # reveal new issues.
    expected_feedback_categories: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_classifier_context(self) -> str:
        """Compact string for injection into the deep classifier prompt."""
        lines = [
            f"PRODUCT: {self.product}",
            f"CATEGORY: {self.category} ({self.segment}, {self.price_tier})",
            f"DIRECT ASPECTS (about the product itself): {', '.join(self.direct_aspects)}",
        ]
        if self.ecosystem_aspects:
            lines.append(f"ECOSYSTEM (related but separate — don't count against product): {', '.join(self.ecosystem_aspects)}")
        if self.not_relevant:
            lines.append(f"NOT RELEVANT (filter out): {', '.join(self.not_relevant)}")
        if self.known_versions:
            lines.append(f"KNOWN VERSIONS: {', '.join(self.known_versions)}")
        if self.key_competitors:
            lines.append(f"KEY COMPETITORS: {', '.join(self.key_competitors)}")
        if self.expectation_anchors:
            lines.append(f"BUYER EXPECTATIONS: {', '.join(self.expectation_anchors)}")
        if self.expected_feedback_categories:
            lines.append(f"EXPECTED FEEDBACK CATEGORIES (map an insight onto one of these when it fits): {', '.join(self.expected_feedback_categories)}")
        return "\n".join(lines)


def _normalize_key(product: str) -> str:
    return (product or "").strip().lower()


def _parse_response(raw: Any, product: str) -> Optional[ProductIntelligence]:
    """Parse the LLM's JSON response into a ProductIntelligence."""
    if not isinstance(raw, dict):
        return None
    try:
        def _list(key):
            v = raw.get(key)
            if isinstance(v, list):
                return [str(x).strip() for x in v if x]
            if isinstance(v, str):
                return [s.strip() for s in v.split(",") if s.strip()]
            return []

        return ProductIntelligence(
            product=product,
            category=str(raw.get("category") or "").strip(),
            segment=str(raw.get("segment") or "general").strip().lower(),
            price_tier=str(raw.get("price_tier") or "").strip(),
            direct_aspects=_list("direct_aspects"),
            ecosystem_aspects=_list("ecosystem_aspects"),
            peripheral_aspects=_list("peripheral_aspects"),
            not_relevant=_list("not_relevant"),
            known_versions=_list("known_versions"),
            key_competitors=_list("key_competitors"),
            lifecycle_cues=_list("lifecycle_cues"),
            expectation_anchors=_list("expectation_anchors"),
            expected_feedback_categories=_list("expected_feedback_categories"),
        )
    except Exception as e:
        log.warning("[product_intelligence] parse failed: %s", e)
        return None


def _fallback(product: str) -> ProductIntelligence:
    """Minimal fallback when LLM is unavailable. Better than nothing."""
    return ProductIntelligence(
        product=product,
        category="consumer product",
        segment="general",
        direct_aspects=["quality", "price", "design", "performance", "durability"],
        not_relevant=["memes", "unrelated news", "off-topic arguments"],
        # Generic starting feedback map so coverage still works without an LLM.
        expected_feedback_categories=[
            "build quality", "performance", "reliability", "value for money",
            "design", "ease of use", "customer support",
        ],
    )


def generate_product_intelligence(product: str, llm_client=None) -> ProductIntelligence:
    """
    Generate or retrieve cached product intelligence. One LLM call per product.
    Never raises — returns a usable fallback on any failure.
    """
    key = _normalize_key(product)
    if not key:
        return _fallback(product)

    # Check cache
    if key in _cache:
        return _cache[key]

    # No LLM? Return fallback
    if llm_client is None:
        result = _fallback(product)
        _cache[key] = result
        return result

    try:
        if llm_client.available_backend() == "none":
            result = _fallback(product)
            _cache[key] = result
            return result
    except Exception:
        result = _fallback(product)
        _cache[key] = result
        return result

    prompt = f"""You are a product analyst. Given a product name, generate a structured understanding
of what this product IS, so a review analysis system can separate reviews ABOUT the product
from reviews about its ecosystem, accessories, or unrelated topics.

PRODUCT: {product}

Return ONLY this JSON (no markdown):
{{
  "category": "what type of product this is (e.g. 'gaming console', 'over-ear headphones', 'electric SUV')",
  "segment": "premium | mid-range | budget | free",
  "price_tier": "approximate price range (e.g. '$400-500', '$30,000-50,000')",
  "direct_aspects": ["aspects that are ABOUT the product itself — score these as product quality"],
  "ecosystem_aspects": ["things that run ON or WITH the product but aren't the product itself — score separately"],
  "peripheral_aspects": ["accessories, add-ons, companion items"],
  "not_relevant": ["types of comments that are NOT reviews — filter these out"],
  "known_versions": ["known variants or model versions of this product"],
  "key_competitors": ["direct competing products"],
  "lifecycle_cues": ["what to watch for over time with this type of product"],
  "expectation_anchors": ["what buyers at this price point typically expect"],
  "expected_feedback_categories": ["the CATEGORIES of feedback a product like this could receive — physical, electrical, software, and experiential failure/praise areas"]
}}

Be specific to THIS product. For a PS5: games are ecosystem, not direct. For headphones: music app quality is ecosystem, not direct. For a car: gas prices are ecosystem, not direct.

For "expected_feedback_categories", think like a reviewer mapping out what could go right or wrong — 6 to 12 concrete categories. Examples:
- PS5 → ["controller", "electrical/power", "build/physical structure", "heating/cooling", "storage", "disc drive", "software/UI", "connectivity", "fan noise"]
- over-ear headphones → ["drivers/sound", "battery", "comfort", "ANC", "build", "companion app", "connectivity", "mic"]
- electric SUV → ["range", "charging", "build quality", "interior", "autopilot/driver-assist", "software/infotainment", "service/support", "ride/handling"]
These are a STARTING expectation, not exhaustive — pick the categories most relevant to THIS product."""

    try:
        raw = llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        result = _parse_response(raw, product)
        if result and result.category:
            log.info("[product_intelligence] generated for '%s': %s (%s, %s)",
                     product, result.category, result.segment, result.price_tier)
            _cache[key] = result
            return result
    except Exception as e:
        log.warning("[product_intelligence] LLM call failed: %s", e)

    result = _fallback(product)
    _cache[key] = result
    return result
