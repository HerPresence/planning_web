from fastapi import APIRouter, Form, HTTPException, Depends
from typing import Optional, List
from pydantic import BaseModel
from db import get_connection
from services.article_import_service import ensure_article_columns
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/articles")


def ensure_article_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_article (
            article_id TEXT PRIMARY KEY,
            article_name TEXT NOT NULL,
            article_type TEXT,
            level1 TEXT,
            level2 TEXT,
            pnl_id INTEGER,
            is_active BOOLEAN DEFAULT true
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


_SELECT = (
    "article_id, article_name, article_type, level1, level2, pnl_id, is_active, "
    "uid_expense_article, expense_element, expense_company, level1_olap, level2_olap, "
    "merged_into_article_id, merged_at, merge_reason"
)


def _ensure_merge_columns():
    conn = get_connection(); cur = conn.cursor()
    try:
        for col, typ in [
            ("merged_into_article_id", "TEXT"),
            ("merged_at",              "TIMESTAMP"),
            ("merged_by",              "INTEGER"),
            ("merge_reason",           "TEXT"),
        ]:
            cur.execute(
                f"ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS {col} {typ}"
            )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pnl_article_merge_audit (
                id                SERIAL PRIMARY KEY,
                merge_run_id      TEXT NOT NULL,
                target_article_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                old_mapping_count INTEGER DEFAULT 0,
                new_mapping_count INTEGER DEFAULT 0,
                fact_rows_updated INTEGER DEFAULT 0,
                plan_rows_updated INTEGER DEFAULT 0,
                merged_by         INTEGER,
                merged_at         TIMESTAMP DEFAULT NOW(),
                reason            TEXT,
                snapshot_before   JSONB
            )
        """)
        conn.commit()
    finally:
        cur.close(); conn.close()


def _row_to_dict(r):
    return {
        "article_id":             r[0],
        "article_name":           r[1],
        "article_type":           r[2],
        "level1":                 r[3],
        "level2":                 r[4],
        "pnl_id":                 r[5],
        "is_active":              r[6],
        "uid_expense_article":    r[7],
        "expense_element":        r[8],
        "expense_company":        r[9],
        "level1_olap":            r[10],
        "level2_olap":            r[11],
        "merged_into_article_id": r[12] if len(r) > 12 else None,
        "merged_at":              r[13].isoformat() if len(r) > 13 and r[13] else None,
        "merge_reason":           r[14] if len(r) > 14 else None,
    }


# ── GET with full filter support ──────────────────────────────────────────────

@router.get("")
def get_articles(
    search:              Optional[str]  = None,
    article_type:        Optional[str]  = None,   # "Дохід" | "Витрати"
    is_active:           Optional[str]  = None,   # "true" | "false"
    level1:              Optional[str]  = None,
    level2:              Optional[str]  = None,
    pnl_id:              Optional[int]  = None,
    uid_expense_article: Optional[str]  = None,
    expense_element:     Optional[str]  = None,
    expense_company:     Optional[str]  = None,
    level1_olap:         Optional[str]  = None,
    level2_olap:         Optional[str]  = None,
    only_with_uid:       bool           = False,
    only_without_uid:    bool           = False,
    only_without_element:bool           = False,
    only_dup_name:       bool           = False,
    only_dup_uid:        bool           = False,
    hide_merged:         bool           = True,
    page:                int            = 1,
    page_size:           int            = 200,
):
    ensure_article_columns()
    _ensure_merge_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        where  = ["1=1"]
        params = []

        if search:
            where.append(
                "(article_id ILIKE %s OR article_name ILIKE %s OR "
                " uid_expense_article ILIKE %s OR expense_element ILIKE %s OR "
                " expense_company ILIKE %s OR level1_olap ILIKE %s OR level2_olap ILIKE %s)"
            )
            p = f"%{search}%"
            params.extend([p, p, p, p, p, p, p])

        if article_type:
            where.append("article_type = %s"); params.append(article_type)
        if is_active is not None:
            where.append("is_active = %s")
            params.append(is_active.lower() == "true")
        if level1:
            where.append("level1 ILIKE %s"); params.append(f"%{level1}%")
        if level2:
            where.append("level2 ILIKE %s"); params.append(f"%{level2}%")
        if pnl_id:
            where.append("pnl_id = %s"); params.append(pnl_id)
        if uid_expense_article:
            where.append("uid_expense_article ILIKE %s"); params.append(f"%{uid_expense_article}%")
        if expense_element:
            where.append("expense_element ILIKE %s"); params.append(f"%{expense_element}%")
        if expense_company:
            where.append("expense_company = %s"); params.append(expense_company)
        if level1_olap:
            where.append("level1_olap ILIKE %s"); params.append(f"%{level1_olap}%")
        if level2_olap:
            where.append("level2_olap ILIKE %s"); params.append(f"%{level2_olap}%")
        if only_with_uid:
            where.append("uid_expense_article IS NOT NULL AND uid_expense_article <> ''")
        if only_without_uid:
            where.append("(uid_expense_article IS NULL OR uid_expense_article = '')")
        if only_without_element:
            where.append("(expense_element IS NULL OR expense_element = '')")
        if only_dup_name:
            where.append(
                "LOWER(article_name) IN ("
                "  SELECT LOWER(article_name) FROM dim_article"
                "  GROUP BY LOWER(article_name) HAVING COUNT(*) > 1"
                ")"
            )
        if only_dup_uid:
            where.append(
                "uid_expense_article IS NOT NULL AND uid_expense_article <> '' AND "
                "uid_expense_article IN ("
                "  SELECT uid_expense_article FROM dim_article"
                "  WHERE uid_expense_article IS NOT NULL AND uid_expense_article <> ''"
                "  GROUP BY uid_expense_article HAVING COUNT(*) > 1"
                ")"
            )
        if hide_merged:
            where.append("(merged_into_article_id IS NULL OR merged_into_article_id = '')")

        sql_where = " AND ".join(where)

        # KPI counts (global, unfiltered)
        cur.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE is_active = TRUE), "
            "COUNT(*) FILTER (WHERE is_active = FALSE), "
            "COUNT(*) FILTER (WHERE article_type = 'Дохід'), "
            "COUNT(*) FILTER (WHERE article_type = 'Витрати'), "
            "COUNT(*) FILTER (WHERE uid_expense_article IS NOT NULL AND uid_expense_article <> ''), "
            "COUNT(*) FILTER (WHERE uid_expense_article IS NULL OR uid_expense_article = ''), "
            "(SELECT COUNT(*) FROM dim_article WHERE LOWER(article_name) IN ("
            "  SELECT LOWER(article_name) FROM dim_article"
            "  GROUP BY LOWER(article_name) HAVING COUNT(*) > 1)), "
            "(SELECT COUNT(*) FROM dim_article"
            "  WHERE uid_expense_article IS NOT NULL AND uid_expense_article <> ''"
            "  AND uid_expense_article IN ("
            "    SELECT uid_expense_article FROM dim_article"
            "    WHERE uid_expense_article IS NOT NULL AND uid_expense_article <> ''"
            "    GROUP BY uid_expense_article HAVING COUNT(*) > 1)), "
            "COUNT(*) FILTER (WHERE merged_into_article_id IS NOT NULL AND merged_into_article_id <> '') "
            "FROM dim_article"
        )
        k = cur.fetchone()
        kpi_global = {
            "total": int(k[0]), "active": int(k[1]), "inactive": int(k[2]),
            "income": int(k[3]), "expense": int(k[4]),
            "with_uid": int(k[5]), "without_uid": int(k[6]),
            "dup_name": int(k[7]), "dup_uid": int(k[8]),
            "merged": int(k[9]),
        }

        # Total filtered count
        cur.execute(f"SELECT COUNT(*) FROM dim_article WHERE {sql_where}", params)
        total = int(cur.fetchone()[0])

        # Filter dropdown values (distinct, always from full table for UX)
        def _distinct(col):
            cur.execute(
                f"SELECT DISTINCT {col} FROM dim_article "
                f"WHERE {col} IS NOT NULL AND {col} <> '' ORDER BY {col}"
            )
            return [r[0] for r in cur.fetchall()]

        filter_values = {
            "level1":          _distinct("level1"),
            "level2":          _distinct("level2"),
            "expense_element": _distinct("expense_element"),
            "expense_company": _distinct("expense_company"),
            "level1_olap":     _distinct("level1_olap"),
            "level2_olap":     _distinct("level2_olap"),
        }

        # Data page
        offset = (max(page, 1) - 1) * page_size
        cur.execute(
            f"SELECT {_SELECT} FROM dim_article WHERE {sql_where} "
            f"ORDER BY level2, level1, article_name LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]

        return {
            "rows":          rows,
            "total":         total,
            "page":          page,
            "page_size":     page_size,
            "kpi":           kpi_global,
            "filter_values": filter_values,
        }
    finally:
        cur.close()
        conn.close()


# CREATE — unchanged
@router.post("")
def create_article(
    article_id:          str = Form(...),
    article_name:        str = Form(...),
    article_type:        str = Form(""),
    level1:              str = Form(""),
    level2:              str = Form(""),
    pnl_id:              int = Form(...),
    uid_expense_article: str = Form(""),
    expense_element:     str = Form(""),
    expense_company:     str = Form(""),
    level1_olap:         str = Form(""),
    level2_olap:         str = Form(""),
):
    if not pnl_id:
        raise HTTPException(
            status_code=400,
            detail="Оберіть структуру PnL (pnl_id не може бути порожнім)",
        )

    ensure_article_columns()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        f"SELECT {_SELECT} FROM dim_article WHERE article_id = %s",
        (article_id,),
    )
    existing = cur.fetchone()

    if existing:
        cur.close(); conn.close()
        return {"status": "exists", "article": _row_to_dict(existing)}

    cur.execute(
        """
        INSERT INTO dim_article
            (article_id, article_name, article_type, level1, level2, pnl_id, is_active,
             uid_expense_article, expense_element, expense_company, level1_olap, level2_olap)
        VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s)
        """,
        (
            article_id, article_name, article_type, level1, level2, pnl_id,
            uid_expense_article, expense_element, expense_company, level1_olap, level2_olap,
        ),
    )
    conn.commit()
    cur.close(); conn.close()

    return {
        "status": "ok",
        "article": {
            "article_id": article_id, "article_name": article_name,
            "article_type": article_type, "level1": level1, "level2": level2,
            "pnl_id": pnl_id, "is_active": True,
            "uid_expense_article": uid_expense_article,
            "expense_element": expense_element, "expense_company": expense_company,
            "level1_olap": level1_olap, "level2_olap": level2_olap,
        },
    }


# UPDATE — unchanged
@router.put("/{old_article_id}")
def update_article(
    old_article_id:      str,
    article_name:        str = Form(...),
    article_type:        str = Form(""),
    level1:              str = Form(""),
    level2:              str = Form(""),
    pnl_id:              int = Form(0),
    is_active:           str = Form("true"),
    uid_expense_article: str = Form(""),
    expense_element:     str = Form(""),
    expense_company:     str = Form(""),
    level1_olap:         str = Form(""),
    level2_olap:         str = Form(""),
):
    ensure_article_columns()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE dim_article
        SET
            article_name        = %s,
            article_type        = %s,
            level1              = %s,
            level2              = %s,
            pnl_id              = %s,
            is_active           = %s,
            uid_expense_article = %s,
            expense_element     = %s,
            expense_company     = %s,
            level1_olap         = %s,
            level2_olap         = %s
        WHERE article_id = %s
        """,
        (
            article_name, article_type, level1, level2, pnl_id,
            is_active.lower() == "true",
            uid_expense_article, expense_element, expense_company,
            level1_olap, level2_olap, old_article_id,
        ),
    )
    conn.commit()
    cur.close(); conn.close()
    return {"status": "ok"}


