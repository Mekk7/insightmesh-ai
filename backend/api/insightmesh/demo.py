# backend/api/insightmesh/demo.py
"""
Pre-built demo reports for the three sample products.

Why this exists:
- This is a free / portfolio project. Most people opening it for the first
  time won't have YouTube + Reddit API keys configured. With no keys, the
  real pipeline returns a 503 and the dashboard is just an error screen.
- The demo endpoint returns a hand-tuned but realistic-looking `final_report`
  for any of the three sample products, populated for every dashboard widget:
  KPIs, sentiment-over-time, emotion mix, platform contributions, customer
  wishes, canonical clusters, voice-of-customer samples, and the verdict card.

Caller contract:
  GET /api/demo/report?product=tesla|sony|vision

Frontend behavior:
  When `run_pipeline` returns 503 (missing creds), the dashboard automatically
  falls back to this endpoint and shows a small "demo mode" banner. Real runs
  still work normally when keys are configured.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- Comment banks per product ----------------------------------------------
# Each tuple is (text, lang, sentiment_label, review_category, emotion_label, days_ago, platform, score, translated)

_BANK = {
    "tesla": [
        ("Best car I've ever driven. Build issues with my early VIN but service was great.", "en", "5 stars", "Praise", "joy", 2, "youtube", 47, None),
        ("Reichweite auf der Autobahn ist unter spec, sonst super.", "de", "3 stars", "Complaint", "sadness", 5, "reddit", 12, "Highway range is below spec, otherwise great."),
        ("续航不太好但其他都很棒。希望有更多充电站。", "zh", "4 stars", "Suggestion", "joy", 1, "youtube", 31, "Range isn't great but everything else is amazing. Hope for more charging stations."),
        ("Phantom braking on the highway scared me. Tesla please fix this with the next OTA.", "en", "2 stars", "Complaint", "fear", 8, "reddit", 89, None),
        ("Need more highway chargers every 100 miles. Road trips are stressful otherwise.", "en", "3 stars", "Suggestion", "neutral", 3, "reddit", 67, None),
        ("Acceleration is unreal. Smiling every drive.", "en", "5 stars", "Praise", "joy", 4, "youtube", 102, None),
        ("Coches eléctricos perfectos hasta que llega el invierno.", "es", "3 stars", "Complaint", "sadness", 11, "reddit", 18, "Electric cars are perfect until winter arrives."),
        ("Wish the heads-up display was an option. Looking down at the center screen is annoying.", "en", "3 stars", "Suggestion", "neutral", 6, "youtube", 41, None),
        ("Software updates broke my auto-wipers for two weeks. Now fixed.", "en", "3 stars", "Complaint", "anger", 14, "reddit", 33, None),
        ("Mon Model Y est génial mais le système de navigation est buggé.", "fr", "4 stars", "Complaint", "anger", 9, "youtube", 24, "My Model Y is great but the navigation system is buggy."),
        ("Summon mode is unreliable in tight parking lots. Should be smarter by now.", "en", "2 stars", "Complaint", "anger", 7, "reddit", 56, None),
        ("Service centers wait times are getting long but the car itself is incredible.", "en", "4 stars", "Praise", "joy", 13, "youtube", 38, None),
    ],
    "sony": [
        ("音質は信じられないほど良い、外の音もよく消える。", "ja", "5 stars", "Praise", "joy", 2, "youtube", 88, "Sound quality is unbelievably good, blocks outside noise well."),
        ("App still randomly forgets the pairing. Otherwise the gold standard.", "en", "4 stars", "Complaint", "anger", 4, "reddit", 54, None),
        ("Klang ist top, aber nach 3 Stunden werden die Ohrmuscheln warm.", "de", "5 stars", "Complaint", "sadness", 7, "reddit", 22, "Sound is great, but ear cups get warm after 3 hours."),
        ("Worth every penny if you fly a lot. Best ANC out there.", "en", "5 stars", "Praise", "joy", 1, "youtube", 134, None),
        ("Bring back the foldable hinge from XM4! New ones don't fold flat.", "en", "3 stars", "Suggestion", "sadness", 3, "reddit", 47, None),
        ("Cooler memory-foam ear pads would solve the warm-ear problem.", "en", "4 stars", "Suggestion", "neutral", 5, "youtube", 31, None),
        ("배터리가 정말 오래간다. 30시간은 거뜬해.", "ko", "5 stars", "Praise", "joy", 6, "youtube", 19, "Battery really lasts long. 30 hours easily."),
        ("Price feels steep next to the XM5 considering minor upgrades.", "en", "3 stars", "Complaint", "neutral", 9, "reddit", 71, None),
        ("USB-C audio at higher bitrates would be a killer feature.", "en", "4 stars", "Suggestion", "neutral", 12, "reddit", 28, None),
        ("Sonido perfecto pero la app sigue siendo frustrante.", "es", "4 stars", "Complaint", "anger", 8, "youtube", 17, "Perfect sound but the app continues to be frustrating."),
    ],
    "vision": [
        ("Magical for 20 minutes, painful after 60. Not worth $3500 without content.", "en", "2 stars", "Complaint", "sadness", 3, "youtube", 312, None),
        ("未来感很强，但戴久了脖子很累。", "zh", "4 stars", "Complaint", "sadness", 1, "reddit", 144, "Very futuristic feel but neck gets tired after long wear."),
        ("Increíble tecnología pero faltan apps que valgan la pena.", "es", "3 stars", "Complaint", "sadness", 5, "youtube", 89, "Incredible tech but missing apps worth using."),
        ("Lighter front module would change everything. Please Apple.", "en", "3 stars", "Suggestion", "neutral", 2, "reddit", 198, None),
        ("Native Netflix and YouTube apps are non-negotiable. Where are they?", "en", "2 stars", "Complaint", "anger", 7, "reddit", 167, None),
        ("Eye strain after 45 minutes is real. Maybe it's the focal distance.", "en", "3 stars", "Complaint", "fear", 4, "youtube", 122, None),
        ("Battery pack tethered cord is ugly engineering for a premium device.", "en", "2 stars", "Complaint", "anger", 10, "reddit", 78, None),
        ("In-air keyboard typing accuracy needs serious work.", "en", "3 stars", "Suggestion", "neutral", 6, "reddit", 64, None),
        ("Pour 3500€ on attendrait mieux. Mais la tech immersive est incroyable.", "fr", "3 stars", "Complaint", "sadness", 8, "youtube", 41, "For 3500 euros you'd expect better. But the immersive tech is incredible."),
        ("Prescription lens insert process needs to be smoother.", "en", "3 stars", "Suggestion", "neutral", 12, "reddit", 33, None),
    ],
}

_PROFILE = {
    "tesla": {
        "query": "Tesla Model Y", "n_kept": 247, "n_input": 312,
        "avg_sent": 4.1, "mood_index": 0.42,
        "clusters": [
            {"reason": "Highway range falls short of spec", "share": 23, "count": 57, "severity": "red"},
            {"reason": "Phantom braking on Autopilot", "share": 18, "count": 44, "severity": "red"},
            {"reason": "Software updates introduce bugs", "share": 14, "count": 35, "severity": "amber"},
            {"reason": "Build quality varies by VIN", "share": 9, "count": 22, "severity": "amber"},
            {"reason": "Service center wait times", "share": 7, "count": 17, "severity": "amber"},
        ],
        "wishes": [
            ("More highway charging stations every 100 miles", 186),
            ("Optional heads-up display", 97),
            ("Better summon-mode reliability in parking lots", 74),
            ("Bigger frunk and underfloor storage", 52),
            ("Native Apple CarPlay support", 41),
        ],
        "platforms": {"youtube": {"used": 138, "avg_sent": 0.78}, "reddit": {"used": 109, "avg_sent": 0.71}},
    },
    "sony": {
        "query": "Sony WH-1000XM6", "n_kept": 178, "n_input": 221,
        "avg_sent": 4.6, "mood_index": 0.68,
        "clusters": [
            {"reason": "Ear cups get warm on long sessions", "share": 14, "count": 25, "severity": "amber"},
            {"reason": "Companion app drops Bluetooth pairing", "share": 11, "count": 20, "severity": "amber"},
            {"reason": "Price feels steep next to XM5", "share": 9, "count": 16, "severity": "amber"},
            {"reason": "No more swappable battery", "share": 7, "count": 12, "severity": "gray"},
        ],
        "wishes": [
            ("Cooler memory-foam ear pad option", 142),
            ("Faster app reconnect after sleep", 88),
            ("USB-C audio at higher bitrates", 61),
            ("Bring back foldable hinge", 38),
        ],
        "platforms": {"youtube": {"used": 102, "avg_sent": 0.86}, "reddit": {"used": 76, "avg_sent": 0.82}},
    },
    "vision": {
        "query": "Apple Vision Pro", "n_kept": 412, "n_input": 528,
        "avg_sent": 3.2, "mood_index": -0.08,
        "clusters": [
            {"reason": "Weight causes neck fatigue", "share": 27, "count": 111, "severity": "red"},
            {"reason": "Killer-app content library is thin", "share": 22, "count": 91, "severity": "red"},
            {"reason": "Eye strain after 45 minutes", "share": 18, "count": 74, "severity": "red"},
            {"reason": "Battery pack tethered cord", "share": 12, "count": 49, "severity": "amber"},
            {"reason": "Price-to-content-value gap", "share": 9, "count": 37, "severity": "amber"},
        ],
        "wishes": [
            ("Lighter, balanced front module", 411),
            ("Native Netflix and YouTube apps", 298),
            ("In-air keyboard typing accuracy", 174),
            ("Prescription lens insert reduction", 91),
            ("Untethered battery", 67),
        ],
        "platforms": {"youtube": {"used": 234, "avg_sent": 0.62}, "reddit": {"used": 178, "avg_sent": 0.58}},
    },
}


def _time_series(profile_key: str, n_days: int = 14) -> List[Dict[str, Any]]:
    """Synthesize a realistic-looking sentiment-over-time series."""
    import math
    today = datetime.now(timezone.utc).date()
    base = _PROFILE[profile_key]["avg_sent"]
    trend = {"tesla": 0.012, "sony": 0.003, "vision": -0.018}[profile_key]
    series = []
    for i in range(n_days):
        day = today - timedelta(days=n_days - 1 - i)
        # Gentle sinusoidal jitter so the line has shape
        jitter = 0.15 * math.sin(i * 0.7)
        avg = max(1.0, min(5.0, base - trend * (n_days - i) + jitter))
        n = max(5, int(20 + 12 * math.cos(i * 0.5) + (i * 0.8)))
        series.append({
            "date": day.isoformat(),
            "n": n,
            "avg_sentiment": round(avg, 2),
            "mood_index": round((avg - 3) / 2, 3),
            "top_emotion": "Delighted" if avg >= 4 else ("Frustrated" if avg < 3 else "Hopeful"),
        })
    return series


def _build_per_review(key: str) -> List[Dict[str, Any]]:
    out = []
    star_to_num = {"1 star": 1, "2 stars": 2, "3 stars": 3, "4 stars": 4, "5 stars": 5}
    for (text, lang, sent_label, cat, emo, days_ago, platform, score, translated) in _BANK[key]:
        # Make sentiment scores plausible
        sent_score = 0.78 if sent_label in ("4 stars", "5 stars") else (0.62 if sent_label == "3 stars" else 0.81)
        out.append({
            "original": text,
            "translated_text": translated,
            "language": lang,
            "sentiment": sent_label,
            "sentiment_score": sent_score,
            "emotion": emo,
            "emotion_score": 0.74 if emo != "neutral" else 0.0,
            "emotion_all": {},
            "quality": 0.65 + (star_to_num[sent_label] * 0.03),
            "is_shallow": False,
            "classification": {"Praise": [], "Complaint": [], "Suggestion": [], "Prediction": []},
            "classification_scores": {"Praise": 0, "Complaint": 0, "Suggestion": 0, "Prediction": 0},
            "review_category": cat,
            "topic_labels": ["Other"],
            "keyphrases": [],
            "entities": [],
            "expectations": [],
            "published_at": _iso_days_ago(days_ago),
            "author": None,
            "score": score,
            "platform": platform,
            "canonical_reason": None,
            "cluster_id": 0,
            "cluster_topic": [],
            "cluster_score": 0.0,
        })
    return out


def _emotion_mix(per_review: List[Dict[str, Any]]) -> Dict[str, int]:
    pretty = {"joy": "Delighted", "surprise": "Excited", "sadness": "Disappointed",
              "fear": "Worried", "anger": "Frustrated", "disgust": "Disgusted", "neutral": "Neutral"}
    out: Dict[str, int] = {}
    for r in per_review:
        label = pretty.get((r.get("emotion") or "neutral").lower(), "Neutral")
        out[label] = out.get(label, 0) + 1
    return out


def _languages(per_review: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in per_review:
        lang = (r.get("language") or "").lower()
        if lang and lang != "unknown":
            out[lang] = out.get(lang, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def build_demo_report(key: str) -> Dict[str, Any]:
    if key not in _PROFILE:
        raise KeyError(key)
    profile = _PROFILE[key]
    per_review = _build_per_review(key)

    # Canonical clusters
    canonical_clusters = []
    for i, c in enumerate(profile["clusters"]):
        canonical_clusters.append({
            "cluster_id": i,
            "reason": c["reason"],
            "count": c["count"],
            "share_%": c["share"],
            "support": 0.78,
            "centroid_sim_mean": 0.84,
            "quotes": [],
            "solution": {
                "high_risk": c["severity"] == "red",
                "bullets": [
                    f"Investigate top failure modes for: {c['reason']}",
                    f"Validate a fix with telemetry across affected cohort",
                ],
            },
        })

    # Customer wishes
    customer_wishes = [
        {"wish": wish, "count": cnt, "samples": [], "sources": {"text_wish_verb": cnt}}
        for (wish, cnt) in profile["wishes"]
    ]

    # Per-platform contributions
    platform_contribs = []
    for p_name, p_data in profile["platforms"].items():
        platform_contribs.append({
            "platform": p_name,
            "share_%": round(p_data["used"] / profile["n_kept"] * 100, 1),
            "used": p_data["used"],
            "avg_sentiment_score": p_data["avg_sent"],
            "stars": {"1 star": 5, "2 stars": 10, "3 stars": 35, "4 stars": 80, "5 stars": p_data["used"] - 130},
            "top_reasons": [c["reason"] for c in profile["clusters"][:3]],
        })

    overview = {
        "average_sentiment": profile["avg_sent"],
        "mood_index": profile["mood_index"],
        "stars": {"1 star": 12, "2 stars": 24, "3 stars": 47, "4 stars": 88, "5 stars": 76},
        "top_keyphrases": ["battery range", "highway driving", "software updates", "build quality", "service experience"],
        "clusters": [],
        "canonical_clusters": canonical_clusters,
        "cluster_suggestions": [],
        "customer_wishes": customer_wishes,
        "language_distribution": _languages(per_review),
        "emotion_mix": _emotion_mix(per_review),
        "sentiment_over_time": _time_series(key),
        "astroturf_signals": {"flag": False, "summary": "No coordinated-review patterns detected.", "suspicious_clusters": [], "repeat_authors": []},
        "next_version_roadmap": _demo_roadmap(key, canonical_clusters),
        "what_users_love": _demo_praise_themes(key),
        "aspect_sentiment": _demo_aspect_sentiment(key),
        "buyer_intent_summary": _demo_buyer_intent(key),
        "sarcasm_stats": {"flagged_count": 0, "total": len(per_review)},
    }

    # Add derived intelligence to demo overview (TrustScore, forecast, counterfactuals)
    overview = _demo_enrich_overview(overview, key)

    return {
        "meta": {
            "user_mode": "consumer",
            "mode": "fast",
            "query_used": profile["query"],
            "time_from": None,
            "time_to": None,
            "strictness": "normal",
            "elapsed_ms": 1420,
            "from_cache": False,
            "demo_mode": True,
        },
        "platforms": {
            p_name: {"counts": {"fetched_raw": p_data["used"] + 20, "text_extracted": p_data["used"] + 20, "deduped": p_data["used"]}, "drop_stats": {}}
            for p_name, p_data in profile["platforms"].items()
        },
        "contributions": {"per_platform": platform_contribs},
        "analysis": {
            "meta": {
                "input_count": profile["n_input"],
                "kept_count": profile["n_kept"],
                "dropped_count": profile["n_input"] - profile["n_kept"],
                "dropped_summary": {"too_short": 24, "spam_phrase": 18, "lang_not_whitelisted": 12, "emoji_heavy": 11},
                "strictness": "normal",
                "terms_used": [profile["query"].lower()],
            },
            "per_review": per_review,
            "overview": overview,
            "executive_summary": {
                "totals_by_category": {"Praise": 38, "Complaint": 64, "Suggestion": 28, "Prediction": 4, "Neutral": 13},
                "top_reasons_overall": {},
                "top_aspects": [],
            },
            "action_items": [
                {"theme": c["reason"], "priority": i + 1, "item": f"Address: {c['reason']}"}
                for i, c in enumerate(profile["clusters"][:3])
            ],
            "topics_debug": {"mode": "demo"},
            "llm_backend": "demo",
        },
    }


def _demo_praise_themes(key: str) -> List[Dict[str, Any]]:
    themes = {
        "tesla": [
            {"theme": "Acceleration and driving feel", "count": 78, "quotes": ["Acceleration is unreal. Smiling every drive."]},
            {"theme": "Charging network reliability", "count": 54, "quotes": ["Supercharger network is unmatched on road trips."]},
            {"theme": "Over-the-air updates", "count": 41, "quotes": ["Car keeps getting better with each update."]},
        ],
        "sony": [
            {"theme": "Sound quality", "count": 102, "quotes": ["Sound quality is unbelievably good, blocks outside noise well."]},
            {"theme": "Noise cancellation", "count": 88, "quotes": ["Best ANC on the market for plane noise."]},
            {"theme": "Battery life", "count": 47, "quotes": ["Battery really lasts long. 30 hours easily."]},
        ],
        "vision": [
            {"theme": "Immersive video experience", "count": 67, "quotes": ["Magical for 20 minutes — watching movies in 3D space is incredible."]},
            {"theme": "Display sharpness", "count": 41, "quotes": ["The micro-OLED displays are next-level."]},
            {"theme": "Hand and eye tracking", "count": 28, "quotes": ["Eye + pinch interaction feels genuinely futuristic."]},
        ],
    }
    return themes.get(key, [])


def _demo_roadmap(key: str, clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, c in enumerate(clusters):
        sol = (c.get("solution") or {})
        bullets = sol.get("bullets") or []
        if not bullets:
            continue
        share = float(c.get("share_%", 0) or 0)
        impact = "high" if share >= 18 else ("medium" if share >= 9 else "low")
        # crude effort guess for the demo
        blob = (c.get("reason") or "").lower()
        effort = "high" if any(k in blob for k in ("battery", "hardware", "weight", "module")) else ("low" if any(k in blob for k in ("app", "software", "update", "price")) else "medium")
        out.append({
            "rank": i + 1,
            "complaint": c.get("reason"),
            "share_pct": share,
            "mentions": int(c.get("count", 0) or 0),
            "impact": impact,
            "effort": effort,
            "priority": round(min(1.0, share / 30.0) * 0.7 + 0.2, 2),
            "high_risk": sol.get("high_risk", False),
            "suggested_actions": bullets[:3],
            "backlog_note": None,
            "confidence": 0.78,
            "sources": [],
            "sample_quote": None,
            "cluster_id": c.get("cluster_id"),
        })
    return out[:6]


def _demo_personas(key: str) -> List[Dict[str, Any]]:
    """Curated reviewer personas per product."""
    by_product = {
        "tesla": [
            {"key": "tech_enthusiast", "label": "Tech enthusiast", "desc": "Reviewers who dig into specs, versions, and technical details", "icon": "▲", "tone": "indigo", "count": 71, "pct": 28.7, "avg_sentiment_stars": 4.4, "verdict": "loved", "top_concern": "Phantom braking on Autopilot", "sample_quote": "OTA updates keep adding new features. Software is the real differentiator.", "buyer_intent_mix": {"RECOMMEND": 18, "OWN": 26, "BUY": 4}},
            {"key": "long_term_owner", "label": "Long-term owner", "desc": "Reviewers who've used the product for months or years", "icon": "◐", "tone": "amber", "count": 53, "pct": 21.5, "avg_sentiment_stars": 4.2, "verdict": "loved", "top_concern": "Service center wait times", "sample_quote": "Had mine for 18 months. Still the best car I've ever driven, despite the service hassles.", "buyer_intent_mix": {"OWN": 53, "RECOMMEND": 12}},
            {"key": "critic", "label": "Comparison shopper", "desc": "Weighs pros and cons against alternatives", "icon": "≡", "tone": "violet", "count": 38, "pct": 15.4, "avg_sentiment_stars": 3.4, "verdict": "mixed", "top_concern": "Highway range falls short of spec", "sample_quote": "Compared to the Ford Lightning, range anxiety is real on Tesla road trips.", "buyer_intent_mix": {"COMPARE": 14, "WAIT": 7, "BUY": 5}},
            {"key": "mainstream", "label": "Mainstream user", "desc": "Everyday buyers focused on value and basic use", "icon": "●", "tone": "zinc", "count": 85, "pct": 34.4, "avg_sentiment_stars": 4.0, "verdict": "mixed", "top_concern": "Software updates introduce bugs", "sample_quote": "Love the car, hate that every update breaks something for a week.", "buyer_intent_mix": {"RECOMMEND": 15, "OWN": 18, "AVOID": 4}},
        ],
        "sony": [
            {"key": "tech_enthusiast", "label": "Tech enthusiast", "desc": "Reviewers who dig into specs, versions, and technical details", "icon": "▲", "tone": "indigo", "count": 41, "pct": 23.0, "avg_sentiment_stars": 4.7, "verdict": "loved", "top_concern": "USB-C audio bitrate limit", "sample_quote": "LDAC at 990kbps is glorious. Wish USB-C audio matched.", "buyer_intent_mix": {"RECOMMEND": 22, "OWN": 14}},
            {"key": "professional", "label": "Professional / power user", "desc": "Using the product for work or production", "icon": "◆", "tone": "blue", "count": 32, "pct": 18.0, "avg_sentiment_stars": 4.5, "verdict": "loved", "top_concern": "Ear cups get warm on long sessions", "sample_quote": "I produce music with these. Best ANC for an open office, hands down.", "buyer_intent_mix": {"RECOMMEND": 20, "OWN": 10}},
            {"key": "long_term_owner", "label": "Long-term owner", "desc": "Reviewers who've used the product for months or years", "icon": "◐", "tone": "amber", "count": 28, "pct": 15.7, "avg_sentiment_stars": 4.6, "verdict": "loved", "top_concern": "App reliability over time", "sample_quote": "Owned the XM4 and now the XM6 — still the gold standard after years.", "buyer_intent_mix": {"OWN": 28, "RECOMMEND": 14}},
            {"key": "mainstream", "label": "Mainstream user", "desc": "Everyday buyers focused on value and basic use", "icon": "●", "tone": "zinc", "count": 77, "pct": 43.3, "avg_sentiment_stars": 4.4, "verdict": "loved", "top_concern": "Price feels steep next to XM5", "sample_quote": "Worth every penny if you fly a lot. Otherwise the XM5 is still great.", "buyer_intent_mix": {"RECOMMEND": 32, "BUY": 6, "WAIT": 4}},
        ],
        "vision": [
            {"key": "tech_enthusiast", "label": "Tech enthusiast", "desc": "Reviewers who dig into specs, versions, and technical details", "icon": "▲", "tone": "indigo", "count": 74, "pct": 18.0, "avg_sentiment_stars": 3.6, "verdict": "mixed", "top_concern": "Weight causes neck fatigue", "sample_quote": "The micro-OLEDs are stunning. The form factor isn't ready for prime time.", "buyer_intent_mix": {"OWN": 38, "RECOMMEND": 8, "WAIT": 18}},
            {"key": "professional", "label": "Professional / power user", "desc": "Using the product for work or production", "icon": "◆", "tone": "blue", "count": 42, "pct": 10.2, "avg_sentiment_stars": 3.1, "verdict": "mixed", "top_concern": "Killer-app content library is thin", "sample_quote": "Tried using it for client presentations. Magical demo, painful in practice.", "buyer_intent_mix": {"OWN": 22, "WAIT": 10, "RETURN": 5}},
            {"key": "critic", "label": "Comparison shopper", "desc": "Weighs pros and cons against alternatives", "icon": "≡", "tone": "violet", "count": 91, "pct": 22.1, "avg_sentiment_stars": 2.6, "verdict": "struggling", "top_concern": "Price-to-content-value gap", "sample_quote": "Compared to Meta Quest 3 at a fraction of the price, value just isn't there yet.", "buyer_intent_mix": {"COMPARE": 22, "AVOID": 18, "WAIT": 14}},
            {"key": "mainstream", "label": "Mainstream user", "desc": "Everyday buyers focused on value and basic use", "icon": "●", "tone": "zinc", "count": 205, "pct": 49.8, "avg_sentiment_stars": 3.0, "verdict": "mixed", "top_concern": "Eye strain after 45 minutes", "sample_quote": "Cool tech but I just want to watch movies. This is overkill.", "buyer_intent_mix": {"AVOID": 23, "WAIT": 29, "OWN": 14}},
        ],
    }
    return by_product.get(key, [])


def _demo_customer_effort(key: str) -> Optional[Dict[str, Any]]:
    """Curated CES per product. Only returned for products where it's a real signal."""
    by_product = {
        "tesla": {
            "score": 38.5, "label": "Moderate", "affected_share_pct": 17.4, "total_signals": 43, "categories_hit": 4,
            "breakdown": [
                {"category": "support_pain", "label": "Support friction", "count": 18, "share_pct": 7.3, "sample": "Service center wait times are getting long but the car itself is incredible."},
                {"category": "setup_friction", "label": "Setup pain", "count": 12, "share_pct": 4.9, "sample": "Couldn't pair my phone for the first three days. Reset everything to get it working."},
                {"category": "learning_curve", "label": "Learning curve", "count": 8, "share_pct": 3.2, "sample": "So many menus and settings it's confusing for the first month."},
                {"category": "documentation_gap", "label": "Docs gap", "count": 5, "share_pct": 2.0, "sample": "Manual is useless — figured it out from YouTube."},
            ],
            "narrative": "Moderate effort. Service friction hurts a portion of buyers (17%). Top pain: support friction.",
        },
        "sony": {
            "score": 18.7, "label": "Light", "affected_share_pct": 8.4, "total_signals": 19, "categories_hit": 2,
            "breakdown": [
                {"category": "setup_friction", "label": "Setup pain", "count": 12, "share_pct": 6.7, "sample": "App still randomly forgets the pairing. Have to re-pair every few weeks."},
                {"category": "account_friction", "label": "Account issues", "count": 7, "share_pct": 3.9, "sample": "Lost my EQ settings after a firmware update. Annoying but recoverable."},
            ],
            "narrative": "Light effort signal — minor friction here and there but most owners get through it cleanly.",
        },
        "vision": {
            "score": 72.3, "label": "Heavy", "affected_share_pct": 38.2, "total_signals": 187, "categories_hit": 6,
            "breakdown": [
                {"category": "learning_curve", "label": "Learning curve", "count": 71, "share_pct": 17.2, "sample": "Took me weeks to figure out the gestures. Tutorials helped but the menus are unintuitive."},
                {"category": "setup_friction", "label": "Setup pain", "count": 54, "share_pct": 13.1, "sample": "Initial setup with the prescription lens insert took multiple tries to get right."},
                {"category": "account_friction", "label": "Account issues", "count": 38, "share_pct": 9.2, "sample": "Apple ID issues meant I lost all my purchased content the first week."},
                {"category": "return_friction", "label": "Returns process", "count": 22, "share_pct": 5.3, "sample": "Returning was a nightmare — store wanted everything, including the box and inserts."},
                {"category": "support_pain", "label": "Support friction", "count": 14, "share_pct": 3.4, "sample": "Called support twice about eye strain. They told me to take breaks. Helpful."},
                {"category": "documentation_gap", "label": "Docs gap", "count": 9, "share_pct": 2.2, "sample": "No proper manual on how to use developer mode."},
            ],
            "narrative": "Effort is heavy — 38% of reviewers mention friction. Top pain: learning curve.",
        },
    }
    return by_product.get(key)


