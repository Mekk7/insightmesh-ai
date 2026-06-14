# backend/utils/filtering.py

import os
import re
from typing import List, Tuple, Dict, Optional
from langdetect import detect, DetectorFactory

# ---------------------------------------------------------------------
# Deterministic language detection
DetectorFactory.seed = 0

# ---- Global defaults (env-tunable, still exported for BC) ----------
MIN_LEN = int(os.getenv("FILTER_MIN_LEN", "6"))             # min chars to keep (base)
MAX_LEN = int(os.getenv("FILTER_MAX_LEN", "1200"))          # max chars (drop walls of text)
ALPHA_MIN = float(os.getenv("FILTER_ALPHA_MIN", "0.05"))    # require >=5% alphabetic chars
DROP_URL_ONLY = os.getenv("FILTER_DROP_URL_ONLY", "1") in {"1", "true", "True"}
DEFAULT_STRICTNESS = os.getenv("FILTER_STRICTNESS", "normal").strip().lower()  # low|normal|high

# Default language allowlist (ISO 639-1 mostly; langdetect returns some variants)
DEFAULT_LANGS = {
    "en","hi","ru","zh-cn","zh-tw","es","fr","de","pt","it",
    "id","tr","ko","ja","af","nl","sv","pl","cs","ro","vi",
    "uk","ar","bn","ta","te","mr","fa","he"
}
# Allow override with comma-separated env (e.g., FILTER_LANGS="en,es,fr")
_langs_env = os.getenv("FILTER_LANGS")
WHITELIST_LANGUAGES = (
    {x.strip().lower() for x in _langs_env.split(",")} if _langs_env else DEFAULT_LANGS
)

# ---- Patterns --------------------------------------------------------
NON_TEXT_PATTERN = re.compile(r"^[\W_]+$", re.UNICODE)  # glyph/emoji/punct-only (no letters/digits)
URL_ONLY_PATTERN = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
URL_ANY_PATTERN  = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
AT_HANDLE_HEAVY  = re.compile(r"(?:^|\s)@[A-Za-z0-9_]{2,}")
HASHTAG_HEAVY    = re.compile(r"(?:^|\s)#[^\s#]{2,}")
REPEAT_CHAR_RUN  = re.compile(r"(.)\1{4,}")                 # e.g., loooool, !!!!!!

# Important: restrict to **Latin** consonants only to avoid nuking non-Latin scripts
ONLY_LATIN_CONSONANTS = re.compile(r"^(?i:[b-df-hj-np-tv-z]{8,})$")

# Emoji ranges (coarse but effective, keeps CJK letters)
EMOJI_PATTERN = re.compile(
    "["                                     # begin char class
    "\U0001F300-\U0001FAFF"                 # emojis & symbols
    "\U00002700-\U000027BF"                 # dingbats
    "\U00002600-\U000026FF"                 # misc symbols
    "]",
    flags=re.UNICODE
)