# ── Merge articles ────────────────────────────────────────────────────────────

class MergePreviewRequest(BaseModel):
    target_article_id:  str
    source_article_ids: List[str]


class MergeRequest(BaseModel):
    target_article_id:  str
    source_article_ids: List[str]
    reason:             str = ""


def _get_article(cur, article_id: str) -> dict:
    cur.execute(f"SELECT {_SELECT} FROM dim_article WHERE article_id = %s", (article_id,))
    r = cur.fetchone()
    return _row_to_dict(r) if r else None


def _count_mappings(cur, article_id: str) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM article_source_mapping WHERE master_article_id = %s",
        (article_id,),
    )
    return int(cur.fetchone()[0])


@router.post("/merge-preview")
def merge_preview(body: MergePreviewRequest, _u=Depends(get_current_user)):
    ensure_article_columns(); _ensure_merge_columns()
    conn = get_connection(); cur = conn.cursor()
    try:
        # Validate target
        target = _get_article(cur, body.target_article_id)
        if not target:
            raise HTTPException(400, f"Target article '{body.target_article_id}' not found")
        if not target["is_active"]:
            raise HTTPException(400, "Target article is inactive — cannot merge into it")
        if target.get("merged_into_article_id"):
            raise HTTPException(400, "Target article is already merged into another article")

        sources, conflicts, warnings = [], [], []

        for sid in body.source_article_ids:
            if sid == body.target_article_id:
                continue
            src = _get_article(cur, sid)
            if not src:
                conflicts.append(f"Source article '{sid}' not found")
                continue
            if src.get("merged_into_article_id"):
                conflicts.append(f"'{src['article_name']}' вже об'єднано з іншою статтею")
                continue

            # Type check — block
            if src["article_type"] and target["article_type"] and src["article_type"] != target["article_type"]:
                conflicts.append(
                    f"'{src['article_name']}': тип {src['article_type']} ≠ {target['article_type']} "
                    f"— не можна об'єднати Дохід з Витратами"
                )
                continue

            # Soft warnings
            if src["level1"] and target["level1"] and src["level1"] != target["level1"]:
                warnings.append(f"'{src['article_name']}': різний Level 1 ({src['level1']} → {target['level1']})")
            if src["level2"] and target["level2"] and src["level2"] != target["level2"]:
                warnings.append(f"'{src['article_name']}': різний Level 2 ({src['level2']} → {target['level2']})")
            if src["expense_company"] and target["expense_company"] and src["expense_company"] != target["expense_company"]:
                warnings.append(f"'{src['article_name']}': різна компанія ({src['expense_company']} → {target['expense_company']})")
            if src["pnl_id"] and target["pnl_id"] and src["pnl_id"] != target["pnl_id"]:
                warnings.append(f"'{src['article_name']}': різна PnL структура ({src['pnl_id']} → {target['pnl_id']})")

            mapping_count = _count_mappings(cur, sid)

            # fact_pnl direct references
            cur.execute("SELECT COUNT(*) FROM fact_pnl WHERE article_id = %s", (sid,))
            fact_count = int(cur.fetchone()[0])

            # plan_pnl direct references
            cur.execute("SELECT COUNT(*) FROM plan_pnl WHERE article_id = %s", (sid,))
            plan_count = int(cur.fetchone()[0])

            sources.append({
                **src,
                "mappings_count": mapping_count,
                "fact_refs":      fact_count,
                "plan_refs":      plan_count,
            })

        return {
            "target":   {**target, "mappings_count": _count_mappings(cur, body.target_article_id)},
            "sources":  sources,
            "total_mappings_to_move": sum(s["mappings_count"] for s in sources),
            "total_fact_refs":        sum(s["fact_refs"] for s in sources),
            "total_plan_refs":        sum(s["plan_refs"] for s in sources),
            "conflicts": conflicts,
            "warnings":  warnings,
            "can_merge": len(conflicts) == 0 and len(sources) > 0,
        }
    finally:
        cur.close(); conn.close()