def _demo_marketing_angles(key: str) -> List[Dict[str, Any]]:
    """Curated marketing angles per product. Only returned when there are strong praise themes."""
    by_product = {
        "tesla": [
            {"theme": "Best-in-Class Acceleration", "raw_theme": "acceleration", "mentions": 58, "positive_ratio": 0.91, "avg_sentiment_stars": 4.6, "best_quote": "Acceleration is unreal — smiling every drive, even after a year.", "supporting_quotes": ["Hands down the most fun car I've ever driven.", "Highly recommend if you've never felt instant torque."]},
            {"theme": "Updates That Keep Getting Better", "raw_theme": "ota updates", "mentions": 44, "positive_ratio": 0.84, "avg_sentiment_stars": 4.3, "best_quote": "OTA updates keep adding new features — the car genuinely gets better every month.", "supporting_quotes": ["No other car maker comes close on software.", "Worth every penny just for the OTA model."]},
            {"theme": "Unmatched Supercharger Network", "raw_theme": "supercharger", "mentions": 31, "positive_ratio": 0.89, "avg_sentiment_stars": 4.5, "best_quote": "Supercharger network is unmatched on road trips — finding a charger is never a worry.", "supporting_quotes": ["Best road-trip EV by a mile because of charging.", "Stellar coverage, even in rural areas."]},
        ],
        "sony": [
            {"theme": "Best Sound Quality in Its Class", "raw_theme": "sound quality", "mentions": 102, "positive_ratio": 0.94, "avg_sentiment_stars": 4.7, "best_quote": "Sound quality is unbelievably good — you hear details you never noticed before.", "supporting_quotes": ["Incredible bass, clear mids, perfect highs.", "Worth every penny for travelers and audiophiles alike."]},
            {"theme": "Silence-Grade Noise Cancellation", "raw_theme": "noise cancellation", "mentions": 88, "positive_ratio": 0.92, "avg_sentiment_stars": 4.6, "best_quote": "Best ANC on the market for plane noise — 12-hour flight, complete silence.", "supporting_quotes": ["Hands down the best ANC I've used.", "Stellar for an open office."]},
            {"theme": "30 Hours of Real Battery Life", "raw_theme": "battery life", "mentions": 28, "positive_ratio": 0.89, "avg_sentiment_stars": 4.4, "best_quote": "30 hours of battery is plenty for a week of travel without charging.", "supporting_quotes": ["Battery really lasts long, exceeded my expectations."]},
        ],
        "vision": [
            {"theme": "Stunning Micro-OLED Displays", "raw_theme": "display", "mentions": 84, "positive_ratio": 0.81, "avg_sentiment_stars": 4.4, "best_quote": "The micro-OLED displays are next-level — nothing else in XR comes close.", "supporting_quotes": ["Incredible resolution, no screen door at all.", "Stunning visuals, gorgeous in every demo."]},
            {"theme": "Genuinely Futuristic Interaction", "raw_theme": "eye and hand tracking", "mentions": 38, "positive_ratio": 0.84, "avg_sentiment_stars": 4.1, "best_quote": "Eye + pinch interaction feels genuinely futuristic — the way computing should work.", "supporting_quotes": ["Best gesture system I've used in any device.", "Unmatched precision on small targets."]},
        ],
    }
    return by_product.get(key, [])


