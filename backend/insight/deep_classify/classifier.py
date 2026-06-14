"""
Deep Review Classifier — the unified "smart classification" layer.

Extracts signals from every review in a single batched LLM call. The headline
signal is PRODUCT_INSIGHT — not "is this comment relevant?" (filtering) but
"what does this comment tell us about the PRODUCT?" (understanding).

0. PRODUCT INSIGHT   — what this comment reveals about the product itself, even
                       when the comment is superficially about something else.
                       "GTA runs at 60fps on PS5" → reveals strong PS5 performance.
                       "GTA is haram" → reveals nothing about the PS5 → null.
                       This is the intelligence that feeds the debate, ask Q&A,
                       and dashboard. Relevance is a SIDE EFFECT of it, not the goal.
1. RELEVANCE TIER    — direct / ecosystem / tangential / off_topic
2. MULTI-INTENT      — every intent in one comment (praise + complaint + suggestion)
3. CONDITIONS        — "great IF you don't use in rain" → conditional sentiment
4. EXPERIENCE STAGE  — first impression vs 6-month owner vs expert repeat buyer
5. SWITCHING         — "returned my Bose for these" → competitive migration
6. CLAIMS vs OPINIONS — "battery lasts 30hr" (verifiable) vs "sounds amazing" (opinion)
7. EXPECTATION GAP   — exceeded / met / fell short of expectations
8. VERSION MENTIONED — specific model/variant referenced
9. CAUSAL CHAIN      — root cause → effect → consequence chains

Accepts optional ProductIntelligence context so it knows what the product IS
(PS5 = console, headphones reviews ≠ game reviews). The product context is what
lets the model decide whether an ecosystem comment carries product intelligence.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger("insightmesh.deep_classify")

# How many reviews per LLM call. Now configurable (env DEEP_CLASSIFY_BATCH_SIZE),
# default 8. CAVEAT for small LOCAL models (qwen2.5:7b on Ollama): at 8 the JSON array
# can truncate and whole batches come back empty — if you see empty classifications on
# Ollama, set DEEP_CLASSIFY_BATCH_SIZE=3. Hosted models (gpt-4o-mini) handle 8+ fine.
BATCH_SIZE = max(1, int(os.getenv("DEEP_CLASSIFY_BATCH_SIZE", "8")))


def _build_batch_prompt(reviews: List[Dict[str, Any]], product_context: str = "") -> str:
    review_block = []
    for i, r in enumerate(reviews):
        text = (r.get("translated_text") or r.get("original") or "").strip().replace("\n", " ")
        stars = r.get("sentiment") or "unknown"
        review_block.append(f'R{i+1} ({stars}): "{text[:500]}"')
    reviews_text = "\n".join(review_block)

    ctx = ""
    if product_context:
        ctx = f"\nPRODUCT CONTEXT — use this to map comments onto the product:\n{product_context}\n"

    n = len(reviews)
    return f"""You are a product-intelligence analyst. You will receive {n} numbered reviews (R1..R{n}).
