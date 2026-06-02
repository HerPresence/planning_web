"""
Article recommendation engine — scores source articles against master articles.

Scoring:
  UUID exact match          → 100 (AUTO_BIND)
  name exact (normalized)   → +40
  source_level1 = master L1 → +20
  source_level2 = master L2 → +15
  expense_flag matches      → +10
  article_type matches      → +10
  pnl_structure matches     → +5

Thresholds:
  95+   AUTO_BIND
  80-94 RECOMMEND
  60-79 REVIEW
  <60   CREATE_MASTER
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

try:
    from rapidfuzz.fuzz import token_sort_ratio as _fuzz  # type: ignore
    _HAS_FUZZ = True
except ImportError:
    _HAS_FUZZ = False


# ── Text normalisation ────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.upper()
    s = _PUNCT.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    return s


def _name_similarity(a: str, b: str) -> float:
    """Return 0–100 similarity score between two article names."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if _HAS_FUZZ:
        return float(_fuzz(na, nb))
    # Fallback: simple character overlap ratio
    set_a, set_b = set(na.split()), set(nb.split())
    if not set_a or not set_b:
        return 0.0
    overlap = len(set_a & set_b) / max(len(set_a | set_b), 1)
    return round(overlap * 100, 1)


# ── Main scoring function ─────────────────────────────────────────────────────

def recommend_master(source_row: dict, masters: list[dict]) -> dict:
    """
    Score a source article row against a list of master articles.

    source_row keys: source_article_id, source_article_name, source_level1,
                     source_level2, source_article_type, uid_expense_article,
                     default_pnl_id
    master keys:     article_id, article_name, level1, level2, article_type,
                     uid_expense_article, pnl_id

    Returns:
      {suggested_master_id, match_score, recommendation, reason,
       matched_fields, conflict_fields}
    """
    if not masters:
        return _no_match("Немає master-статей")

    best_id    = None
    best_score = 0.0
    best_meta: dict = {}

    src_uid  = (source_row.get("uid_expense_article") or "").strip()
    src_name = source_row.get("source_article_name") or ""
    src_l1   = _norm(source_row.get("source_level1") or source_row.get("level1_olap") or "")
    src_l2   = _norm(source_row.get("source_level2") or source_row.get("level2_olap") or "")
    src_type = _norm(source_row.get("source_article_type") or "")
    src_exp  = source_row.get("uid_expense_article")
    src_pnl  = source_row.get("default_pnl_id")

    for m in masters:
        score = 0.0
        matched: list[str] = []
        conflict: list[str] = []

        mst_uid  = (m.get("uid_expense_article") or "").strip()
        mst_name = m.get("article_name") or ""
        mst_l1   = _norm(m.get("level1") or "")
        mst_l2   = _norm(m.get("level2") or "")
        mst_type = _norm(m.get("article_type") or "")
        mst_exp  = m.get("uid_expense_article")
        mst_pnl  = m.get("pnl_id")

        # UUID exact match → instant win
        if src_uid and mst_uid and src_uid == mst_uid:
            score = 100.0
            matched.append("uid")
        else:
            # Name similarity (0–100 → weighted to 0–40)
            name_sim = _name_similarity(src_name, mst_name)
            score += name_sim * 0.4
            if name_sim >= 90:
                matched.append("article_name")
            elif name_sim < 50 and mst_name:
                conflict.append("article_name")

            # Level 1
            if src_l1 and mst_l1:
                if src_l1 == mst_l1:
                    score += 20; matched.append("level1")
                else:
                    conflict.append("level1")

            # Level 2
            if src_l2 and mst_l2:
                if src_l2 == mst_l2:
                    score += 15; matched.append("level2")
                else:
                    conflict.append("level2")

            # Expense flag / uid_expense_article
            if src_exp is not None and mst_exp is not None:
                if str(src_exp) == str(mst_exp):
                    score += 10; matched.append("expense_flag")
                else:
                    conflict.append("expense_flag")

            # Article type
            if src_type and mst_type:
                if src_type == mst_type:
                    score += 10; matched.append("article_type")
                else:
                    conflict.append("article_type")

            # PnL structure
            if src_pnl and mst_pnl and str(src_pnl) == str(mst_pnl):
                score += 5; matched.append("pnl_structure")

        score = min(score, 100.0)

        if score > best_score:
            best_score = score
            best_id    = m["article_id"]
            best_meta  = {
                "matched_fields":  matched,
                "conflict_fields": conflict,
                "master_name":     mst_name,
            }

    if best_score < 30 or best_id is None:
        return _no_match("Схожих master-статей не знайдено")

    rec, reason = _classify(best_score, best_meta)
    return {
        "suggested_master_id": best_id,
        "match_score":         round(best_score, 1),
        "recommendation":      rec,
        "reason":              reason,
        "matched_fields":      best_meta.get("matched_fields", []),
        "conflict_fields":     best_meta.get("conflict_fields", []),
    }


def _classify(score: float, meta: dict) -> tuple[str, str]:
    conflicts = meta.get("conflict_fields", [])
    matched   = meta.get("matched_fields", [])
    name      = meta.get("master_name", "")

    if score >= 95:
        return "AUTO_BIND", f"Автоматична прив'язка: {', '.join(matched) or 'UUID'}"
    if score >= 80:
        if conflicts:
            return "RECOMMEND", f"Висока схожість, конфлікт: {', '.join(conflicts)}"
        return "RECOMMEND", f"Висока схожість за {', '.join(matched)}"
    if score >= 60:
        return "REVIEW", f"Середня схожість ({score:.0f}%), перевірте конфлікти: {', '.join(conflicts) or 'немає'}"
    return "CREATE_MASTER", f"Низька схожість ({score:.0f}%) — можливо потрібна нова master-стаття"


def _no_match(reason: str) -> dict:
    return {
        "suggested_master_id": None,
        "match_score":         0.0,
        "recommendation":      "CREATE_MASTER",
        "reason":              reason,
        "matched_fields":      [],
        "conflict_fields":     [],
    }


# ── Batch scoring for a list of source rows ───────────────────────────────────

def batch_recommend(source_rows: list[dict], masters: list[dict]) -> list[dict]:
    """Score each source_row and return list of recommendation dicts (same order)."""
    return [recommend_master(row, masters) for row in source_rows]
