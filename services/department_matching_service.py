"""
Department matching engine — similarity scoring for source → master department suggestions.
"""
from __future__ import annotations

import re
from typing import Optional

try:
    from rapidfuzz.fuzz import token_sort_ratio as _fuzz_token_sort  # type: ignore
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

_WORD_MAP = {
    "ВІД.": "ВІДДІЛ",
    "ОТДЕЛ": "ВІДДІЛ",
    "ФИЛИАЛ": "ФІЛІЯ",
    "АДМІН": "АДМІНІСТРАЦІЯ",
}
_PUNCT_RE  = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


def normalize_department_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip().upper()
    s = s.translate(str.maketrans("", "", "\"'«»“”‘’"))
    s = _PUNCT_RE.sub(" ", s)
    s = " ".join(_WORD_MAP.get(w, w) for w in s.split())
    return _SPACES_RE.sub(" ", s).strip()


def _fuzzy(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if a == b:
        return 100
    sh, lo = sorted([a, b], key=len)
    if sh in lo:
        return max(85, int(len(sh) / len(lo) * 95))
    if _HAS_RAPIDFUZZ:
        return int(_fuzz_token_sort(a, b))
    import difflib
    return int(difflib.SequenceMatcher(None, a, b).ratio() * 100)


def _score_one(source: dict, master: dict) -> tuple[int, list, list]:
    """Return (score, matched_fields, mismatched_fields)."""
    src_id  = (source.get("source_department_id") or "").strip()
    src_nm  = normalize_department_name(source.get("source_department_name") or "")
    src_org = (source.get("organization_name") or "").strip().upper()
    src_br  = (source.get("branch_name") or "").strip().upper()
    src_re  = (source.get("region_name") or "").strip().upper()
    src_ho  = (source.get("holding_name") or "").strip().upper()
    src_pid = (source.get("parent_department_id") or "").strip()
    src_pnm = normalize_department_name(source.get("parent_department_name") or "")

    mst_id  = (master.get("department_id") or "").strip()
    mst_nm  = normalize_department_name(master.get("department_name") or "")
    mst_org = (master.get("organization_name") or "").strip().upper()
    mst_br  = (master.get("branch_name") or "").strip().upper()
    mst_re  = (master.get("region_name") or "").strip().upper()
    mst_ho  = (master.get("holding_name") or "").strip().upper()
    mst_pid = (master.get("parent_department_id") or "").strip()
    mst_pnm = normalize_department_name(master.get("parent_department_name") or "")

    score = 0
    matched:    list[str] = []
    mismatched: list[str] = []

    # 1. Exact dept_id match → short-circuit
    if src_id and mst_id and src_id == mst_id:
        return 100, ["dept_id"], []

    # 2–3. Name (exact normalized = 35, fuzzy scaled to 0–30)
    if src_nm and mst_nm:
        if src_nm == mst_nm:
            score += 35
            matched.append("dept_name")
        else:
            f = _fuzzy(src_nm, mst_nm)
            score += int(f / 100 * 30)
            if f >= 80:
                matched.append("dept_name")
            elif f < 40:
                mismatched.append("dept_name")

    # 4. Org
    if src_org and mst_org:
        if src_org == mst_org:
            score += 20; matched.append("org")
        else:
            score -= 25; mismatched.append("org")
    elif src_org and not mst_org:
        mismatched.append("org")

    # 5. Branch
    if src_br and mst_br:
        if src_br == mst_br:
            score += 15; matched.append("branch")
        else:
            score -= 15; mismatched.append("branch")

    # 6. Region
    if src_re and mst_re:
        if src_re == mst_re:
            score += 10; matched.append("region")
        else:
            mismatched.append("region")

    # 7. Holding
    if src_ho and mst_ho:
        if src_ho == mst_ho:
            score += 10; matched.append("holding")
        else:
            mismatched.append("holding")

    # 8. Parent ID
    if src_pid and mst_pid:
        if src_pid == mst_pid:
            score += 20; matched.append("parent_id")
        else:
            score -= 25; mismatched.append("parent_id")
    elif src_pid and not mst_pid:
        mismatched.append("parent_id")

    # 9. Parent name
    if src_pnm and mst_pnm:
        if _fuzzy(src_pnm, mst_pnm) >= 80:
            score += 10; matched.append("parent_name")
        else:
            mismatched.append("parent_name")

    return max(0, score), matched, mismatched


def _derive_recommendation(
    score: int, parent_missing: bool, matched: list, mismatched: list
) -> tuple[str, str, bool]:
    """Returns (recommendation, reason, risky_duplicate)."""
    if score == 100 and "dept_id" in matched:
        return "AUTO_BIND", "Точний збіг за department_id", False
    if parent_missing:
        return "CREATE_PARENT_FIRST", "Спочатку потрібно створити parent-підрозділ", False

    risky = ("dept_name" in matched and
             any(f in mismatched for f in ("org", "branch", "parent_id")))
    if risky:
        return "REVIEW", "Схожа назва, але інший контекст", True
    if score >= 90:
        return "RECOMMEND_BIND", f"Високий збіг ({score}%)", False
    if score >= 60:
        return "REVIEW", f"Середній збіг ({score}%) — перевірте", False
    return "CREATE", "Немає достатнього збігу — рекомендовано створити", False


def find_best_department_match(source: dict, masters: list[dict]) -> dict:
    """Find best-matching master for a source row. Returns enrichment dict."""
    parent_missing = bool(source.get("parent_missing"))

    _empty: dict = {
        "suggested_master_department_id":   None,
        "suggested_master_department_name": None,
        "suggested_master_parent_id":       None,
        "suggested_master_parent_name":     None,
        "suggested_master_org":             None,
        "suggested_master_branch":          None,
        "suggested_master_region":          None,
        "match_score":                      0,
        "matched_fields":                   [],
        "mismatched_fields":                [],
        "recommendation":                   "CREATE_PARENT_FIRST" if parent_missing else "CREATE",
        "recommendation_reason":            "Спочатку потрібно створити parent-підрозділ" if parent_missing
                                            else "Немає збігу",
        "confidence_level":                 "LOW",
        "risky_duplicate":                  False,
    }

    if not masters:
        return _empty

    best_score       = -1
    best_master: Optional[dict] = None
    best_matched:    list = []
    best_mismatched: list = []

    for m in masters:
        sc, matched, mismatched = _score_one(source, m)
        if sc > best_score:
            best_score      = sc
            best_master     = m
            best_matched    = matched
            best_mismatched = mismatched

    if best_master is None or best_score <= 0:
        return _empty

    rec, reason, risky = _derive_recommendation(
        best_score, parent_missing, best_matched, best_mismatched
    )
    confidence = "HIGH" if best_score >= 90 else "MEDIUM" if best_score >= 60 else "LOW"

    return {
        "suggested_master_department_id":   best_master["department_id"],
        "suggested_master_department_name": best_master.get("department_name"),
        "suggested_master_parent_id":       best_master.get("parent_department_id"),
        "suggested_master_parent_name":     best_master.get("parent_department_name"),
        "suggested_master_org":             best_master.get("organization_name"),
        "suggested_master_branch":          best_master.get("branch_name"),
        "suggested_master_region":          best_master.get("region_name"),
        "match_score":                      best_score,
        "matched_fields":                   best_matched,
        "mismatched_fields":                best_mismatched,
        "recommendation":                   rec,
        "recommendation_reason":            reason,
        "confidence_level":                 confidence,
        "risky_duplicate":                  risky,
    }


def batch_find_matches(source_rows: list[dict], masters: list[dict]) -> dict:
    """Batch compute matches. Returns dict keyed by (source_id, source_department_id)."""
    result: dict = {}
    for src in source_rows:
        key = (src.get("source_id"), src.get("source_department_id", ""))
        result[key] = find_best_department_match(src, masters)
    return result


def find_top_candidates(source: dict, masters: list[dict], top_n: int = 10) -> list[dict]:
    """Return top-n scored master department candidates."""
    scored = []
    parent_missing = bool(source.get("parent_missing"))
    for m in masters:
        sc, matched, mismatched = _score_one(source, m)
        if sc <= 0:
            continue
        rec, reason, risky = _derive_recommendation(sc, parent_missing, matched, mismatched)
        scored.append({
            "department_id":          m["department_id"],
            "department_name":        m.get("department_name"),
            "parent_department_id":   m.get("parent_department_id"),
            "parent_department_name": m.get("parent_department_name"),
            "organization_name":      m.get("organization_name"),
            "branch_name":            m.get("branch_name"),
            "region_name":            m.get("region_name"),
            "holding_name":           m.get("holding_name"),
            "score":                  sc,
            "matched_fields":         matched,
            "mismatched_fields":      mismatched,
            "recommendation":         rec,
            "recommendation_reason":  reason,
            "risky_duplicate":        risky,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]