For EACH review, analyze ONLY that review's own words and output one JSON object.
Return a JSON object {{"reviews": [ ... ]}} whose "reviews" array holds exactly {n} objects, one per
review, in order R1..R{n}. You MUST output all {n} — do not stop after the first. Never invent text
that is not in the review.
{ctx}
YOUR PRIMARY JOB is the "product_insight" field: what does THIS comment tell us about the PRODUCT itself?
A comment can be superficially about a game/app/song/accessory and STILL reveal real intelligence about
the product's performance, hardware, build, thermals, value, or demand. Mine that. Worked examples
(product = PS5 console):
  - "GTA runs at 60fps on PS5"              -> reveals_product_info=true, aspect=performance, insight="PS5 sustains 60fps in demanding games", sentiment=positive, evidence_type=ecosystem
  - "Controller haptics make GTA incredible" -> true, aspect=controller, insight="DualSense haptics noticeably elevate gameplay", positive, ecosystem
  - "Bought a PS5 just for Spider-Man"       -> true, aspect=demand, insight="exclusive titles drive console purchases", positive, ecosystem
  - "Console roars and gets hot in Elden Ring" -> true, aspect=thermals, insight="PS5 runs hot and loud under heavy load", negative, ecosystem
  - "Load times dropped from 40s to 2s"      -> true, aspect=load_times, insight="SSD delivers near-instant loads", positive, direct
  - "GTA has a great story"                  -> reveals_product_info=false (about the game's content, not the console)
  - "GTA is haram"  /  "first" / "who's watching in 2026" -> reveals_product_info=false (noise)
Rule: if the comment's praise/complaint is about the GAME/CONTENT itself (story, characters, morality)
rather than how the PRODUCT runs/feels/lasts/sells, set reveals_product_info=false.

RELEVANCE TIER DEFINITIONS (follow strictly):
- "direct"      = Comment is ABOUT the product — its features, quality, price, design, experience, or purchase decision. ANY opinion, complaint, praise, or question about owning/using the product is "direct".
- "ecosystem"   = Comment is about a related app/game/accessory/competitor BUT reveals something about the product (performance, compatibility, demand, hardware behavior). Keep these — they carry intelligence.
- "tangential"  = Loosely related to the product category but reveals NOTHING specific about this product. Example: generic industry commentary, unrelated brand discussion.
- "off_topic"   = Completely unrelated noise: spam, memes, "first!", "who's watching in 2026", self-promotion, arguments between commenters about politics/religion, or content that has zero connection to the product.

CRITICAL BIAS: When in doubt, classify UP (off_topic→tangential, tangential→ecosystem, ecosystem→direct). A comment like "too expensive" or "not worth it" or "love mine" IS direct — it's a product opinion. A comment discussing price, availability, competition, use cases, or the purchase decision IS direct. Only use off_topic for genuine noise with NO product signal at all.

OUTPUT — for each review, an object with these fields (use null or [] when a signal is absent):
- "id": "R1".."R{n}"
- "product_insight": {{"reveals_product_info": true/false, "aspect": "...", "insight": "standalone fact about the product", "sentiment": "positive|negative|neutral|mixed", "confidence": 0..1, "evidence_type": "direct|ecosystem|peripheral", "quote": "exact phrase from the review"}}
- "relevance_tier": "direct|ecosystem|tangential|off_topic"
- "intents": [{{"type": "praise|complaint|suggestion|question|comparison", "aspect": "...", "quote": "..."}}]
- "conditions": [{{"condition": "...", "sentiment_flips": true/false, "quote": "..."}}]
- "experience_stage": {{"stage": "first_impression|short_term|long_term|expert", "signal": "..."}}
- "switching": {{"from_product": "...", "to_product": "...", "reason": "...", "direction": "..."}} or null
- "claims": [{{"text": "...", "type": "claim|opinion", "aspect": "..."}}]
- "expectation_gap": {{"direction": "exceeded|met|fell_short", "expected": "...", "reality": "..."}} or null
- "version_mentioned": "..." or null
- "causal_chain": ["cause", "effect", ...] or []

After product_insight, do NOT skip the other fields when they are clearly present:
"switching" whenever the reviewer came from / moved to another product ("returned my X for this"),
"conditions" for any IF/BUT/UNLESS that flips sentiment, "expectation_gap" when they compare what they
expected vs got, and "intents" for each praise/complaint/suggestion. Use null/[] only when truly absent.

Here are the {n} reviews to analyze:
{reviews_text}

Now return ONLY {{"reviews": [...]}} with all {n} objects (R1..R{n}), grounded strictly in each review's wording."""


def _parse_batch_response(raw: Any, batch_size: int) -> List[Optional[Dict[str, Any]]]:
    # Small models sometimes wrap the array in {"reviews":[...]} or return a single
    # object instead of an array. Normalize all of these to a list before parsing.
    if isinstance(raw, dict):
        for k in ("reviews", "results", "items", "data"):
            if isinstance(raw.get(k), list):
                raw = raw[k]
                break
        else:
            raw = [raw] if raw.get("id") else []
    if not isinstance(raw, list):
        return [None] * batch_size
    result = [None] * batch_size
    for item in raw:
        if not isinstance(item, dict):
            continue
        rid = item.get("id") or ""
        try:
            idx = int(str(rid).replace("R", "").replace("r", "")) - 1
        except (ValueError, TypeError):
            continue
        if idx < 0 or idx >= batch_size:
            continue
        deep = {}

        # 0. Relevance tier
        tier = str(item.get("relevance_tier") or "").lower().strip()
        if tier in ("direct", "ecosystem", "tangential", "off_topic"):
            deep["relevance_tier"] = tier

        # 0b. Product insight — the core intelligence: what this comment reveals
        #     about the PRODUCT, even when it's superficially about something else.
        pi = item.get("product_insight")
        if isinstance(pi, dict) and pi.get("reveals_product_info") and str(pi.get("insight") or "").strip():
            sent = str(pi.get("sentiment") or "neutral").lower().strip()
            if sent not in ("positive", "negative", "neutral", "mixed"):
                sent = "neutral"
            etype = str(pi.get("evidence_type") or "").lower().strip()
            if etype not in ("direct", "ecosystem", "peripheral"):
                # fall back to the relevance tier, defaulting to direct
                etype = tier if tier in ("direct", "ecosystem") else "direct"
            try:
                conf = max(0.0, min(1.0, float(pi.get("confidence"))))
            except (TypeError, ValueError):
                conf = 0.5
            deep["product_insight"] = {
                "aspect": str(pi.get("aspect") or "general").lower().strip()[:40] or "general",
                "insight": str(pi["insight"]).strip()[:240],
                "sentiment": sent,
                "confidence": conf,
                "evidence_type": etype,
                "quote": str(pi.get("quote") or "").strip()[:200],
            }

        # 1. Multi-intent
        intents = item.get("intents")
        if isinstance(intents, list):
            clean = [{"type": str(i["type"]).lower().strip(), "aspect": str(i["aspect"]).lower().strip(), "quote": str(i.get("quote") or "").strip()[:200]}
                     for i in intents if isinstance(i, dict) and i.get("type") and i.get("aspect")]
            if clean:
                deep["intents"] = clean

        # 2. Conditions
        conditions = item.get("conditions")
        if isinstance(conditions, list):
            clean = [{"condition": str(c["condition"]).strip()[:100], "sentiment_flips": bool(c.get("sentiment_flips")), "quote": str(c.get("quote") or "").strip()[:200]}
                     for c in conditions if isinstance(c, dict) and c.get("condition")]
            if clean:
                deep["conditions"] = clean

        # 3. Experience stage
        stage = item.get("experience_stage")
        if isinstance(stage, dict) and stage.get("stage"):
            s = str(stage["stage"]).lower().strip()
            valid = {"first_impression", "short_term", "long_term", "expert"}
            if s in valid:
                deep["experience_stage"] = {
                    "stage": s,
                    "signal": str(stage.get("signal") or "").strip()[:100],
                    "weight": {"expert": 1.0, "long_term": 0.85, "short_term": 0.6, "first_impression": 0.4}[s],
                }

        # 4. Switching
        switching = item.get("switching")
        if isinstance(switching, dict) and (switching.get("from_product") or switching.get("to_product")):
            deep["switching"] = {
                "from_product": str(switching["from_product"]).strip()[:60] if switching.get("from_product") else None,
                "to_product": str(switching["to_product"]).strip()[:60] if switching.get("to_product") else None,
                "reason": str(switching.get("reason") or "").strip()[:120],
                "direction": str(switching.get("direction") or "comparing").lower().strip(),
            }

        # 5. Claims
        claims = item.get("claims")
        if isinstance(claims, list):
            clean = []
            for cl in claims:
                if isinstance(cl, dict) and cl.get("text"):
                    ctype = str(cl.get("type") or "opinion").lower().strip()
                    if ctype not in ("claim", "opinion"):
                        ctype = "opinion"
                    clean.append({"text": str(cl["text"]).strip()[:200], "type": ctype, "aspect": str(cl.get("aspect") or "general").lower().strip()})
            if clean:
                deep["claims"] = clean

        # 6. Expectation gap
        gap = item.get("expectation_gap")
        if isinstance(gap, dict) and gap.get("direction"):
            d = str(gap["direction"]).lower().strip()
            if d in ("exceeded", "met", "fell_short"):
                deep["expectation_gap"] = {
                    "direction": d,
                    "expected": str(gap.get("expected") or "").strip()[:150],
                    "reality": str(gap.get("reality") or "").strip()[:150],
                }

        # 7. Version mentioned
        ver = item.get("version_mentioned")
        if ver and str(ver).strip().lower() not in ("null", "none", ""):
            deep["version_mentioned"] = str(ver).strip()[:60]

        # 8. Causal chain
        chain = item.get("causal_chain")
        if isinstance(chain, list) and len(chain) >= 2:
            deep["causal_chain"] = [str(s).strip()[:100] for s in chain if s][:6]

        result[idx] = deep if deep else None
    return result


def deep_classify_reviews(per_review: List[Dict[str, Any]], llm_client=None, product_intel=None) -> List[Dict[str, Any]]:
    """Run deep classification. Accepts optional ProductIntelligence for domain context."""
    if llm_client is None:
        return per_review
    try:
        if llm_client.available_backend() == "none":
            log.info("[deep_classify] no LLM available, skipping")
            return per_review
    except Exception:
        return per_review

    # Build product context string for the prompt
    product_context = ""
    if product_intel is not None:
        try:
            product_context = product_intel.to_classifier_context()
        except Exception:
            pass

    classifiable = [(i, r) for i, r in enumerate(per_review)
                    if len((r.get("translated_text") or r.get("original") or "").strip()) >= 15
                    and r.get("is_relevant") is not False]
    if not classifiable:
        return per_review

    log.info("[deep_classify] classifying %d reviews in batches of %d (product context: %s)",
             len(classifiable), BATCH_SIZE, "yes" if product_context else "no")

    for batch_start in range(0, len(classifiable), BATCH_SIZE):
        batch = classifiable[batch_start:batch_start + BATCH_SIZE]
        prompt = _build_batch_prompt([r for _, r in batch], product_context)
        try:
            raw = llm_client.chat_json([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=max(2500, BATCH_SIZE * 350))
            results = _parse_batch_response(raw, len(batch))
        except Exception as e:
            log.warning("[deep_classify] batch %d failed: %s", batch_start, e)
            results = [None] * len(batch)
        for j, (orig_idx, _) in enumerate(batch):
            if j < len(results) and results[j]:
                per_review[orig_idx]["deep"] = results[j]

    classified = sum(1 for r in per_review if r.get("deep"))
    log.info("[deep_classify] done: %d/%d reviews classified", classified, len(per_review))
    return per_review


def aggregate_deep_signals(per_review: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate all deep signals across reviews into summary stats."""
    all_intents, all_conditions, all_claims, all_opinions = [], [], [], []
    all_expectation_gaps = []
    all_causal_chains = []
    all_product_insights = []
    version_mentions = {}
    relevance_counts = {"direct": 0, "ecosystem": 0, "tangential": 0, "off_topic": 0}
    switching_from, switching_to = {}, {}
    stage_counts = {"first_impression": 0, "short_term": 0, "long_term": 0, "expert": 0}

    for r in per_review:
        deep = r.get("deep")
        if not deep:
            continue

        # Relevance
        tier = deep.get("relevance_tier")
        if tier in relevance_counts:
            relevance_counts[tier] += 1

        # Product insight — the core intelligence extracted from this comment
        pi = deep.get("product_insight")
        if pi and pi.get("insight"):
            all_product_insights.append(pi)

        all_intents.extend(deep.get("intents") or [])
        all_conditions.extend(deep.get("conditions") or [])

        stage = deep.get("experience_stage")
        if stage and stage.get("stage") in stage_counts:
            stage_counts[stage["stage"]] += 1

        sw = deep.get("switching")
        if sw:
            if sw.get("from_product"):
                switching_from[sw["from_product"]] = switching_from.get(sw["from_product"], 0) + 1
            if sw.get("to_product"):
                switching_to[sw["to_product"]] = switching_to.get(sw["to_product"], 0) + 1

        for cl in deep.get("claims") or []:
            (all_claims if cl["type"] == "claim" else all_opinions).append(cl)

        # Expectation gaps
        gap = deep.get("expectation_gap")
        if gap:
            all_expectation_gaps.append(gap)

        # Version mentions
        ver = deep.get("version_mentioned")
        if ver:
            version_mentions[ver] = version_mentions.get(ver, 0) + 1

        # Causal chains
        chain = deep.get("causal_chain")
        if chain and len(chain) >= 2:
            all_causal_chains.append(chain)

    # Intent aggregations
    intent_by_type = {}
    for i in all_intents:
        intent_by_type[i["type"]] = intent_by_type.get(i["type"], 0) + 1

    intent_by_aspect = {}
    for i in all_intents:
        a = i["aspect"]
        if a not in intent_by_aspect:
            intent_by_aspect[a] = {"praise": 0, "complaint": 0, "suggestion": 0, "question": 0, "comparison": 0}
        if i["type"] in intent_by_aspect[a]:
            intent_by_aspect[a][i["type"]] += 1

    # Claim cross-verification
    claim_groups = {}
    for c in all_claims:
        key = (c["aspect"], c["text"].lower()[:50])
        if key not in claim_groups:
            claim_groups[key] = {"text": c["text"], "aspect": c["aspect"], "count": 0}
        claim_groups[key]["count"] += 1

    # Expectation gap summary
    gap_summary = {"exceeded": 0, "met": 0, "fell_short": 0}
    for g in all_expectation_gaps:
        d = g.get("direction")
        if d in gap_summary:
            gap_summary[d] += 1

    # Product insight aggregation — the headline intelligence layer.
    # Group what we learned about the product, by aspect, with sentiment direction
    # and a tally of how much came from ECOSYSTEM comments a pure filter would discard.
    insights_by_aspect: Dict[str, Dict[str, Any]] = {}
    n_ecosystem_intel = 0
    for pi in all_product_insights:
        if pi.get("evidence_type") in ("ecosystem", "peripheral"):
            n_ecosystem_intel += 1
        asp = pi.get("aspect") or "general"
        bucket = insights_by_aspect.setdefault(
            asp, {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0, "from_ecosystem": 0, "insights": []}
        )
        sent = pi.get("sentiment") or "neutral"
        if sent in bucket:
            bucket[sent] += 1
        if pi.get("evidence_type") in ("ecosystem", "peripheral"):
            bucket["from_ecosystem"] += 1
        bucket["insights"].append(pi)
    # keep each aspect's exemplar insights, highest-confidence first, capped
    for bucket in insights_by_aspect.values():
        bucket["insights"] = sorted(bucket["insights"], key=lambda p: -float(p.get("confidence") or 0))[:5]
        bucket["total"] = bucket["positive"] + bucket["negative"] + bucket["neutral"] + bucket["mixed"]
    insights_by_aspect = dict(sorted(insights_by_aspect.items(), key=lambda kv: -kv[1]["total"]))
    top_product_insights = sorted(all_product_insights, key=lambda p: -float(p.get("confidence") or 0))[:20]

    return {
        # Product insight — the core intelligence: what reviews reveal about the product
        "product_insights": top_product_insights,
        "product_insights_by_aspect": insights_by_aspect,
        "n_product_insights": len(all_product_insights),
        "n_ecosystem_intelligence": n_ecosystem_intel,
        # Relevance breakdown
        "relevance": relevance_counts,
        "n_direct": relevance_counts["direct"],
        "n_ecosystem": relevance_counts["ecosystem"],
        "n_filtered": relevance_counts["tangential"] + relevance_counts["off_topic"],
        # Intents
        "total_intents": len(all_intents),
        "intent_by_type": intent_by_type,
        "intent_by_aspect": dict(sorted(intent_by_aspect.items(), key=lambda x: sum(x[1].values()), reverse=True)),
        "multi_intent_reviews": sum(1 for r in per_review if len((r.get("deep") or {}).get("intents") or []) > 1),
        # Conditions
        "conditions": all_conditions,
        "n_conditional": len(all_conditions),
        # Experience
        "experience_stages": stage_counts,
        # Switching
        "switching_from": dict(sorted(switching_from.items(), key=lambda x: -x[1])),
        "switching_to": dict(sorted(switching_to.items(), key=lambda x: -x[1])),
        "n_switching": sum(switching_from.values()) + sum(switching_to.values()),
        # Claims
        "verified_claims": sorted([v for v in claim_groups.values() if v["count"] >= 2], key=lambda x: -x["count"]),
        "unverified_claims": [v for v in claim_groups.values() if v["count"] == 1][:10],
        "n_claims": len(all_claims),
        "n_opinions": len(all_opinions),
        # Expectation gaps
        "expectation_gaps": all_expectation_gaps,
        "expectation_summary": gap_summary,
        # Version trajectory
        "version_mentions": dict(sorted(version_mentions.items(), key=lambda x: -x[1])),
        # Causal chains
        "causal_chains": all_causal_chains,
        "n_causal_chains": len(all_causal_chains),
    }
