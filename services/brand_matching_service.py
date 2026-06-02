"""
Brand matching engine — fuzzy/exact similarity scoring for source → master brand suggestions.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── Try to import rapidfuzz; fall back to difflib ────────────────────────────

try:
    from rapidfuzz.fuzz import token_sort_ratio as _fuzz_token_sort  # type: ignore
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

# ── Service-word pattern ──────────────────────────────────────────────────────

_SERVICE_WORDS = [
    "TRADING", "UKRAINE", "УКРАЇНА",
    "GROUP", "BRAND", "TRADE", "CORP", "GRUP",
    "LLC", "ЛТД", "ФОП", "ТОВ", "ПП",
    "TM", "ТМ",
]
# Build a single pattern that matches any of the above as whole words
_SERVICE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _SERVICE_WORDS) + r")\b"
)

# Characters to replace with a space (punctuation except already-handled quotes)
_PUNCT_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)

# Multiple-space collapser
_SPACES_PATTERN = re.compile(r"\s+")


# ── 1. normalize_brand_name ───────────────────────────────────────────────────

def normalize_brand_name(name: str) -> str:
    """
    Normalise a brand name for comparison:
      1. Replace Cyrillic ё/Ё with е/Е
      2. UPPER-case
      3. Remove quotes: \" ' « » “ ” ‘ ’
      4. Replace remaining punctuation with a space
      5. Remove service words (ТОВ, TM, GROUP, …)
      6. Collapse whitespace and strip
    """
    if not name:
        return ""

    # 1. ё → е (both cases, then upper-case will unify)
    s = name.replace("ё", "е").replace("Ё", "Е")

    # 2. Upper-case
    s = s.upper()

    # 3. Remove quotes
    s = s.translate(str.maketrans("", "", "\"'«»“”‘’"))

    # 4. Replace punctuation with space
    s = _PUNCT_PATTERN.sub(" ", s)

    # 5. Remove service words
    s = _SERVICE_PATTERN.sub(" ", s)

    # 6. Collapse spaces and strip
    s = _SPACES_PATTERN.sub(" ", s).strip()

    return s


# ── 2. compute_match_score ────────────────────────────────────────────────────

def compute_match_score(src_normalized: str, master_normalized: str) -> int:
    """
    Return an integer score 0–100 representing similarity between two
    already-normalised brand names.

    Rules (evaluated in priority order):
      - Exact match                      → 100
      - One string contains the other    → max(85, int(shorter/longer * 95))
      - rapidfuzz.fuzz.token_sort_ratio  (if available)
      - difflib.SequenceMatcher ratio    (fallback)
    """
    if not src_normalized or not master_normalized:
        return 0

    if src_normalized == master_normalized:
        return 100

    # Substring / containment check
    shorter, longer = sorted([src_normalized, master_normalized], key=len)
    if shorter in longer:
        ratio_score = int(len(shorter) / len(longer) * 95)
        return max(85, ratio_score)

    # Fuzzy similarity
    if _HAS_RAPIDFUZZ:
        score = _fuzz_token_sort(src_normalized, master_normalized)
        return int(score)
    else:
        import difflib
        ratio = difflib.SequenceMatcher(None, src_normalized, master_normalized).ratio()
        return int(ratio * 100)


# ── 3. get_recommendation ─────────────────────────────────────────────────────

def get_recommendation(score: int) -> str:
    """
    Convert a numeric match score into a human-readable recommendation tag.
      >= 95 → "AUTO_BIND"
      >= 80 → "RECOMMEND_BIND"
      >= 60 → "REVIEW"
      else  → "CREATE"
    """
    if score >= 95:
        return "AUTO_BIND"
    if score >= 80:
        return "RECOMMEND_BIND"
    if score >= 60:
        return "REVIEW"
    return "CREATE"


# ── 4. find_best_match ────────────────────────────────────────────────────────

def find_best_match(source_name: str, masters: list[dict]) -> dict:
    """
    Find the best-matching master brand for a given source brand name.

    Parameters
    ----------
    source_name : str
        Raw (un-normalised) source brand name.
    masters : list[dict]
        Each dict must have keys:
          id, brand_name, brand_group, normalized_name, mapped_sources_count

    Returns
    -------
    dict with keys:
        suggested_master_brand_id   : int | None
        suggested_master_brand_name : str | None
        suggested_brand_group       : str | None
        match_score                 : int
        recommendation              : str
        normalized_source_name      : str
    """
    src_norm = normalize_brand_name(source_name)

    _empty: dict = {
        "suggested_master_brand_id":   None,
        "suggested_master_brand_name": None,
        "suggested_brand_group":       None,
        "match_score":                 0,
        "recommendation":              "CREATE",
        "normalized_source_name":      src_norm,
    }

    if not src_norm or not masters:
        return _empty

    best_score = -1
    best_master: Optional[dict] = None

    for master in masters:
        m_norm = master.get("normalized_name") or normalize_brand_name(master.get("brand_name", ""))
        score = compute_match_score(src_norm, m_norm)
        if score > best_score:
            best_score = score
            best_master = master

    if best_master is None or best_score <= 0:
        return _empty

    return {
        "suggested_master_brand_id":   best_master["id"],
        "suggested_master_brand_name": best_master.get("brand_name"),
        "suggested_brand_group":       best_master.get("brand_group"),
        "match_score":                 best_score,
        "recommendation":              get_recommendation(best_score),
        "normalized_source_name":      src_norm,
    }


# ── 5. batch_find_suggestions ─────────────────────────────────────────────────

def batch_find_suggestions(source_rows: list[dict], masters: list[dict]) -> dict:
    """
    Compute best-match suggestions for a batch of source brands in a single pass.

    Parameters
    ----------
    source_rows : list[dict]
        Each dict must have: source_brand_id (str), source_brand_name (str)
    masters : list[dict]
        Same format as find_best_match — pre-normalised list of master brands.

    Returns
    -------
    dict mapping source_brand_id → find_best_match result dict
    """
    if not source_rows or not masters:
        return {}

    # Pre-normalise master names once
    master_norms: list[tuple[dict, str]] = [
        (m, m.get("normalized_name") or normalize_brand_name(m.get("brand_name", "")))
        for m in masters
    ]

    result: dict = {}
    for row in source_rows:
        sid  = row.get("source_brand_id", "")
        name = row.get("source_brand_name", "")
        src_norm = normalize_brand_name(name)

        if not src_norm:
            result[sid] = {
                "suggested_master_brand_id":   None,
                "suggested_master_brand_name": None,
                "suggested_brand_group":       None,
                "match_score":                 0,
                "recommendation":              "CREATE",
                "normalized_source_name":      src_norm,
            }
            continue

        best_score = -1
        best_master: Optional[dict] = None

        for master, m_norm in master_norms:
            score = compute_match_score(src_norm, m_norm)
            if score > best_score:
                best_score = score
                best_master = master

        if best_master is None or best_score <= 0:
            result[sid] = {
                "suggested_master_brand_id":   None,
                "suggested_master_brand_name": None,
                "suggested_brand_group":       None,
                "match_score":                 0,
                "recommendation":              "CREATE",
                "normalized_source_name":      src_norm,
            }
        else:
            result[sid] = {
                "suggested_master_brand_id":   best_master["id"],
                "suggested_master_brand_name": best_master.get("brand_name"),
                "suggested_brand_group":       best_master.get("brand_group"),
                "match_score":                 best_score,
                "recommendation":              get_recommendation(best_score),
                "normalized_source_name":      src_norm,
            }

    return result