# Lightweight normalizer for dedupe keys: collapse whitespace, strip, casefold
def _norm_text_for_key(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t.casefold()

# --- Language helpers -------------------------------------------------
def _normalize_lang_code(code: str) -> str:
    """
    Normalize langdetect outputs like 'pt-BR' → 'pt-br' and expose primary subtag.
    """
    code = (code or "").strip().lower()
    return code

def _lang_allowed(code: str) -> bool:
    if not code:
        return False
    c = _normalize_lang_code(code)
    if c in WHITELIST_LANGUAGES:
        return True
    base = c.split("-")[0]
    return base in WHITELIST_LANGUAGES

def detect_lang(text: str) -> str:
    try:
        return _normalize_lang_code(detect((text or "").strip()))
    except Exception:
        return "unk"

# --- Signal helpers ---------------------------------------------------
def _alpha_ratio(text: str) -> float:
    t = (text or "")
    if not t:
        return 0.0
    alpha = sum(ch.isalpha() for ch in t)
    return alpha / max(1, len(t))

def _emoji_ratio(text: str) -> float:
    if not text:
        return 0.0
    emojis = len(EMOJI_PATTERN.findall(text))
    return emojis / max(1, len(text))

def _unique_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    s = set(text.replace(" ", ""))
    return len(s) / max(1, len(text))

def _strip_soft_noise(text: str) -> str:
    """Remove URLs and compress whitespace (for checks only; never returned to caller)."""
    t = URL_ANY_PATTERN.sub(" ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _unicode_alpha_word_count(text: str) -> int:
    """
    Count 'words' with at least two alphabetic (Unicode) characters.
    Supports non-Latin scripts (Cyrillic, CJK, etc.).
    """
    t = _strip_soft_noise(text)
    # Split on whitespace; keep tokens that contain >=2 alphabetic chars
    count = 0
    for tok in re.findall(r"\S+", t, flags=re.UNICODE):
        if sum(ch.isalpha() for ch in tok) >= 2:
            count += 1
    return count

# ---- Strictness profiles ---------------------------------------------
# Each profile defines thresholds beyond the base env defaults.
STRICT_PROFILES = {
    "low": {
        "min_len": max(4, MIN_LEN),
        "min_words": 2,
        "alpha_min": max(0.03, ALPHA_MIN * 0.6),
        "max_emoji_ratio": 0.45,
        "min_unique_ratio": 0.02,
        "drop_hashtag_spam": False,
        "drop_at_spam": False,
    },
    "normal": {
        "min_len": max(6, MIN_LEN),
        "min_words": 3,
        "alpha_min": max(0.05, ALPHA_MIN),
        "max_emoji_ratio": 0.35,
        "min_unique_ratio": 0.03,
        "drop_hashtag_spam": True,
        "drop_at_spam": True,
    },
    "high": {
        "min_len": max(10, MIN_LEN),
        "min_words": 4,
        "alpha_min": max(0.08, ALPHA_MIN * 1.5),
        "max_emoji_ratio": 0.25,
        "min_unique_ratio": 0.05,
        "drop_hashtag_spam": True,
        "drop_at_spam": True,
    },
}

# Spammy/low-signal phrases (case-insensitive). Keep short list; we avoid nuking legit content.
SPAM_PHRASES = [
    "subscribe", "follow me", "giveaway", "promo code", "discount code",
    "link in bio", "dm me", "check my profile", "cashapp", "venmo", "onlyfans",
    "first comment", "pin this", "pin me", "early gang", "who's here in 20",
]

# Classic ASCII emoticons that the unicode emoji regex misses
_ASCII_EMOTICON_RE = re.compile(r"(?::|;|=)(?:'|-)?(?:\)|\(|D|P|p|\||\*|\$|/|\\|\[|\])+")

# Try to import the quality module for is_shallow; tolerate absence
try:
    from backend.utils.quality import is_shallow as _is_shallow_quality
except Exception:
    _is_shallow_quality = None

def _pick_profile(strictness: Optional[str]) -> Dict[str, object]:
    s = (strictness or DEFAULT_STRICTNESS or "normal").lower()
    return STRICT_PROFILES.get(s, STRICT_PROFILES["normal"])

def is_clean_sentence(text: str) -> bool:
    """
    Lightweight gate used by some callers. Kept for backward compatibility.
    Uses DEFAULT_STRICTNESS.
    """
    ok, _, _ = validate_with_reason(text, strictness=None)
    return ok

def validate_with_reason(text: str, strictness: Optional[str] = None) -> Tuple[bool, str, str]:
    """
    Return (ok, reason, lang)

    reasons:
      - ok
      - too_short
      - too_long
      - glyph_noise        (emoji/symbol-only)
      - url_only
      - low_alpha          (< alpha_min alphabetic chars)
      - too_few_words      (< min_words)
      - emoji_heavy        (> max_emoji_ratio)
      - keyboard_smash     (long repeats or Latin consonant-only strings)
      - low_unique_chars   (< min_unique_ratio)
      - hashtag_spam
      - at_mention_spam
      - spam_phrase
      - lang_not_whitelisted
    """
    prof = _pick_profile(strictness)
    t = (text or "").strip()

    # Fast exits
    if len(t) < int(prof["min_len"]):
        return False, "too_short", "unk"
    if len(t) > MAX_LEN:
        return False, "too_long", "unk"
    if NON_TEXT_PATTERN.match(t):
        return False, "glyph_noise", "unk"
    if DROP_URL_ONLY and URL_ONLY_PATTERN.match(t):
        return False, "url_only", "unk"

    # Soft normalization for checks
    t_soft = _strip_soft_noise(t)

    # Signal checks
    if _alpha_ratio(t_soft) < float(prof["alpha_min"]):
        return False, "low_alpha", "unk"
    if _unicode_alpha_word_count(t_soft) < int(prof["min_words"]):
        return False, "too_few_words", "unk"
    if _emoji_ratio(t) > float(prof["max_emoji_ratio"]):
        return False, "emoji_heavy", "unk"
    if REPEAT_CHAR_RUN.search(t):
        return False, "keyboard_smash", "unk"
    if ONLY_LATIN_CONSONANTS.match(t.replace(" ", "")):
        return False, "keyboard_smash", "unk"
    if _unique_char_ratio(t_soft) < float(prof["min_unique_ratio"]):
        return False, "low_unique_chars", "unk"

    # Social-noise controls
    if prof["drop_hashtag_spam"] and len(HASHTAG_HEAVY.findall(t)) >= 3:
        return False, "hashtag_spam", "unk"
    if prof["drop_at_spam"] and len(AT_HANDLE_HEAVY.findall(t)) >= 3:
        return False, "at_mention_spam", "unk"

    lower = t_soft.casefold()
    if any(p in lower for p in SPAM_PHRASES):
        return False, "spam_phrase", "unk"

    # ASCII emoticon ratio (smileys like :) :D ;P) — these dodge the unicode emoji check
    emoticons = len(_ASCII_EMOTICON_RE.findall(t))
    if emoticons >= 3 and len(t) < 80:
        return False, "emoticon_heavy", "unk"

    # Shallow-reaction filter (only fires on high/ultra strictness, to avoid over-pruning)
    profile_name = (strictness or DEFAULT_STRICTNESS or "normal").lower()
    if profile_name in {"high", "ultra"} and _is_shallow_quality is not None:
        try:
            if _is_shallow_quality(t):
                return False, "shallow_reaction", "unk"
        except Exception:
            pass

    # Language gate (run late so we skip wasted detection on obvious noise)
    lang = detect_lang(t_soft)
    if not _lang_allowed(lang):
        return False, "lang_not_whitelisted", lang

    return True, "ok", lang

def filter_comments(comments: List[str], desired_count: int = 20, strictness: Optional[str] = None) -> List[str]:
    """
    Keep-or-drop with dedupe, for callers that only need the list.
    Dedupe key uses normalized (casefolded, whitespace-collapsed) text.
    Backward-compatible: 'strictness' is optional.
    """
    kept: List[str] = []
    seen = set()

    for c in comments or []:
        ok, _, _ = validate_with_reason(c, strictness=strictness)
        if not ok:
            continue
        key = _norm_text_for_key(c)
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)
        if len(kept) >= desired_count:
            break

    return kept

def filter_and_metrics(
    stream: List[str],
    desired_count: int,
    strictness: Optional[str] = None
) -> Tuple[List[str], Dict[str, int], Dict[str, int]]:
    """
    Filter comments with reasons and produce:
      kept: list[str]
      dropped_by_reason: dict[str,int]
      lang_hist: dict[str,int]
    Backward-compatible: 'strictness' is optional.
    """
    dropped: Dict[str, int] = {}
    langs: Dict[str, int] = {}
    kept: List[str] = []
    seen = set()

    for c in stream or []:
        ok, reason, lang = validate_with_reason(c, strictness=strictness)
        langs[lang] = langs.get(lang, 0) + 1

        if not ok:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue

        key = _norm_text_for_key(c)
        if key in seen:
            dropped["duplicate"] = dropped.get("duplicate", 0) + 1
            continue

        seen.add(key)
        kept.append(c)
        if len(kept) >= desired_count:
            break

    return kept, dropped, langs

def filter_items_and_metrics(
    items: List[Dict],
    desired_count: int,
    strictness: Optional[str] = None,
    text_key: str = "text",
) -> Tuple[List[Dict], Dict[str, int], Dict[str, int]]:
    """
    Same as `filter_and_metrics` but operates on dicts (kept_items) instead of strings,
    so per-comment metadata (timestamps, author, score) survives the filter pass.
    Items without a usable `text_key` are dropped as `no_text`.
    """
    dropped: Dict[str, int] = {}
    langs: Dict[str, int] = {}
    kept: List[Dict] = []
    seen = set()

    for item in items or []:
        if not isinstance(item, dict):
            dropped["no_text"] = dropped.get("no_text", 0) + 1
            continue
        text = item.get(text_key)
        if not isinstance(text, str) or not text.strip():
            dropped["no_text"] = dropped.get("no_text", 0) + 1
            continue

        ok, reason, lang = validate_with_reason(text, strictness=strictness)
        langs[lang] = langs.get(lang, 0) + 1
        if not ok:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue

        key = _norm_text_for_key(text)
        if key in seen:
            dropped["duplicate"] = dropped.get("duplicate", 0) + 1
            continue

        seen.add(key)
        # Attach detected language so downstream stages don't have to re-run langdetect.
        item_out = dict(item)
        item_out["language_detected"] = lang
        kept.append(item_out)
        if len(kept) >= desired_count:
            break

    return kept, dropped, langs

__all__ = [
    "detect_lang",
    "is_clean_sentence",
    "validate_with_reason",
    "filter_comments",
    "filter_and_metrics",
    "filter_items_and_metrics",
    "WHITELIST_LANGUAGES",
    "MIN_LEN",
    "MAX_LEN",
    "ALPHA_MIN",
]
