# CLAUDE.md — InsightMesh AI: Reviewer Credibility Intelligence

## ⚠️ CRITICAL RULES
1. Read `docs/CLAUDE_ONBOARDING.md` for full context.
2. No quick_mode guards. All modes run identical analysis.
3. NEVER full-read `analyze_reviews.py`. Targeted grep/line-range only.
4. NEVER delete llm_cache.db. Only delete insightmesh.db if schema changed.
5. `is_fast = FAST_MODE or depth == "quick"` — PERMANENT.
6. MAX_UNDERSTANDING_PER_RUN=15 in .env.
7. LLM_PROVIDER=openai, LLM_MODEL=gpt-4o-mini.

## What's Already Built (do NOT rebuild)
All 10 intelligence modules, 6-layer noise filtering, smart sampler, 
multi-agent debate, dual-view dashboard, competitive intel, deal-breaker 
detector, purchase advisor, coverage intelligence, self-corrector.

---

## THE BUILD: Reviewer Credibility Intelligence

### Why This Matters

Right now "trash product lol" from a non-owner counts the same as a 200-word 
detailed review from a 6-month owner who switched from a competitor. Every 
aggregation — sentiment average, cluster sizes, aspect ratings, roadmap 
prioritization — treats all reviews equally. That's wrong.

Reviewer Credibility Scoring weights every reviewer on how much their opinion 
should be trusted. This single change makes EVERY existing feature smarter:
- Sentiment average becomes credibility-weighted (more accurate)
- Cluster importance is weighted by credible reviewers (real complaints rise)
- Aspect ratings weight credible mentions higher (less noise)
- Voice of the Customer shows the most credible reviewers first
- The executive summary can say "Credible reviewers rate 3.8★ vs 2.1★ from 
  casual commenters — the product is better than the raw average suggests"

### Architecture

**New file:** `backend/insight/intelligence/credibility_scorer.py`

Pure computation — NO LLM calls. Uses signals already in per_review data 
from the deep classifier and understanding module.

### Credibility Formula

Each reviewer gets a credibility score (0.0 - 1.0) from 5 signals:

```python
def score_credibility(review: dict, all_reviews: list) -> dict:
    text = review.get("translated_text") or review.get("original", "")
    understanding = review.get("understanding") or {}
    deep = review.get("deep_signals") or {}
    
    # 1. OWNERSHIP EVIDENCE (0-0.25)
    # Does this person actually own/use the product?
    ownership = 0.0
    experience_stage = deep.get("experience_stage", "") or understanding.get("experience_stage", "")
    
    if experience_stage in ("long_term", "expert", "repeat_buyer"):
        ownership = 0.25
    elif experience_stage in ("short_term",):
        ownership = 0.20
    elif experience_stage in ("first_impression",):
        ownership = 0.10
    else:
        # Check text for ownership language
        import re
        has_own = bool(re.search(
            r'(i (bought|purchased|own|have|got|ordered|received|use daily|returned))',
            text, re.I
        ))
        has_duration = bool(re.search(
            r'(for|after|since)\s+\d+\s*(day|week|month|year)',
            text, re.I
        ))
        if has_own and has_duration:
            ownership = 0.20
        elif has_own:
            ownership = 0.12
        elif has_duration:
            ownership = 0.10
        else:
            ownership = 0.02  # no evidence of ownership
    
    # 2. SPECIFICITY OF CLAIMS (0-0.25)
    # Verifiable claims > vague opinions
    specificity = 0.0
    claims = deep.get("verified_claims") or []
    opinions_only = deep.get("claims_vs_opinions", "") == "opinion_only"
    
    has_measurements = bool(re.search(
        r'\d+\s*(hour|hr|min|gb|tb|inch|mm|fps|hz|ms|mbps|percent|%|dollar|\$)',
        text, re.I
    ))
    has_specific_feature = bool(re.search(
        r'(battery|display|screen|trackpad|joystick|trigger|speaker|mic|wifi|bluetooth|usb|hdmi|dock|fan|thermal|fps|resolution)',
        text, re.I
    ))
    has_comparison_detail = bool(re.search(
        r'(compared to|better than|worse than|faster than|slower than|unlike|whereas)',
        text, re.I
    ))
    
    if claims and len(claims) >= 2:
        specificity = 0.25
    elif has_measurements and has_specific_feature:
        specificity = 0.22
    elif has_measurements or has_comparison_detail:
        specificity = 0.15
    elif has_specific_feature:
        specificity = 0.10
    elif opinions_only:
        specificity = 0.03
    else:
        specificity = 0.05
    
    # 3. REVIEW DEPTH (0-0.20)
    # Length + structure indicate effort and thoughtfulness
    word_count = len(text.split())
    
    if word_count >= 100:
        depth = 0.20
    elif word_count >= 60:
        depth = 0.16
    elif word_count >= 30:
        depth = 0.10
    elif word_count >= 15:
        depth = 0.06
    else:
        depth = 0.02
    
    # Bonus for structured reviews (pros/cons, multiple aspects)
    has_structure = bool(re.search(r'(pros?:|cons?:|however|but|although|on the other hand)', text, re.I))
    aspects_mentioned = sum(1 for a in ["battery", "display", "price", "comfort", "weight", 
                                         "performance", "build", "software", "content", "design",
                                         "screen", "audio", "camera", "storage", "charging"]
                          if a in text.lower())
    if has_structure:
        depth = min(0.20, depth + 0.04)
    if aspects_mentioned >= 3:
        depth = min(0.20, depth + 0.03)
    
    # 4. EMOTIONAL CALIBRATION (0-0.15)
    # Moderate, nuanced opinions > extreme, one-sided rants
    # A credible reviewer acknowledges both positives and negatives
    calibration = 0.0
    
    intents = deep.get("multi_intent") or understanding.get("intents") or []
    has_praise = any(i in ["praise", "recommendation"] for i in intents) if isinstance(intents, list) else "praise" in str(intents).lower()
    has_complaint = any(i in ["complaint", "criticism"] for i in intents) if isinstance(intents, list) else "complaint" in str(intents).lower()
    
    if has_praise and has_complaint:
        calibration = 0.15  # Acknowledges both sides = high credibility
    elif has_praise or has_complaint:
        calibration = 0.08  # One-sided but at least has a clear stance
    else:
        calibration = 0.04  # No clear intent
    
    # Extreme emotion with no substance = low credibility
    emotion = review.get("emotion", {})
    emotion_score = emotion.get("score", 0) if isinstance(emotion, dict) else 0
    if emotion_score > 0.9 and word_count < 20:
        calibration = max(0.0, calibration - 0.05)  # Extreme emotion + short = rant
    
    # 5. PLATFORM CREDIBILITY BASELINE (0-0.15)
    # App Store reviews have higher baseline (chose to write a review)
    # Reddit in product subreddits = engaged community members
    # YouTube comments = lowest baseline (casual, often reactive)
    platform = (review.get("platform") or "").lower()
    
    if platform == "appstore":
        platform_score = 0.12
    elif platform == "reddit":
        has_subreddit = bool(review.get("subreddit"))
        platform_score = 0.11 if has_subreddit else 0.08
    elif platform == "youtube":
        platform_score = 0.05  # lowest baseline — most YouTube comments are reactive
    else:
        platform_score = 0.07
    
    # Composite
    raw = ownership + specificity + depth + calibration + platform_score
    composite = round(min(0.95, raw), 2)
    
    # Label
    if composite >= 0.60:
        label = "HIGH"
    elif composite >= 0.35:
        label = "MEDIUM"
    else:
        label = "LOW"
    
    return {
        "credibility": composite,
        "credibility_label": label,
        "factors": {
            "ownership": round(ownership, 2),
            "specificity": round(specificity, 2),
            "depth": round(depth, 2),
            "calibration": round(calibration, 2),
            "platform": round(platform_score, 2),
        }
    }
```

### Credibility-Weighted Aggregations

After scoring all reviewers, compute weighted versions of key metrics:

```python
def compute_weighted_metrics(per_review: list) -> dict:
    """
    Compute credibility-weighted alternatives to raw averages.
    Returns metrics that can be added to the overview.
    """
    weighted_sentiment_sum = 0.0
    weight_sum = 0.0
    
    credible_reviews = []  # credibility >= 0.5
    casual_reviews = []    # credibility < 0.3
    
    for review in per_review:
        cred = review.get("_credibility", {}).get("credibility", 0.5)
        stars = review.get("sentiment_score", 3.0)
        
        weighted_sentiment_sum += stars * cred
        weight_sum += cred
        
        if cred >= 0.5:
            credible_reviews.append(review)
        elif cred < 0.3:
            casual_reviews.append(review)
    
    weighted_avg = round(weighted_sentiment_sum / max(0.01, weight_sum), 2)
    raw_avg = round(sum(r.get("sentiment_score", 3) for r in per_review) / max(1, len(per_review)), 2)
    
    # Credible vs casual sentiment gap
    credible_avg = round(sum(r.get("sentiment_score", 3) for r in credible_reviews) / max(1, len(credible_reviews)), 2) if credible_reviews else None
    casual_avg = round(sum(r.get("sentiment_score", 3) for r in casual_reviews) / max(1, len(casual_reviews)), 2) if casual_reviews else None
    
    gap = round(credible_avg - casual_avg, 2) if credible_avg is not None and casual_avg is not None else None
    
    # Insight generation
    insight = None
    if gap is not None and abs(gap) >= 0.5:
        if gap > 0:
            insight = f"Credible reviewers rate {gap:.1f}\u2605 higher than casual commenters \u2014 the product is better than the raw average suggests."
        else:
            insight = f"Credible reviewers rate {abs(gap):.1f}\u2605 lower than casual commenters \u2014 the product may be worse than the raw average suggests."
    
    return {
        "weighted_sentiment": weighted_avg,
        "raw_sentiment": raw_avg,
        "sentiment_gap": gap,
        "credible_count": len(credible_reviews),
        "casual_count": len(casual_reviews),
        "credible_avg": credible_avg,
        "casual_avg": casual_avg,
        "insight": insight,
        "credibility_distribution": {
            "high": sum(1 for r in per_review if r.get("_credibility", {}).get("credibility_label") == "HIGH"),
            "medium": sum(1 for r in per_review if r.get("_credibility", {}).get("credibility_label") == "MEDIUM"),
            "low": sum(1 for r in per_review if r.get("_credibility", {}).get("credibility_label") == "LOW"),
        }
    }
```

### Wiring

In `analyze_reviews.py`, after the per-review loop but before clustering/overview:

```python
# Score reviewer credibility (pure computation, no LLM)
from backend.insight.intelligence.credibility_scorer import score_credibility, compute_weighted_metrics

for review in results:
    review["_credibility"] = score_credibility(review, results)

# Compute weighted metrics for the overview
credibility_metrics = compute_weighted_metrics(results)
```

Then in the overview assembly, add:
```python
overview["credibility_intelligence"] = credibility_metrics
```

### Frontend Changes (Insights.jsx)

#### 1. Credibility badge on review cards
In the review browser, show a small credibility indicator on each review card:
- HIGH credibility: green shield icon + "verified depth" tag
- MEDIUM: no special indicator (default)
- LOW: muted opacity (0.7)

```jsx
{review._credibility?.credibility_label === "HIGH" && (
    <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-900/40 
                     text-emerald-400 border border-emerald-800">
        verified depth
    </span>
)}
```

#### 2. Credibility Intelligence card (new section)
Show the credibility-weighted sentiment vs raw sentiment:

```jsx
{overview.credibility_intelligence && (
    <Card title="Reviewer credibility" 
          subtitle="Weighted by ownership evidence, specificity, and review depth">
        <div className="grid grid-cols-3 gap-4">
            <div>
                <div className="text-xs text-zinc-500">RAW AVERAGE</div>
                <div className="text-2xl font-bold">
                    {credibility.raw_sentiment}\u2605
                </div>
                <div className="text-xs text-zinc-500">all {total} reviewers</div>
            </div>
            <div>
                <div className="text-xs text-zinc-500">CREDIBILITY-WEIGHTED</div>
                <div className="text-2xl font-bold text-violet-400">
                    {credibility.weighted_sentiment}\u2605
                </div>
                <div className="text-xs text-zinc-500">weighted by credibility</div>
            </div>
            <div>
                <div className="text-xs text-zinc-500">CREDIBLE REVIEWERS ONLY</div>
                <div className="text-2xl font-bold text-emerald-400">
                    {credibility.credible_avg}\u2605
                </div>
                <div className="text-xs text-zinc-500">
                    {credibility.credible_count} verified-depth reviewers
                </div>
            </div>
        </div>
        
        {/* Credibility distribution bar */}
        <div className="mt-4 flex gap-1 h-2 rounded overflow-hidden">
            <div className="bg-emerald-500" style={{width: `${highPct}%`}} />
            <div className="bg-amber-500" style={{width: `${medPct}%`}} />
            <div className="bg-zinc-600" style={{width: `${lowPct}%`}} />
        </div>
        <div className="flex justify-between text-xs text-zinc-500 mt-1">
            <span>HIGH {dist.high}</span>
            <span>MEDIUM {dist.medium}</span>
            <span>LOW {dist.low}</span>
        </div>
        
        {/* Gap insight */}
        {credibility.insight && (
            <div className="mt-3 p-2 rounded-lg bg-violet-950/20 border 
                          border-violet-900/30 text-sm text-violet-300">
                {credibility.insight}
            </div>
        )}
    </Card>
)}
```

#### 3. Voice of Customer sorting update
The Voice of the Customer section currently sorts by review intelligence score.
Add a secondary sort: within the same intelligence tier, sort by credibility.
This ensures the most credible AND most informative reviews appear first.

#### 4. Cross-insight: credibility gap
If the credibility gap (credible_avg - casual_avg) is >= 0.8 stars, add a 
cross-insight callout:

```python
# In synthesizer.py find_cross_insights:
cred = overview.get("credibility_intelligence", {})
gap = cred.get("sentiment_gap")
if gap is not None and abs(gap) >= 0.8:
    if gap > 0:
        insights.append({
            "type": "credibility_gap_positive",
            "description": f"Credible reviewers (ownership + detail) rate {gap:.1f}\u2605 higher than casual commenters. The raw average undersells this product.",
            "sections": ["sentiment", "credibility"],
            "severity": "insight"
        })
    else:
        insights.append({
            "type": "credibility_gap_negative", 
            "description": f"Credible reviewers rate {abs(gap):.1f}\u2605 lower than casual commenters. The raw average oversells this product.",
            "sections": ["sentiment", "credibility"],
            "severity": "warning"
        })
```

### Testing

Delete insightmesh.db only. Restart backend. Test:
1. Apple Vision Pro Quick — verify credibility scores on review cards
2. Check the Credibility Intelligence card shows raw vs weighted vs credible-only sentiment
3. Check distribution bar (HIGH/MEDIUM/LOW counts)
4. Check if a credibility gap insight appears as a cross-insight callout
5. Voice of Customer should show high-credibility reviews more prominently

### What NOT to Do
- No LLM calls in the credibility scorer. Pure computation from existing signals.
- No quick_mode guards.
- Don't delete llm_cache.db.
- Don't change existing per_review fields. Only ADD _credibility.
- Fail-open: if credibility scoring errors, reviews render without badges.

---

## Start Command

```powershell
cd D:\IM_AI_folder
claude
```

"Read CLAUDE.md completely. Build the Reviewer Credibility Intelligence system.
Create backend/insight/intelligence/credibility_scorer.py with the 5-factor
credibility formula (ownership, specificity, depth, calibration, platform).
Wire it into analyze_reviews.py after the per-review loop. Compute weighted
metrics for the overview. Build the Credibility Intelligence card in Insights.jsx
showing raw vs weighted vs credible-only sentiment with a distribution bar.
Add credibility badges on review cards in the review browser. Add the credibility
gap cross-insight to synthesizer.py. Delete insightmesh.db only, restart,
test Apple Vision Pro Quick. Verify credibility scores appear on reviews,
the card renders with the sentiment comparison, and the gap insight fires
if applicable."
