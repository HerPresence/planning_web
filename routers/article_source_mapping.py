from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List

from db import get_connection
from services.article_import_service import ensure_source_staging_tables, ensure_article_columns

router = APIRouter(prefix="/api/article-source-mapping")


class BindRequest(BaseModel):
    source_id:         int
    source_article_id: str
    master_article_id: Optional[str] = None
    mapping_status:    str = "mapped"


class AutoBindRequest(BaseModel):
    source_id: Optional[int] = None


class UUIDBindingItem(BaseModel):
    source_id:         int
    source_article_id: str
    master_article_id: str


class ConfirmUUIDBindings(BaseModel):
    bindings: List[UUIDBindingItem]


# ── staged articles ───────────────────────────────────────────────────────────

@router.get("/staged")
def get_staged_articles(
    source_id:      Optional[int] = None,
    company:        Optional[str] = None,
    mapping_status: Optional[str] = None,
    search:         Optional[str] = None,
    page:           int = 1,
    page_size:      int = 50,
):
    ensure_source_staging_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        # Base conditions (no status filter) — used for KPI counts
        base_where  = ["das.is_active = TRUE"]
        base_params = []

        if source_id:
            base_where.append("das.source_id = %s")
            base_params.append(source_id)
        if company:
            base_where.append("das.expense_company ILIKE %s")
            base_params.append(f"%{company}%")
        if search:
            base_where.append(
                "(das.source_article_id ILIKE %s OR das.source_article_name ILIKE %s)"
            )
            base_params.extend([f"%{search}%", f"%{search}%"])

        # Extended conditions (with status filter) — used for data query
        full_where  = list(base_where)
        full_params = list(base_params)

        if mapping_status:
            if mapping_status == "pending":
                full_where.append("(asm.mapping_status IS NULL OR asm.mapping_status = 'pending')")
            else:
                full_where.append("asm.mapping_status = %s")
                full_params.append(mapping_status)

        base_sql = " AND ".join(base_where)
        full_sql = " AND ".join(full_where)

        join_sql = """
            FROM dim_article_source das
            LEFT JOIN article_source_mapping asm
                   ON asm.source_id = das.source_id
                  AND asm.source_article_id = das.source_article_id
        """

        # KPI counts (independent of status filter)
        cur.execute(
            f"""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE asm.mapping_status IS NULL OR asm.mapping_status = 'pending'),
                COUNT(*) FILTER (WHERE asm.mapping_status IN ('mapped', 'auto')),
                COUNT(*) FILTER (WHERE asm.mapping_status = 'rejected')
            {join_sql}
            WHERE {base_sql}
            """,
            base_params,
        )
        kpi_row = cur.fetchone()
        kpi = {
            "total":    int(kpi_row[0]),
            "pending":  int(kpi_row[1]),
            "mapped":   int(kpi_row[2]),
            "rejected": int(kpi_row[3]),
        }

        # Total for current filter (including status filter)
        cur.execute(
            f"SELECT COUNT(*) {join_sql} WHERE {full_sql}",
            full_params,
        )
        total = cur.fetchone()[0]

        limit  = page_size
        offset = (max(page, 1) - 1) * page_size

        cur.execute(
            f"""
            SELECT
                das.id,
                das.source_id,
                das.source_name,
                das.source_article_id,
                das.source_article_name,
                das.source_article_type,
                das.source_level1,
                das.source_level2,
                das.uid_expense_article,
                das.expense_element,
                das.expense_company,
                das.level1_olap,
                das.level2_olap,
                das.loaded_at,
                asm.id             AS mapping_id,
                asm.master_article_id,
                asm.mapping_status,
                asm.confidence,
                da.article_name    AS master_article_name
            {join_sql}
            LEFT JOIN dim_article da ON da.article_id = asm.master_article_id
            WHERE {full_sql}
            ORDER BY das.expense_company, das.source_article_id
            LIMIT %s OFFSET %s
            """,
            full_params + [limit, offset],
        )
        rows = cur.fetchall()

        result = [
            {
                "id":                  r[0],
                "source_id":           r[1],
                "source_name":         r[2],
                "source_article_id":   r[3],
                "source_article_name": r[4],
                "source_article_type": r[5],
                "source_level1":       r[6],
                "source_level2":       r[7],
                "uid_expense_article": r[8],
                "expense_element":     r[9],
                "expense_company":     r[10],
                "level1_olap":         r[11],
                "level2_olap":         r[12],
                "loaded_at":           r[13].isoformat() if r[13] else None,
                "mapping_id":          r[14],
                "master_article_id":   r[15],
                "mapping_status":      r[16] or "pending",
                "confidence":          float(r[17]) if r[17] else 0,
                "master_article_name": r[18],
            }
            for r in rows
        ]

        return {
            "status":    "ok",
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "kpi":       kpi,
            "rows":      result,
        }
    finally:
        cur.close()
        conn.close()


# ── master articles for dropdown ──────────────────────────────────────────────