def _demo_enrich_overview(overview: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Attach TrustScore + counterfactuals + forecast + risk register to demo overview."""
    try:
        from backend.insight.trust.score import compute_trust_score
        overview["trust_score"] = compute_trust_score(
            average_sentiment=overview.get("average_sentiment"),
            sample_size=(overview.get("sarcasm_stats") or {}).get("total", 0),
            language_count=len(overview.get("language_distribution") or {}),
            astroturf_flag=bool((overview.get("astroturf_signals") or {}).get("flag")),
            sarcasm_stats=overview.get("sarcasm_stats"),
            decision_health=(overview.get("buyer_intent_summary") or {}).get("decision_health"),
            canonical_clusters=overview.get("canonical_clusters"),
            sentiment_over_time=overview.get("sentiment_over_time"),
        )
    except Exception:
        pass

    try:
        from backend.insight.forecast.sentiment_predict import forecast_sentiment
        overview["sentiment_forecast"] = forecast_sentiment(overview.get("sentiment_over_time") or [], horizon_days=14)
    except Exception:
        pass

    try:
        from backend.insight.counterfactual.impact import compute_counterfactuals, cumulative_impact
        overview["next_version_roadmap"] = compute_counterfactuals(
            roadmap_items=overview.get("next_version_roadmap") or [],
            canonical_clusters=overview.get("canonical_clusters") or [],
            average_sentiment=overview.get("average_sentiment"),
        )
        overview["cumulative_impact"] = cumulative_impact(
            overview.get("next_version_roadmap") or [],
            top_k=3,
            average_sentiment=overview.get("average_sentiment"),
        )
    except Exception:
        pass

    try:
        from backend.insight.severity.scorer import score_cluster as _sc
        # Make sure each demo cluster has a severity block so the risk register works
        for c in overview.get("canonical_clusters") or []:
            if "severity" not in c:
                c["severity"] = _sc(c.get("reason", ""), c.get("quotes", []))
        from backend.insight.severity.risk_register import build_risk_register
        overview["risk_register"] = build_risk_register(overview.get("canonical_clusters") or [])
    except Exception:
        pass

    # Curated personas / effort / marketing angles per demo product ("no N/A" friendly)
    personas = _demo_personas(key)
    if personas:
        overview["personas"] = personas
    effort = _demo_customer_effort(key)
    if effort:
        overview["customer_effort"] = effort
    angles = _demo_marketing_angles(key)
    if angles:
        overview["marketing_angles"] = angles

    # Curated smart summary per demo product (narrative card for the top of the dashboard)
    smart = _demo_smart_summary(key)
    if smart:
        overview["smart_summary"] = smart

    # Curated aspect-hierarchy + taxonomy per demo product (Phase A depth)
    hierarchy = _demo_aspect_hierarchy(key)
    if hierarchy:
        overview["aspect_hierarchy"] = hierarchy
        overview["aspect_taxonomy"] = {
            "source": "demo",
            "domain_detected": {"tesla": "auto_ev", "sony": "audio", "vision": "xr_vr"}.get(key),
            "aspect_count": len(hierarchy),
        }

    return overview


def _demo_aspect_hierarchy(key: str) -> List[Dict[str, Any]]:
    """Curated hierarchical decomposition per demo product. Shows the kind of depth
    the system produces on real data (sub-issues with stats, severity, narratives)."""
    by_product = {
        "tesla": [
            {
                "aspect": "battery",
                "label": "Battery & Range",
                "total_mentions": 67,
                "share_of_complaints_pct": 32.1,
                "share_of_all_pct": 27.1,
                "avg_sentiment_stars": 2.2,
                "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Highway Range Below EPA Spec",
                        "mentions": 28, "share_of_aspect_pct": 41.8, "share_of_all_pct": 11.3,
                        "avg_sentiment_stars": 1.9, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["critic", "long_term_owner"],
                        "fix_difficulty": {"category": "engineering", "effort": "high"},
                        "sample_quotes": [
                            "Range drops 30% on highway at 75mph compared to EPA estimates.",
                            "On road trips I'm losing 20-25 miles vs what the screen predicts.",
                            "Real-world range matches city driving fine but highway is rough.",
                        ],
                        "narrative": "Affects long-term owners and comparison shoppers most. Reports concentrated in North America. Tech-savvy reviewers cite specific mph and mile loss numbers; mainstream owners describe it as range anxiety. Pattern is consistent across battery sizes.",
                        "signals": {"geographic_hints": ["North America"], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Cold-Weather Range Drop",
                        "mentions": 16, "share_of_aspect_pct": 23.9, "share_of_all_pct": 6.5,
                        "avg_sentiment_stars": 2.1, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner"],
                        "fix_difficulty": {"category": "engineering", "effort": "high"},
                        "sample_quotes": [
                            "Lost 30% of range when it dropped below freezing in Minnesota.",
                            "Winter in Norway cut my range almost in half on the first cold week.",
                            "Cold weather kills the battery hard, especially when parking outside overnight.",
                        ],
                        "narrative": "Almost all reports from cold-climate owners (US Northeast, Canada, Scandinavia). Long-term owners notice it most after the first winter. Consistent with battery chemistry behavior and could improve with heat-pump retrofits.",
                        "signals": {"geographic_hints": ["North America", "Europe"], "temporal_hint": "winter", "version_hints": []},
                    },
                    {
                        "name": "Charging Port Flap Reliability",
                        "mentions": 13, "share_of_aspect_pct": 19.4, "share_of_all_pct": 5.3,
                        "avg_sentiment_stars": 2.4, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner"],
                        "fix_difficulty": {"category": "hardware", "effort": "medium"},
                        "sample_quotes": [
                            "Charging port flap broke after 8 months, had to wait 3 weeks for the part.",
                            "The little door that covers the charging port is flimsy and easy to break.",
                            "Second charging port replacement in 18 months — design flaw clearly.",
                        ],
                        "narrative": "Affects long-term owners after sustained daily use. Clear hardware-design pattern — the flap mechanism fails under repeated cycles. Not safety-critical but service-center bottleneck.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Supercharger Queue Waits",
                        "mentions": 10, "share_of_aspect_pct": 14.9, "share_of_all_pct": 4.0,
                        "avg_sentiment_stars": 2.7, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner"],
                        "fix_difficulty": {"category": "process", "effort": "high"},
                        "sample_quotes": [
                            "On holiday weekends Supercharger waits hit 45+ minutes on I-95.",
                            "Long line at the only supercharger on my route home from Thanksgiving.",
                            "Wait times getting worse as more EVs hit the road.",
                        ],
                        "narrative": "Concentrated on holiday weekends and major travel corridors. Mainly affects long-distance travelers. Network-capacity problem, not a product problem per se.",
                        "signals": {"geographic_hints": ["North America"], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "autopilot",
                "label": "Autopilot & FSD",
                "total_mentions": 51, "share_of_complaints_pct": 24.4, "share_of_all_pct": 20.6,
                "avg_sentiment_stars": 2.6, "verdict": "mixed",
                "sub_issues": [
                    {
                        "name": "Phantom Braking on Highway",
                        "mentions": 22, "share_of_aspect_pct": 43.1, "share_of_all_pct": 8.9,
                        "avg_sentiment_stars": 1.7, "severity": "CRITICAL", "is_safety": True, "is_accessibility": False,
                        "personas_most_affected": ["tech_enthusiast", "long_term_owner"],
                        "fix_difficulty": {"category": "firmware", "effort": "high"},
                        "sample_quotes": [
                            "Phantom braking on the highway scared me — thought I was going to get rear-ended.",
                            "Autopilot slammed the brakes for no reason on a clear interstate.",
                            "Random hard braking events under overpasses and bridge shadows.",
                        ],
                        "narrative": "Safety-critical pattern affecting both tech-enthusiast and long-term-owner personas. Reports across all regions but US Northeast bridge-shadow events are most common. Firmware-level fix possible — should ship in next OTA regardless of share.",
                        "signals": {"geographic_hints": ["North America"], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Lane-Keep Drift in Construction Zones",
                        "mentions": 14, "share_of_aspect_pct": 27.5, "share_of_all_pct": 5.7,
                        "avg_sentiment_stars": 2.6, "severity": "HIGH", "is_safety": True, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner"],
                        "fix_difficulty": {"category": "firmware", "effort": "high"},
                        "sample_quotes": [
                            "Autopilot gets confused when lanes shift in construction — had to grab the wheel.",
                            "Lane-keep tries to follow the old painted lines through a work zone.",
                            "Construction zones are still a known weakness for the system.",
                        ],
                        "narrative": "Long-term owners notice it more (they encounter more edge cases). Software-perception limitation in temporary road markings. Active research area for the Autopilot team.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "FSD Beta Cost-vs-Value Concerns",
                        "mentions": 15, "share_of_aspect_pct": 29.4, "share_of_all_pct": 6.1,
                        "avg_sentiment_stars": 3.0, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["critic"],
                        "fix_difficulty": {"category": "process", "effort": "medium"},
                        "sample_quotes": [
                            "FSD at $15k feels steep for what's still beta software.",
                            "Paid for FSD two years ago and still doesn't drive me door to door.",
                            "Considering canceling and switching to the subscription model.",
                        ],
                        "narrative": "Comparison-shopper persona dominates this complaint. Pricing/positioning issue rather than a product defect. Subscription model is already addressing it for new buyers.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "software",
                "label": "Software & Updates",
                "total_mentions": 44, "share_of_complaints_pct": 21.1, "share_of_all_pct": 17.8,
                "avg_sentiment_stars": 3.2, "verdict": "mixed",
                "sub_issues": [
                    {
                        "name": "OTA Updates Introduce Regressions",
                        "mentions": 18, "share_of_aspect_pct": 40.9, "share_of_all_pct": 7.3,
                        "avg_sentiment_stars": 2.8, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["tech_enthusiast", "long_term_owner"],
                        "fix_difficulty": {"category": "firmware", "effort": "medium"},
                        "sample_quotes": [
                            "Latest update broke my auto-wipers — they spaz out in light rain now.",
                            "Every other update breaks something for a week before the hotfix.",
                            "OTA software updates broke the auto-wiper sensitivity for me.",
                        ],
                        "narrative": "Tech-enthusiast persona reports most of these because they pay attention to specific feature regressions. Suggests QA-coverage gap in OTA release process. Quick wins available.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Phone-as-Key Pairing Loss",
                        "mentions": 14, "share_of_aspect_pct": 31.8, "share_of_all_pct": 5.7,
                        "avg_sentiment_stars": 2.5, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "firmware", "effort": "medium"},
                        "sample_quotes": [
                            "Phone-as-key keeps dropping the connection — I have to use the card to get in.",
                            "Every couple of weeks I'm locked out and have to re-pair my phone.",
                            "Bluetooth keyfob connection is unreliable in cold weather.",
                        ],
                        "narrative": "Mainstream-user persona reports this most since it impacts daily driving experience. Reliability problem in BLE pairing layer. Frequency-of-use makes it disproportionately painful.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Infotainment Map Lag in Cities",
                        "mentions": 12, "share_of_aspect_pct": 27.3, "share_of_all_pct": 4.9,
                        "avg_sentiment_stars": 3.1, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "firmware", "effort": "low"},
                        "sample_quotes": [
                            "Map gets laggy navigating downtown SF — misses turns.",
                            "Navigation rendering chokes in dense city blocks.",
                            "Map sometimes loses my position briefly between tall buildings.",
                        ],
                        "narrative": "Annoyance-level rather than blocker. Mostly noticed in major US metros. Map-renderer optimization could resolve.",
                        "signals": {"geographic_hints": ["North America"], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "service",
                "label": "Service Experience",
                "total_mentions": 28, "share_of_complaints_pct": 13.4, "share_of_all_pct": 11.3,
                "avg_sentiment_stars": 2.6, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Service Center Wait Times",
                        "mentions": 18, "share_of_aspect_pct": 64.3, "share_of_all_pct": 7.3,
                        "avg_sentiment_stars": 2.4, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner", "mainstream"],
                        "fix_difficulty": {"category": "process", "effort": "high"},
                        "sample_quotes": [
                            "Service center wait times are getting really long — 3 weeks for an appointment.",
                            "Service has gone downhill as Tesla scales — used to be a week, now it's a month.",
                            "Appointment scheduled 4 weeks out for what should be a 30-minute fix.",
                        ],
                        "narrative": "Affects long-term owners and mainstream users alike. Capacity not scaling with fleet size. Brand-trust risk because the product itself is great when working.",
                        "signals": {"geographic_hints": ["North America"], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Mobile Service Quality Variance",
                        "mentions": 10, "share_of_aspect_pct": 35.7, "share_of_all_pct": 4.0,
                        "avg_sentiment_stars": 2.9, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["long_term_owner"],
                        "fix_difficulty": {"category": "process", "effort": "medium"},
                        "sample_quotes": [
                            "Mobile service tech was great but had to come back 3 times to finish the work.",
                            "Quality varies hugely between mobile service techs in my area.",
                            "Mobile service is great in theory but the techs aren't always equipped.",
                        ],
                        "narrative": "Quality-of-execution issue rather than capacity. Tech training and parts-stocking gaps. Could improve with stronger field-tech enablement.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
        ],
        "sony": [
            {
                "aspect": "comfort",
                "label": "Comfort & Fit",
                "total_mentions": 41, "share_of_complaints_pct": 38.7, "share_of_all_pct": 23.0,
                "avg_sentiment_stars": 2.9, "verdict": "mixed",
                "sub_issues": [
                    {
                        "name": "Ear Cup Warmth on Long Sessions",
                        "mentions": 21, "share_of_aspect_pct": 51.2, "share_of_all_pct": 11.8,
                        "avg_sentiment_stars": 2.7, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["professional"],
                        "fix_difficulty": {"category": "hardware", "effort": "medium"},
                        "sample_quotes": [
                            "Ear cups get really warm after 3 hours — had to switch back to my AirPods.",
                            "Comfortable for an hour but starts feeling hot on long flights.",
                            "Pads are soft but they trap heat against my ears.",
                        ],
                        "narrative": "Professionals using these for long sessions are the most-affected segment. Thermal pad redesign or breathable mesh option would address it. Not a deal-breaker for short-session users.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Headband Pressure on Larger Heads",
                        "mentions": 12, "share_of_aspect_pct": 29.3, "share_of_all_pct": 6.7,
                        "avg_sentiment_stars": 3.0, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "low"},
                        "sample_quotes": [
                            "Headband clamps a little tight for me — had to stretch it out over a few weeks.",
                            "Tight on top of my head after an hour of use.",
                            "My partner with a smaller head finds them perfect; for me it's a bit much.",
                        ],
                        "narrative": "Size-fit variance rather than a defect. Adjustment tip in documentation would reduce returns.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Glasses Wearer Seal Issues",
                        "mentions": 8, "share_of_aspect_pct": 19.5, "share_of_all_pct": 4.5,
                        "avg_sentiment_stars": 3.1, "severity": "LOW", "is_safety": False, "is_accessibility": True,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "medium"},
                        "sample_quotes": [
                            "Glasses arms break the ANC seal slightly — small loss but noticeable on planes.",
                            "As a glasses wearer the seal isn't perfect.",
                            "Noise cancellation drops a bit because the cup can't seat flat over my temples.",
                        ],
                        "narrative": "Accessibility-adjacent issue. Affects glasses wearers (15-20% of users). Pad geometry change could close the gap.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "app",
                "label": "Companion App",
                "total_mentions": 32, "share_of_complaints_pct": 30.2, "share_of_all_pct": 18.0,
                "avg_sentiment_stars": 2.4, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Random Pairing Loss",
                        "mentions": 18, "share_of_aspect_pct": 56.3, "share_of_all_pct": 10.1,
                        "avg_sentiment_stars": 2.1, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream", "professional"],
                        "fix_difficulty": {"category": "firmware", "effort": "medium"},
                        "sample_quotes": [
                            "App still randomly forgets the pairing every few weeks.",
                            "Have to re-pair the headphones with my phone too often.",
                            "Bluetooth pairing drops and the app needs me to manually reconnect.",
                        ],
                        "narrative": "Affects both mainstream and professional users. Frequency-of-use makes a medium-severity bug feel HIGH. Firmware/BLE-layer fix would unlock the strongest praise themes (sound + ANC) for these affected users.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "EQ Settings Reset After Firmware Update",
                        "mentions": 9, "share_of_aspect_pct": 28.1, "share_of_all_pct": 5.1,
                        "avg_sentiment_stars": 2.6, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["tech_enthusiast"],
                        "fix_difficulty": {"category": "firmware", "effort": "low"},
                        "sample_quotes": [
                            "Lost my EQ settings after a firmware update.",
                            "Custom EQ profile got wiped during the last app update.",
                            "After update my carefully tuned EQ was back to default.",
                        ],
                        "narrative": "Affects tuners and tech-enthusiast persona. Easy quick-win fix — preserve user settings across firmware updates.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": ["firmware update"]},
                    },
                    {
                        "name": "App UI Confusing for New Users",
                        "mentions": 5, "share_of_aspect_pct": 15.6, "share_of_all_pct": 2.8,
                        "avg_sentiment_stars": 3.0, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "firmware", "effort": "low"},
                        "sample_quotes": [
                            "App UI is confusing — too many tabs for what should be a simple control.",
                            "Took me a while to find the noise-cancel settings in the app.",
                            "App could use a redesign for clarity.",
                        ],
                        "narrative": "Onboarding-flow gap. Mainstream users find the IA cluttered. UX-only fix.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "price",
                "label": "Price & Value",
                "total_mentions": 24, "share_of_complaints_pct": 22.6, "share_of_all_pct": 13.5,
                "avg_sentiment_stars": 2.5, "verdict": "mixed",
                "sub_issues": [
                    {
                        "name": "Price Premium over XM5",
                        "mentions": 14, "share_of_aspect_pct": 58.3, "share_of_all_pct": 7.9,
                        "avg_sentiment_stars": 2.7, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["critic", "mainstream"],
                        "fix_difficulty": {"category": "process", "effort": "low"},
                        "sample_quotes": [
                            "Price feels steep next to the XM5 which is now heavily discounted.",
                            "$100 more than the XM5 for what feels like a small upgrade.",
                            "Better value to grab the XM5 on sale unless you really need the latest.",
                        ],
                        "narrative": "Comparison-shopper persona dominates. Pricing/positioning question rather than product flaw. Marketing can address with clearer value-prop messaging.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Carrying Case Plastic Feels Cheap",
                        "mentions": 6, "share_of_aspect_pct": 25.0, "share_of_all_pct": 3.4,
                        "avg_sentiment_stars": 2.8, "severity": "LOW", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "low"},
                        "sample_quotes": [
                            "Carrying case zipper and plastic feel cheap given the price.",
                            "At this price the included case should feel more premium.",
                            "The case clasp on the older model felt sturdier.",
                        ],
                        "narrative": "Perception issue around premium-product packaging. Low-cost fix with high satisfaction impact.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
        ],
        "vision": [
            {
                "aspect": "weight",
                "label": "Weight & Wearability",
                "total_mentions": 121, "share_of_complaints_pct": 45.3, "share_of_all_pct": 29.4,
                "avg_sentiment_stars": 1.6, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Neck Fatigue After 30+ Minutes",
                        "mentions": 64, "share_of_aspect_pct": 52.9, "share_of_all_pct": 15.5,
                        "avg_sentiment_stars": 1.5, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["critic", "mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "high"},
                        "sample_quotes": [
                            "Weight causes neck fatigue after 30 minutes — had to take it off.",
                            "Couldn't get through a full movie because my neck was sore.",
                            "Front-heavy design means constant adjusting and breaks.",
                        ],
                        "narrative": "Hits comparison-shoppers and mainstream users hardest because they expect long sessions. Front-heavy weight distribution is structural — needs a real hardware revision. Affects core use-cases (movies, gaming sessions).",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Tethered Battery Pack Cable Annoyance",
                        "mentions": 32, "share_of_aspect_pct": 26.4, "share_of_all_pct": 7.8,
                        "avg_sentiment_stars": 1.9, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "high"},
                        "sample_quotes": [
                            "External battery pack on a cable feels like a step backwards.",
                            "The tether to the battery is awkward and limits movement.",
                            "Couldn't walk around naturally because of the battery cable.",
                        ],
                        "narrative": "Mainstream users find the tethered battery a workflow-breaker. Required design trade-off for current weight target. Future on-headset battery would address both this and the weight issue.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Light Seal Discomfort",
                        "mentions": 25, "share_of_aspect_pct": 20.7, "share_of_all_pct": 6.1,
                        "avg_sentiment_stars": 2.0, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "medium"},
                        "sample_quotes": [
                            "Light seal leaves a red ring on my face after an hour.",
                            "Light seal presses uncomfortably on the cheekbones.",
                            "Imprint on my face is visible for a while after taking it off.",
                        ],
                        "narrative": "Pressure-distribution issue at the face seal. Alternate light-seal sizes or third-party-friendly designs would help. Visible imprints are a social-friction factor too.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "content",
                "label": "Content Library",
                "total_mentions": 98, "share_of_complaints_pct": 36.7, "share_of_all_pct": 23.8,
                "avg_sentiment_stars": 2.0, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Missing Native Netflix and YouTube",
                        "mentions": 49, "share_of_aspect_pct": 50.0, "share_of_all_pct": 11.9,
                        "avg_sentiment_stars": 1.7, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream", "critic"],
                        "fix_difficulty": {"category": "process", "effort": "high"},
                        "sample_quotes": [
                            "Native Netflix and YouTube apps are non-negotiable at this price.",
                            "Why is there no YouTube app on a $3500 device?",
                            "Without Netflix and YouTube the content library feels broken.",
                        ],
                        "narrative": "Mainstream and comparison-shopper personas cite this as the #1 deal-breaker. This is a partnership/business problem, not engineering — but it's the single biggest unlock for satisfaction and adoption.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Thin Killer-App Library",
                        "mentions": 31, "share_of_aspect_pct": 31.6, "share_of_all_pct": 7.5,
                        "avg_sentiment_stars": 2.2, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["tech_enthusiast", "professional"],
                        "fix_difficulty": {"category": "process", "effort": "high"},
                        "sample_quotes": [
                            "Where are the must-have apps that only run on this thing?",
                            "Some apps are genuinely magical but they're too few right now.",
                            "App ecosystem feels barren outside of the demos.",
                        ],
                        "narrative": "Tech-enthusiast and professional segments expected a richer ecosystem at launch. Developer-relations investment and dedicated launch titles are needed.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Productivity Apps Underdeveloped",
                        "mentions": 18, "share_of_aspect_pct": 18.4, "share_of_all_pct": 4.4,
                        "avg_sentiment_stars": 2.6, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["professional"],
                        "fix_difficulty": {"category": "process", "effort": "medium"},
                        "sample_quotes": [
                            "Tried using it for client presentations — magical demo, painful in practice.",
                            "Productivity apps don't feel ready for real work yet.",
                            "Need better text-input and multi-window flows for real workflows.",
                        ],
                        "narrative": "Professional persona expected the device to replace a laptop and it can't yet. Multi-app and text-input UX work would close the gap. This is the audience most likely to convert if addressed.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "price",
                "label": "Price & Value",
                "total_mentions": 76, "share_of_complaints_pct": 28.5, "share_of_all_pct": 18.4,
                "avg_sentiment_stars": 1.8, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Value Gap vs Meta Quest 3",
                        "mentions": 42, "share_of_aspect_pct": 55.3, "share_of_all_pct": 10.2,
                        "avg_sentiment_stars": 1.6, "severity": "HIGH", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["critic"],
                        "fix_difficulty": {"category": "process", "effort": "high"},
                        "sample_quotes": [
                            "Compared to Meta Quest 3 at a fraction of the price, value just isn't there yet.",
                            "Quest 3 gives 80% of the experience for 15% of the price.",
                            "Hard to justify $3500 when Quest 3 has more content for $500.",
                        ],
                        "narrative": "Comparison-shopper persona overwhelmingly cites this. Pricing or positioning shift needed — hardware differentiation alone isn't winning the value argument.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "Accessory Pricing Adds Up",
                        "mentions": 22, "share_of_aspect_pct": 28.9, "share_of_all_pct": 5.3,
                        "avg_sentiment_stars": 1.9, "severity": "MEDIUM", "is_safety": False, "is_accessibility": False,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "process", "effort": "low"},
                        "sample_quotes": [
                            "Prescription lens insert is an extra $150 and you need it.",
                            "Travel case is another $200 — it adds up fast.",
                            "All the optional accessories push the real price over $4000.",
                        ],
                        "narrative": "Bundle pricing perception. Mainstream users feel nickel-and-dimed by accessory pricing. Bundled-edition or trade-in offers could soften this.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
            {
                "aspect": "comfort",
                "label": "Eye Strain & Comfort",
                "total_mentions": 67, "share_of_complaints_pct": 25.1, "share_of_all_pct": 16.3,
                "avg_sentiment_stars": 2.2, "verdict": "struggling",
                "sub_issues": [
                    {
                        "name": "Eye Strain After 45 Minutes",
                        "mentions": 38, "share_of_aspect_pct": 56.7, "share_of_all_pct": 9.2,
                        "avg_sentiment_stars": 2.0, "severity": "HIGH", "is_safety": False, "is_accessibility": True,
                        "personas_most_affected": ["mainstream", "professional"],
                        "fix_difficulty": {"category": "hardware", "effort": "high"},
                        "sample_quotes": [
                            "Eye strain after 45 minutes is real — had to take long breaks.",
                            "Eyes feel tired after extended sessions even with the right IPD.",
                            "Long viewing sessions cause noticeable eye fatigue.",
                        ],
                        "narrative": "Affects both mainstream and professional users equally. Accessibility-relevant for vision-sensitive users. Vergence-accommodation conflict is inherent to current XR optics — longer-term research problem.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                    {
                        "name": "IPD Adjustment Limitations",
                        "mentions": 18, "share_of_aspect_pct": 26.9, "share_of_all_pct": 4.4,
                        "avg_sentiment_stars": 2.3, "severity": "MEDIUM", "is_safety": False, "is_accessibility": True,
                        "personas_most_affected": ["mainstream"],
                        "fix_difficulty": {"category": "hardware", "effort": "medium"},
                        "sample_quotes": [
                            "IPD adjustment is too coarse — doesn't quite fit my eyes.",
                            "Wish IPD was continuous rather than stepped.",
                            "My IPD falls between two settings and it's noticeable.",
                        ],
                        "narrative": "Users with non-standard IPD lose viewing comfort. Continuous IPD adjustment would close the accessibility gap.",
                        "signals": {"geographic_hints": [], "temporal_hint": None, "version_hints": []},
                    },
                ],
            },
        ],
    }
    return by_product.get(key, [])


def _demo_smart_summary(key: str) -> Optional[Dict[str, Any]]:
    """Curated smart summary per demo product. Shows what a great narrative looks like."""
    by_product = {
        "tesla": {
            "consumer": {
                "headline": "Strong buy — but read the safety footnote",
                "summary": "Across 247 reviewers in 4 languages, the Model Y earns a 72/100 TrustScore (Strong). Long-term owners and tech enthusiasts love it for acceleration, OTA software updates, and the Supercharger network. The watch-outs are real though: phantom braking on Autopilot is a recurring safety complaint, and highway range is consistently below spec. 35% of reviewers recommend it; only 4% are returning. Recent sentiment is stable.",
                "key_takeaways": [
                    "Best-in-class acceleration (4.6★ / 88% positive)",
                    "Autopilot phantom braking is the #1 risk",
                    "Service center wait times causing friction",
                    "35% actively recommending, 4% returning",
                    "Owners love it long-term despite quirks",
                ],
                "recommendation": "Buy with awareness of Autopilot edge-cases and service-center wait times.",
            },
            "company": {
                "headline": "Healthy product, one urgent safety lever",
                "summary": "TrustScore sits at 72/100 (Strong) with 247 reviewers across 4 languages. Acceleration and OTA cadence are unmatched marketing assets. The urgent move is the Risk Register: phantom braking carries safety severity and should ship a fix in the next OTA regardless of share. Service-center friction is a brand drag on otherwise loyal long-term owners. Forecast: stable around 4.1★.",
                "key_takeaways": [
                    "#1 to fix: Phantom braking on Autopilot (CRITICAL/safety)",
                    "#2 to fix: Highway range vs spec gap",
                    "#1 to amplify: Best-in-Class Acceleration",
                    "Customer effort: 38.5 Moderate (service-driven)",
                    "Net buyer intent: +0.27 (healthy)",
                ],
                "strategic_priority": "Ship Autopilot phantom-braking mitigation in next OTA — safety-severity items can't wait for the v2.",
                "marketing_lead": "Lean on 'Best-in-Class Acceleration' — 58 reviewers back this claim with 91% positive language.",
            },
            "_source": "demo",
        },
        "sony": {
            "consumer": {
                "headline": "Strong buy — a confident audio pick",
                "summary": "178 reviewers consistently praise the WH-1000XM6 for sound quality, noise cancellation, and 30-hour battery. TrustScore is 82/100 (Strong) and the Risk Register is empty — no safety, reliability, or accessibility flags. The only soft spots are ear-cup warmth on long sessions and occasional app pairing issues. 48% of reviewers actively recommend it; just 2% are returning.",
                "key_takeaways": [
                    "Sound quality 4.7★ / 94% positive",
                    "Noise cancellation 4.6★ / 92% positive",
                    "Comfort dips after 3 hours",
                    "App reliability is the main nit",
                    "48% recommending, 2% returning — strong signal",
                ],
                "recommendation": "Buy with confidence — especially if you fly or work in open offices.",
            },
            "company": {
                "headline": "Healthy flagship — lean on it in marketing",
                "summary": "TrustScore 82/100 (Strong) across 178 reviewers. No critical or high-severity issues — the Risk Register is empty. Three marketing angles have direct quotable proof: sound, ANC, and battery life. The realistic next-version work is the companion app (occasional pairing drops) and ear-cup thermals on long sessions. Forecast trend is rising.",
                "key_takeaways": [
                    "No risks in Risk Register — clean profile",
                    "#1 to amplify: 'Best Sound Quality in Its Class'",
                    "Companion app pairing the only real bug",
                    "Customer effort: 18.7 Light — effortless journey",
                    "Net buyer intent: +0.43 (very strong)",
                ],
                "strategic_priority": "Ship a companion-app reliability sprint in Q1 — cheap fix, removes the last friction point.",
                "marketing_lead": "Lean on 'Best Sound Quality in Its Class' — 102 reviewers, 94% positive, ready-to-quote testimonials.",
            },
            "_source": "demo",
        },
        "vision": {
            "consumer": {
                "headline": "Wait — incredible tech, painful product",
                "summary": "412 reviewers paint a sharply divided picture. The micro-OLED display and gesture system are genuinely best-in-class, but weight, content gaps, and a $3500 price tag drag the TrustScore to 42/100 (Risky). Critic-persona reviewers struggle most (2.6★, 22% of audience). Customer Effort is Heavy at 72/100 — setup, learning curve, and eye strain affect 38% of buyers. 19% of reviewers are returning it. Forecast is declining.",
                "key_takeaways": [
                    "Display 4.4★ — best-in-class",
                    "Weight + neck fatigue is the #1 complaint",
                    "19% returning, 41% warning others off",
                    "Heavy effort: setup, learning curve, eye strain",
                    "Sentiment declining (-0.13★/week)",
                ],
                "recommendation": "Wait. Unless you're an early-adopter developer, the v2 will be a much better buy.",
            },
            "company": {
                "headline": "Crisis territory — reposition or rework",
                "summary": "TrustScore 42/100 (Risky) with sentiment declining at -0.13★/week. The hardware story is genuinely great (display, tracking) but it doesn't survive the weight + price + content-gap triangle. Customer effort score is 72.3 (Heavy) across all six friction categories. The Risk Register flags eye strain and weight as HIGH severity. Strategic question is bigger than feature work: this is a positioning problem.",
                "key_takeaways": [
                    "19% return rate — unsustainable",
                    "#1 to fix: Weight / neck fatigue (HIGH)",
                    "Heavy effort across 6 friction categories",
                    "Content library is the missing flywheel",
                    "Sentiment declining — act before it normalizes",
                ],
                "strategic_priority": "Bring weight under 450g and partner with Netflix/YouTube for native apps before v2 launch.",
                "marketing_lead": "Lean on 'Stunning Micro-OLED Displays' — 84 reviewers, 81% positive, the one universally-loved theme.",
            },
            "_source": "demo",
        },
    }
    return by_product.get(key)


def _demo_aspect_sentiment(key: str) -> Dict[str, Any]:
    """Curated ABSA data for the demo. Realistic numbers per product."""
    data = {
        "tesla": {
            "domain": "auto_ev",
            "aspects": [
                {"aspect": "battery", "mentions": 67, "avg_polarity": -0.42, "avg_sentiment_stars": 2.2, "pct_positive": 23, "pct_negative": 61, "pct_neutral": 16, "sample_positive": "Battery lasts perfectly on city drives.", "sample_negative": "Range drops 30% on highway speeds."},
                {"aspect": "acceleration", "mentions": 58, "avg_polarity": 0.81, "avg_sentiment_stars": 4.6, "pct_positive": 88, "pct_negative": 6, "pct_neutral": 6, "sample_positive": "Acceleration is unreal. Smiling every drive.", "sample_negative": ""},
                {"aspect": "autopilot", "mentions": 51, "avg_polarity": -0.18, "avg_sentiment_stars": 2.6, "pct_positive": 31, "pct_negative": 47, "pct_neutral": 22, "sample_positive": "Autopilot saved me on a long road trip.", "sample_negative": "Phantom braking on the highway scared me."},
                {"aspect": "software", "mentions": 44, "avg_polarity": 0.12, "avg_sentiment_stars": 3.2, "pct_positive": 49, "pct_negative": 33, "pct_neutral": 18, "sample_positive": "OTA updates keep adding new features.", "sample_negative": "Software updates broke my auto-wipers."},
                {"aspect": "service", "mentions": 28, "avg_polarity": -0.21, "avg_sentiment_stars": 2.6, "pct_positive": 36, "pct_negative": 50, "pct_neutral": 14, "sample_positive": "Service center fixed my issue quickly.", "sample_negative": "Service center wait times are getting long."},
                {"aspect": "interior", "mentions": 22, "avg_polarity": 0.45, "avg_sentiment_stars": 3.9, "pct_positive": 64, "pct_negative": 14, "pct_neutral": 22, "sample_positive": "Minimalist interior is gorgeous.", "sample_negative": "Trim quality varies by VIN."},
            ],
        },
        "sony": {
            "domain": "audio",
            "aspects": [
                {"aspect": "sound", "mentions": 102, "avg_polarity": 0.84, "avg_sentiment_stars": 4.7, "pct_positive": 91, "pct_negative": 3, "pct_neutral": 6, "sample_positive": "Sound quality is unbelievably good.", "sample_negative": ""},
                {"aspect": "noise_cancel", "mentions": 88, "avg_polarity": 0.78, "avg_sentiment_stars": 4.6, "pct_positive": 86, "pct_negative": 8, "pct_neutral": 6, "sample_positive": "Best ANC out there for plane noise.", "sample_negative": ""},
                {"aspect": "comfort", "mentions": 41, "avg_polarity": -0.05, "avg_sentiment_stars": 2.9, "pct_positive": 38, "pct_negative": 41, "pct_neutral": 21, "sample_positive": "Comfortable for an hour or two.", "sample_negative": "Ear cups get warm after 3 hours."},
                {"aspect": "app", "mentions": 32, "avg_polarity": -0.32, "avg_sentiment_stars": 2.4, "pct_positive": 22, "pct_negative": 56, "pct_neutral": 22, "sample_positive": "App lets you tune the EQ nicely.", "sample_negative": "App still randomly forgets the pairing."},
                {"aspect": "battery", "mentions": 28, "avg_polarity": 0.68, "avg_sentiment_stars": 4.4, "pct_positive": 79, "pct_negative": 11, "pct_neutral": 10, "sample_positive": "30 hours of battery is plenty.", "sample_negative": ""},
                {"aspect": "price", "mentions": 24, "avg_polarity": -0.25, "avg_sentiment_stars": 2.5, "pct_positive": 25, "pct_negative": 50, "pct_neutral": 25, "sample_positive": "Worth every penny for travelers.", "sample_negative": "Price feels steep next to the XM5."},
            ],
        },
        "vision": {
            "domain": "xr_vr",
            "aspects": [
                {"aspect": "weight", "mentions": 121, "avg_polarity": -0.72, "avg_sentiment_stars": 1.6, "pct_positive": 8, "pct_negative": 84, "pct_neutral": 8, "sample_positive": "", "sample_negative": "Weight causes neck fatigue after 30 minutes."},
                {"aspect": "content", "mentions": 98, "avg_polarity": -0.51, "avg_sentiment_stars": 2.0, "pct_positive": 17, "pct_negative": 68, "pct_neutral": 15, "sample_positive": "Some apps are genuinely magical.", "sample_negative": "Native Netflix and YouTube apps are non-negotiable."},
                {"aspect": "display", "mentions": 84, "avg_polarity": 0.72, "avg_sentiment_stars": 4.4, "pct_positive": 81, "pct_negative": 9, "pct_neutral": 10, "sample_positive": "The micro-OLED displays are next-level.", "sample_negative": ""},
                {"aspect": "price", "mentions": 76, "avg_polarity": -0.61, "avg_sentiment_stars": 1.8, "pct_positive": 11, "pct_negative": 75, "pct_neutral": 14, "sample_positive": "Worth it for early adopters.", "sample_negative": "Not worth $3500 without content."},
                {"aspect": "comfort", "mentions": 67, "avg_polarity": -0.38, "avg_sentiment_stars": 2.2, "pct_positive": 23, "pct_negative": 61, "pct_neutral": 16, "sample_positive": "", "sample_negative": "Eye strain after 45 minutes is real."},
                {"aspect": "tracking", "mentions": 38, "avg_polarity": 0.55, "avg_sentiment_stars": 4.1, "pct_positive": 71, "pct_negative": 16, "pct_neutral": 13, "sample_positive": "Eye + pinch interaction feels genuinely futuristic.", "sample_negative": "In-air keyboard typing accuracy needs work."},
            ],
        },
    }
    return data.get(key, {"domain": None, "aspects": []})


def _demo_buyer_intent(key: str) -> Dict[str, Any]:
    """Curated buyer intent distributions per demo product."""
    profiles = {
        "tesla":  {"BUY": 18, "OWN": 42, "RETURN": 4,  "RECOMMEND": 35, "AVOID": 6,  "WAIT": 12, "COMPARE": 14, "UNKNOWN": 116},
        "sony":   {"BUY": 12, "OWN": 38, "RETURN": 2,  "RECOMMEND": 48, "AVOID": 2,  "WAIT": 5,  "COMPARE": 9,  "UNKNOWN": 62},
        "vision": {"BUY": 8,  "OWN": 28, "RETURN": 19, "RECOMMEND": 14, "AVOID": 41, "WAIT": 47, "COMPARE": 22, "UNKNOWN": 233},
    }
    counts = profiles.get(key, {})
    total = max(1, sum(counts.values()))
    label_pretty = {
        "BUY": "Buying / ordered", "OWN": "Owns it", "RETURN": "Returning / refunding",
        "RECOMMEND": "Recommending", "AVOID": "Warning others off",
        "WAIT": "Waiting for next version", "COMPARE": "Comparing alternatives",
        "UNKNOWN": "No stated action",
    }
    label_tone = {
        "BUY": "blue", "OWN": "zinc", "RETURN": "red", "RECOMMEND": "green",
        "AVOID": "red", "WAIT": "amber", "COMPARE": "indigo", "UNKNOWN": "zinc",
    }
    distribution = []
    for label in ["BUY", "OWN", "RETURN", "RECOMMEND", "AVOID", "WAIT", "COMPARE", "UNKNOWN"]:
        c = counts.get(label, 0)
        distribution.append({
            "label": label, "pretty": label_pretty[label], "count": c,
            "pct": round(100 * c / total, 1), "tone": label_tone[label],
        })
    compared_targets = {
        "tesla": [("Ford Lightning", 6), ("Rivian R1S", 4), ("BMW iX", 3)],
        "sony":  [("Bose QC Ultra", 5), ("Sony WH-1000XM5", 4), ("Sennheiser Momentum 4", 2)],
        "vision":[("Meta Quest 3", 12), ("PSVR2", 4), ("Magic Leap 2", 2)],
    }.get(key, [])
    buy = counts.get("BUY", 0) / total
    ret = counts.get("RETURN", 0) / total
    rec = counts.get("RECOMMEND", 0) / total
    avo = counts.get("AVOID", 0) / total
    return {
        "distribution": distribution,
        "compared_products": [{"name": n, "count": c} for n, c in compared_targets],
        "decision_health": {
            "buy_pct": round(buy * 100, 1),
            "return_pct": round(ret * 100, 1),
            "recommend_pct": round(rec * 100, 1),
            "avoid_pct": round(avo * 100, 1),
            "net_intent": round((rec + buy) - (avo + ret), 3),
        },
    }


router = APIRouter()

@router.get("/demo/report", summary="Pre-built demo final_report (no API keys required)")
def demo_report(product: str = "tesla") -> Dict[str, Any]:
    key = (product or "").strip().lower()
    aliases = {"tesla": "tesla", "model y": "tesla", "tesla model y": "tesla",
               "sony": "sony", "wh-1000xm6": "sony", "sony wh-1000xm6": "sony",
               "vision": "vision", "apple vision pro": "vision", "vision pro": "vision"}
    key = aliases.get(key, key)
    if key not in _PROFILE:
        raise HTTPException(404, f"No demo for '{product}'. Available: {list(_PROFILE.keys())}")
    return {"final_report": build_demo_report(key)}


@router.get("/demo/products")
def demo_products() -> Dict[str, Any]:
    return {
        "products": [
            {"key": k, "name": _PROFILE[k]["query"], "n_kept": _PROFILE[k]["n_kept"], "avg_sent": _PROFILE[k]["avg_sent"]}
            for k in _PROFILE
        ]
    }
