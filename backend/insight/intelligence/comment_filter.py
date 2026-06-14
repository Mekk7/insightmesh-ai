"""
Platform-aware comment pre-filter.
Removes noise BEFORE any expensive processing.
Returns (kept_comments, filtered_stats).

This is ADDITIVE to deep classification, not a replacement: comments that pass
this fast regex pre-filter still get deep-classified in Balanced/Deep. The goal is
to cheaply drop comments that carry zero product signal (video reactions, emoji,
device lists, one-word reactions) before any transformer/LLM call. Bias toward
KEEPING — when in doubt, keep (see CLAUDE.md). The caller is responsible for the
never-filter-to-empty fail-safe.
"""
import re
from typing import List, Dict, Tuple, Any

# ── YouTube noise patterns ──
# YouTube comments are short, sarcastic, emoji-heavy.
# The useful signal is 10-20% of comments. The rest is noise.

YOUTUBE_NOISE_PATTERNS = [
    # Video reactions (not about the product)
    r'^(first|first!|first comment)',
    r'(like and subscribe|smash.*(like|bell)|notification squad)',
    r'^(who\'?s? (here|watching)|anyone (else|here)|came here from)',
    r'^(underrated|overrated|W video|L video|goat|legend)',

    # Pure timestamp references (reacting to video moments, not product)
    r'^\d{1,2}:\d{2}\b(?!.*\b(battery|screen|display|price|comfort|weight))',

    # Emoji-only or near-empty
    r'^[\s\U0001F000-\U0001FFFF☀-➿︀-️]+$',

    # Bot/spam patterns
    r'(check out my|subscribe to my|follow me|dm me|link in bio)',
    r'(earn \$|make money|crypto|nft|giveaway)',

    # Self-promotion
    r'(my channel|my video|i made a video)',
]

# ── Reddit noise patterns ──
# Reddit comments are threaded. Top-level comments are usually substantive.
# Short replies ("this", "lol", "based") are noise.

REDDIT_NOISE_PATTERNS = [
    r'^(this|same|facts|based|mood|ratio|underrated comment|came here to say this)\.?$',
    r'^(lol|lmao|rofl|haha|bruh|bro|fr fr|no cap|ong|deadass)\.?$',
    r'^\^+this',  # "^this" reddit agreement pattern
    r'^(edit|update|eta):',  # meta-comments about the comment itself
    r'(happy cake day|username checks out|r/\w+)',  # reddit meta
]

# ── App Store noise patterns ──
# App Store reviews are usually product-relevant but can be one-word.

APPSTORE_NOISE_PATTERNS = [
    r'^(good|bad|ok|nice|great|terrible|awful|amazing|love it|hate it)\.?$',
    r'^(\.+|\*+|n\/a|na|none|no comment)$',
]

# ── Universal quality checks ──
MIN_WORD_COUNT = 5  # Below this = no useful signal
MAX_PRODUCT_LIST_RATIO = 0.6  # If >60% of words are product names, it's a device list

DEVICE_NAMES = {
    'iphone', 'ipad', 'macbook', 'imac', 'airpods', 'apple watch', 'homepod',
    'mac mini', 'mac pro', 'ipod', 'apple tv', 'vision pro',
    'tesla', 'model y', 'model 3', 'model s', 'model x', 'cybertruck',
    'galaxy', 'pixel', 'oneplus', 'samsung', 'huawei', 'xiaomi',
    'ps5', 'playstation', 'xbox', 'nintendo', 'switch', 'steam deck',
    'meta quest', 'quest pro', 'quest 3', 'hololens', 'psvr', 'valve index',
    'alexa', 'echo', 'google home', 'nest', 'ring', 'chromebook',
    'kindle', 'fire tablet', 'roku', 'apple tv', 'chromecast',
    'beats', 'bose', 'sony wh', 'airpods max', 'airpods pro',
}


def detect_platform(meta: dict) -> str:
    """Detect which platform a comment came from."""
    platform = (meta.get("platform") or "").lower()
    if platform:
        return platform
    if meta.get("subreddit"):
        return "reddit"
    if meta.get("video_id"):
        return "youtube"
    if meta.get("app_id") or meta.get("store"):
        return "appstore"
    return "unknown"


def is_device_list(text: str) -> bool:
    """Detect if a comment is just listing devices with no opinion."""
    words = text.lower().split()
    if len(words) < 8:
        return False
    device_word_count = 0
    lowered = text.lower()
    for device in DEVICE_NAMES:
        if device in lowered:
            device_word_count += len(device.split())
    return device_word_count / max(1, len(words)) > MAX_PRODUCT_LIST_RATIO


def filter_comment(text: str, meta: dict) -> Tuple[bool, str]:
    """
    Returns (keep: bool, reason: str).
    reason is empty if kept, or a short description of why filtered.
    """
    text = (text or "").strip()

    # Too short
    word_count = len(text.split())
    if word_count < MIN_WORD_COUNT:
        return False, "too_short"

    # Device list without opinion
    if is_device_list(text):
        return False, "device_list"

    # Platform-specific noise
    platform = detect_platform(meta)
    patterns = []
    if platform == "youtube":
        patterns = YOUTUBE_NOISE_PATTERNS
    elif platform == "reddit":
        patterns = REDDIT_NOISE_PATTERNS
    elif platform == "appstore":
        patterns = APPSTORE_NOISE_PATTERNS

    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return False, f"noise_{platform}"

    return True, ""


def filter_batch(comments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Filter a batch of comments. Returns (kept, stats).
    stats = {"total": N, "kept": N, "too_short": N, "noise_youtube": N, ...}
    """
    kept = []
    stats = {"total": len(comments), "kept": 0}

    for comment in comments:
        text = comment.get("text", comment.get("original", ""))
        meta = comment.get("meta", comment)
        ok, reason = filter_comment(text, meta)
        if ok:
            kept.append(comment)
            stats["kept"] = stats.get("kept", 0) + 1
        else:
            stats[reason] = stats.get(reason, 0) + 1

    return kept, stats