@router.get("/masters")
def get_master_articles():
    ensure_article_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT article_id, article_name, level1, level2, uid_expense_article "
            "FROM dim_article WHERE is_active = TRUE ORDER BY article_id"
        )
        rows = cur.fetchall()
        return [
            {
                "article_id":          r[0],
                "article_name":        r[1],
                "level1":              r[2],
                "level2":              r[3],
                "uid_expense_article": r[4],
            }
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


# ── source companies list ─────────────────────────────────────────────────────

@router.get("/companies")
def get_staged_companies(source_id: Optional[int] = None):
    ensure_source_staging_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        if source_id:
            cur.execute(
                "SELECT DISTINCT expense_company FROM dim_article_source "
                "WHERE is_active=TRUE AND source_id=%s AND expense_company<>'' "
                "ORDER BY expense_company",
                (source_id,),
            )
        else:
            cur.execute(
                "SELECT DISTINCT expense_company FROM dim_article_source "
                "WHERE is_active=TRUE AND expense_company<>'' ORDER BY expense_company"
            )
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ── bind / update mapping ─────────────────────────────────────────────────────

@router.post("/bind")
def bind_article(req: BindRequest):
    ensure_source_staging_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO article_source_mapping
                (source_id, source_article_id, master_article_id, mapping_status, confidence, updated_at)
            VALUES (%s, %s, %s, %s, 100, NOW())
            ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                master_article_id = EXCLUDED.master_article_id,
                mapping_status    = EXCLUDED.mapping_status,
                confidence        = 100,
                updated_at        = NOW()
            """,
            (req.source_id, req.source_article_id, req.master_article_id, req.mapping_status),
        )
        conn.commit()
        return {"status": "ok"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── auto-bind by matching article IDs (legacy, kept for backward compat) ──────

@router.post("/auto-bind")
def auto_bind(req: AutoBindRequest):
    """Match source_article_id to dim_article.article_id where exact match exists."""
    ensure_source_staging_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        where = "das.is_active = TRUE"
        params = []
        if req.source_id:
            where += " AND das.source_id = %s"
            params.append(req.source_id)

        cur.execute(
            f"""
            SELECT das.source_id, das.source_article_id, da.article_id
            FROM dim_article_source das
            JOIN dim_article da ON da.article_id = das.source_article_id
            LEFT JOIN article_source_mapping asm
                   ON asm.source_id = das.source_id
                  AND asm.source_article_id = das.source_article_id
            WHERE {where}
              AND (asm.mapping_status IS NULL OR asm.mapping_status = 'pending')
            """,
            params,
        )
        matches = cur.fetchall()
        bound = 0
        for source_id, source_article_id, master_article_id in matches:
            cur.execute(
                """
                INSERT INTO article_source_mapping
                    (source_id, source_article_id, master_article_id, mapping_status, confidence, updated_at)
                VALUES (%s, %s, %s, 'auto', 95, NOW())
                ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                    master_article_id = EXCLUDED.master_article_id,
                    mapping_status    = 'auto',
                    confidence        = 95,
                    updated_at        = NOW()
                """,
                (source_id, source_article_id, master_article_id),
            )
            bound += 1
        conn.commit()
        return {"status": "ok", "bound": bound, "total_checked": len(matches)}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── UUID auto-bind preview (NO save) ─────────────────────────────────────────

@router.post("/auto-bind-uuid-preview")
def auto_bind_uuid_preview(req: AutoBindRequest):
    """Find UUID matches WITHOUT saving. Returns proposed matches for confirmation."""
    ensure_source_staging_tables()
    ensure_article_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        where_parts = [
            "das.is_active = TRUE",
            "das.uid_expense_article <> ''",
            "das.uid_expense_article IS NOT NULL",
        ]
        params = []
        if req.source_id:
            where_parts.append("das.source_id = %s")
            params.append(req.source_id)

        where_sql = " AND ".join(where_parts)

        cur.execute(
            f"""
            SELECT
                das.source_id,
                das.source_article_id,
                das.source_article_name,
                das.expense_company,
                das.uid_expense_article,
                da.article_id   AS master_article_id,
                da.article_name AS master_article_name
            FROM dim_article_source das
            JOIN dim_article da
                ON da.uid_expense_article = das.uid_expense_article
               AND da.uid_expense_article <> ''
               AND da.uid_expense_article IS NOT NULL
               AND da.is_active = TRUE
            LEFT JOIN article_source_mapping asm
                   ON asm.source_id = das.source_id
                  AND asm.source_article_id = das.source_article_id
            WHERE {where_sql}
              AND (asm.mapping_status IS NULL OR asm.mapping_status = 'pending')
            ORDER BY das.expense_company, das.source_article_id
            """,
            params,
        )
        rows = cur.fetchall()
        matches = [
            {
                "source_id":           r[0],
                "source_article_id":   r[1],
                "source_article_name": r[2],
                "expense_company":     r[3],
                "uuid":                r[4],
                "master_article_id":   r[5],
                "master_article_name": r[6],
            }
            for r in rows
        ]
        return {"status": "ok", "count": len(matches), "matches": matches}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── confirm UUID bindings (batch save after user approval) ────────────────────

@router.post("/confirm-uuid-bindings")
def confirm_uuid_bindings(req: ConfirmUUIDBindings):
    """Batch-bind UUID matches after user confirmation."""
    ensure_source_staging_tables()
    conn = get_connection()
    cur  = conn.cursor()
    bound = 0
    try:
        for b in req.bindings:
            cur.execute(
                """
                INSERT INTO article_source_mapping
                    (source_id, source_article_id, master_article_id, mapping_status, confidence, updated_at)
                VALUES (%s, %s, %s, 'mapped', 100, NOW())
                ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                    master_article_id = EXCLUDED.master_article_id,
                    mapping_status    = 'mapped',
                    confidence        = 100,
                    updated_at        = NOW()
                """,
                (b.source_id, b.source_article_id, b.master_article_id),
            )
            bound += 1
        conn.commit()
        return {"status": "ok", "bound": bound}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── delete mapping ────────────────────────────────────────────────────────────

@router.delete("/bind/{source_id}/{source_article_id}")
def delete_bind(source_id: int, source_article_id: str):
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE article_source_mapping "
            "SET mapping_status='pending', master_article_id=NULL, updated_at=NOW() "
            "WHERE source_id=%s AND source_article_id=%s",
            (source_id, source_article_id),
        )
        conn.commit()
        return {"status": "ok"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()