import uuid as _uuid
import json as _json


@router.post("/merge")
def merge_articles(body: MergeRequest, _u=Depends(get_current_user)):
    ensure_article_columns(); _ensure_merge_columns()
    conn = get_connection(); cur = conn.cursor()
    try:
        # Run same validation as preview
        target = _get_article(cur, body.target_article_id)
        if not target:
            raise HTTPException(400, f"Target article '{body.target_article_id}' not found")
        if not target["is_active"]:
            raise HTTPException(400, "Target article is inactive")
        if target.get("merged_into_article_id"):
            raise HTTPException(400, "Target article is already merged")

        run_id = str(_uuid.uuid4())
        merged_count = moved_mappings = fact_updated = plan_updated = 0

        for sid in body.source_article_ids:
            if sid == body.target_article_id:
                continue
            src = _get_article(cur, sid)
            if not src:
                continue
            if src.get("merged_into_article_id"):
                continue
            if src["article_type"] and target["article_type"] and src["article_type"] != target["article_type"]:
                continue  # blocked — type mismatch

            old_mapping_count = _count_mappings(cur, sid)

            # Snapshot source article
            snapshot = _json.dumps(src, ensure_ascii=False, default=str)

            # 1. Move article_source_mapping rows
            # Use UPSERT to avoid duplicates: if target already has the same
            # (source_id, source_article_id), skip (DO NOTHING)
            cur.execute(
                """UPDATE article_source_mapping
                   SET master_article_id = %s
                   WHERE master_article_id = %s
                     AND (source_id, source_article_id) NOT IN (
                         SELECT source_id, source_article_id
                         FROM article_source_mapping
                         WHERE master_article_id = %s
                     )""",
                (body.target_article_id, sid, body.target_article_id),
            )
            moved = cur.rowcount
            moved_mappings += moved

            # 2. Update fact_pnl direct references
            cur.execute(
                "UPDATE fact_pnl SET article_id = %s, article_name = %s WHERE article_id = %s",
                (body.target_article_id, target["article_name"], sid),
            )
            fact_updated += cur.rowcount

            # 3. Update plan_pnl direct references
            cur.execute(
                "UPDATE plan_pnl SET article_id = %s, article_name = %s WHERE article_id = %s",
                (body.target_article_id, target["article_name"], sid),
            )
            plan_updated += cur.rowcount

            new_mapping_count = _count_mappings(cur, body.target_article_id)

            # 4. Mark source article as merged
            cur.execute(
                """UPDATE dim_article
                   SET is_active = FALSE,
                       merged_into_article_id = %s,
                       merged_at = NOW(),
                       merged_by = %s,
                       merge_reason = %s
                   WHERE article_id = %s""",
                (body.target_article_id, _u["id"], body.reason or None, sid),
            )
            merged_count += 1

            # 5. Audit record
            cur.execute(
                """INSERT INTO pnl_article_merge_audit
                       (merge_run_id, target_article_id, source_article_id,
                        old_mapping_count, new_mapping_count,
                        fact_rows_updated, plan_rows_updated,
                        merged_by, reason, snapshot_before)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (run_id, body.target_article_id, sid,
                 old_mapping_count, new_mapping_count,
                 cur.rowcount, plan_updated,
                 _u["id"], body.reason or None, snapshot),
            )

        conn.commit()
        return {
            "status":          "ok",
            "run_id":          run_id,
            "merged_count":    merged_count,
            "moved_mappings":  moved_mappings,
            "fact_updated":    fact_updated,
            "plan_updated":    plan_updated,
        }
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(500, str(exc))
    finally:
        cur.close(); conn.close()
