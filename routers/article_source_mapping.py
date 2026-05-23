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


class BulkFillFilters(BaseModel):
    source_id:        Optional[int] = None
    company:          Optional[str] = None
    status:           Optional[str] = None
    source_level1:    Optional[str] = None
    source_level2:    Optional[str] = None
    master_level1:    Optional[str] = None
    master_level2:    Optional[str] = None
    pnl_structure_id: Optional[int] = None
    search:           Optional[str] = None


class BulkFillRequest(BaseModel):
    filters: BulkFillFilters
    field:   str
    value:   str
    confirm: bool = False


# ── bulk-fill helpers ─────────────────────────────────────────────────────────

_ALLOWED_FIELDS = {
    "master_l1":    "level1",
    "master_l2":    "level2",
    "pnl_id":       "pnl_id",
    "article_type": "article_type",
    "article_name": "article_name",
}

_FIELD_LABELS = {
    "master_l1":    "Master L1",
    "master_l2":    "Master L2",
    "pnl_id":       "PnL структура",
    "article_type": "Тип",
    "article_name": "Назва master-статті",
}

# dim_article_source columns that store pending defaults for unmapped rows
_STAGING_DEFAULT_FIELDS = {
    "master_l1":    "default_master_level1",
    "master_l2":    "default_master_level2",
    "pnl_id":       "default_pnl_id",
    "article_type": "default_article_type",
    "article_name": "default_master_article_name",
}

_staging_defaults_initialized = False


def _ensure_staging_default_columns():
    """Add default_* columns to dim_article_source once per process."""
    global _staging_defaults_initialized
    if _staging_defaults_initialized:
        return
    conn = get_connection()
    cur  = conn.cursor()
    try:
        for col, typ in [
            ("default_article_type",        "TEXT"),
            ("default_pnl_id",              "INTEGER"),
            ("default_master_level1",       "TEXT"),
            ("default_master_level2",       "TEXT"),
            ("default_master_article_name", "TEXT"),
        ]:
            cur.execute(
                f"ALTER TABLE dim_article_source ADD COLUMN IF NOT EXISTS {col} {typ}"
            )
        conn.commit()
        _staging_defaults_initialized = True
    finally:
        cur.close()
        conn.close()


def _has_any_filter(f: BulkFillFilters) -> bool:
    return bool(
        f.source_id or f.company or (f.status and f.status != "all")
        or f.source_level1 or f.source_level2
        or f.master_level1 or f.master_level2
        or f.pnl_structure_id or f.search
    )


def _bulk_join() -> str:
    return """
        FROM dim_article_source das
        LEFT JOIN article_source_mapping asm
               ON asm.source_id = das.source_id
              AND asm.source_article_id = das.source_article_id
        LEFT JOIN dim_article da ON da.article_id = asm.master_article_id
        LEFT JOIN pnl_structure ps  ON ps.id  = da.pnl_id
        LEFT JOIN pnl_structure ps2 ON ps2.id = das.default_pnl_id
    """


def _build_bulk_base_where(f: BulkFillFilters):
    """Base WHERE for bulk-fill — no mapped/unmapped restriction, respects all user filters."""
    SRC_L1 = "COALESCE(NULLIF(das.level1_olap, ''), das.source_level1)"
    SRC_L2 = "COALESCE(NULLIF(das.level2_olap, ''), das.source_level2)"
    where  = ["das.is_active = TRUE"]
    params = []
    if f.source_id:
        where.append("das.source_id = %s");          params.append(f.source_id)
    if f.company:
        where.append("das.expense_company ILIKE %s"); params.append(f"%{f.company}%")
    if f.search:
        where.append("(das.source_article_id ILIKE %s OR das.source_article_name ILIKE %s)")
        params.extend([f"%{f.search}%", f"%{f.search}%"])
    if f.source_level1:
        where.append(f"{SRC_L1} = %s");              params.append(f.source_level1)
    if f.source_level2:
        where.append(f"{SRC_L2} = %s");              params.append(f.source_level2)
    if f.master_level1:
        where.append("da.level1 = %s");              params.append(f.master_level1)
    if f.master_level2:
        where.append("da.level2 = %s");              params.append(f.master_level2)
    if f.pnl_structure_id:
        where.append("COALESCE(ps.id, ps2.id) = %s"); params.append(f.pnl_structure_id)
    if f.status and f.status != "all":
        if f.status == "pending":
            where.append("(asm.mapping_status IS NULL OR asm.mapping_status = 'pending')")
        else:
            where.append("asm.mapping_status = %s"); params.append(f.status)
    return " AND ".join(where), params


# ── staged articles ───────────────────────────────────────────────────────────

@router.get("/staged")
def get_staged_articles(
    source_id:        Optional[int] = None,
    company:          Optional[str] = None,
    mapping_status:   Optional[str] = None,
    search:           Optional[str] = None,
    level1:           Optional[str] = None,
    level2:           Optional[str] = None,
    master_level1:    Optional[str] = None,
    master_level2:    Optional[str] = None,
    pnl_structure_id: Optional[int] = None,
    page:             int = 1,
    page_size:        int = 50,
):
    ensure_source_staging_tables()
    _ensure_staging_default_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        # SQL expression for displayed source L1/L2 (mirrors frontend column render)
        SRC_L1 = "COALESCE(NULLIF(das.level1_olap, ''), das.source_level1)"
        SRC_L2 = "COALESCE(NULLIF(das.level2_olap, ''), das.source_level2)"

        # dim_article + pnl_structure included in all queries for master-level filter/value support
        # ps  = PNL from bound master article (mapped rows)
        # ps2 = PNL from staging default_pnl_id (unmapped rows filled via Bulk Fill)
        join_sql = """
            FROM dim_article_source das
            LEFT JOIN article_source_mapping asm
                   ON asm.source_id = das.source_id
                  AND asm.source_article_id = das.source_article_id
            LEFT JOIN dim_article da ON da.article_id = asm.master_article_id
            LEFT JOIN pnl_structure ps  ON ps.id  = da.pnl_id
            LEFT JOIN pnl_structure ps2 ON ps2.id = das.default_pnl_id
        """

        # Base conditions (no status filter) — used for KPI counts and filter values
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
        if level1:
            base_where.append(f"{SRC_L1} = %s")
            base_params.append(level1)
        if level2:
            base_where.append(f"{SRC_L2} = %s")
            base_params.append(level2)
        if master_level1:
            base_where.append("da.level1 = %s")
            base_params.append(master_level1)
        if master_level2:
            base_where.append("da.level2 = %s")
            base_params.append(master_level2)
        if pnl_structure_id:
            base_where.append("COALESCE(ps.id, ps2.id) = %s")
            base_params.append(pnl_structure_id)

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

        # KPI counts (include master_level1/2 filters, exclude status filter)
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

        # Base context for filter value queries: source/company/search only
        # (no level1/level2/master filters so dropdowns show all available values)
        fv_where  = ["das.is_active = TRUE"]
        fv_params = []
        if source_id:
            fv_where.append("das.source_id = %s")
            fv_params.append(source_id)
        if company:
            fv_where.append("das.expense_company ILIKE %s")
            fv_params.append(f"%{company}%")
        if search:
            fv_where.append(
                "(das.source_article_id ILIKE %s OR das.source_article_name ILIKE %s)"
            )
            fv_params.extend([f"%{search}%", f"%{search}%"])
        fv_sql = " AND ".join(fv_where)

        # Source L1 values — independent of Source L2 selection
        cur.execute(
            f"""
            SELECT DISTINCT {SRC_L1} AS val FROM dim_article_source das
            WHERE {fv_sql} AND {SRC_L1} IS NOT NULL AND {SRC_L1} <> ''
            ORDER BY val
            """,
            fv_params,
        )
        level1_values = [r[0] for r in cur.fetchall()]

        # Source L2 values — independent of Source L1 selection
        cur.execute(
            f"""
            SELECT DISTINCT {SRC_L2} AS val FROM dim_article_source das
            WHERE {fv_sql} AND {SRC_L2} IS NOT NULL AND {SRC_L2} <> ''
            ORDER BY val
            """,
            fv_params,
        )
        level2_values = [r[0] for r in cur.fetchall()]

        # Master L1 values — independent of Master L2 selection
        cur.execute(
            f"""
            SELECT DISTINCT da.level1 AS val
            {join_sql}
            WHERE {fv_sql} AND da.level1 IS NOT NULL AND da.level1 <> ''
            ORDER BY val
            """,
            fv_params,
        )
        master_level1_values = [r[0] for r in cur.fetchall()]

        # Master L2 values — independent of Master L1 selection
        cur.execute(
            f"""
            SELECT DISTINCT da.level2 AS val
            {join_sql}
            WHERE {fv_sql} AND da.level2 IS NOT NULL AND da.level2 <> ''
            ORDER BY val
            """,
            fv_params,
        )
        master_level2_values = [r[0] for r in cur.fetchall()]

        # PNL structure values — includes both bound master (ps) and staging default (ps2)
        cur.execute(
            f"""
            SELECT DISTINCT
                COALESCE(ps.id,       ps2.id)       AS pnl_id,
                COALESCE(ps.pnl_code, ps2.pnl_code) AS pnl_code,
                COALESCE(ps.pnl_name, ps2.pnl_name) AS pnl_name
            {join_sql}
            WHERE {fv_sql} AND COALESCE(ps.id, ps2.id) IS NOT NULL
            ORDER BY pnl_id
            """,
            fv_params,
        )
        pnl_structure_values = [
            {"id": r[0], "pnl_code": r[1], "pnl_name": r[2]}
            for r in cur.fetchall()
        ]

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
                da.article_name    AS master_article_name,
                da.level1          AS master_level1,
                da.level2          AS master_level2,
                da.article_type    AS master_article_type,
                das.default_article_type,
                das.default_pnl_id,
                das.default_master_level1,
                das.default_master_level2,
                das.default_master_article_name,
                COALESCE(ps.id,       ps2.id)       AS pnl_structure_id,
                COALESCE(ps.pnl_code, ps2.pnl_code) AS pnl_structure_code,
                COALESCE(ps.pnl_name, ps2.pnl_name) AS pnl_structure_name
            {join_sql}
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
                "master_article_name":        r[18],
                "master_level1":              r[19],
                "master_level2":              r[20],
                "master_article_type":        r[21],
                "default_article_type":        r[22],
                "default_pnl_id":              r[23],
                "default_master_level1":       r[24],
                "default_master_level2":       r[25],
                "default_master_article_name": r[26],
                "pnl_structure_id":            r[27],
                "pnl_structure_code":          r[28],
                "pnl_structure_name":          r[29],
            }
            for r in rows
        ]

        return {
            "status":    "ok",
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "kpi":       kpi,
            "filters": {
                "level1_values":         level1_values,
                "level2_values":         level2_values,
                "master_level1_values":  master_level1_values,
                "master_level2_values":  master_level2_values,
                "pnl_structure_values":  pnl_structure_values,
            },
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


# ── bulk fill preview ─────────────────────────────────────────────────────────

_IS_MAPPED   = "asm.master_article_id IS NOT NULL AND asm.mapping_status IN ('mapped', 'auto')"
_IS_UNMAPPED = "(asm.master_article_id IS NULL OR asm.mapping_status NOT IN ('mapped', 'auto') OR asm.mapping_status IS NULL)"


@router.post("/bulk-fill-preview")
def bulk_fill_preview(req: BulkFillRequest):
    _ensure_staging_default_columns()
    if req.field not in _ALLOWED_FIELDS:
        return {"status": "error", "message": f"Поле '{req.field}' не дозволено для bulk-fill"}
    if not _has_any_filter(req.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur  = conn.cursor()
    try:
        join_sql          = _bulk_join()
        where_sql, params = _build_bulk_base_where(req.filters)
        cur.execute(
            f"""
            SELECT
              COUNT(DISTINCT asm.master_article_id) FILTER (WHERE {_IS_MAPPED}),
              COUNT(*) FILTER (WHERE {_IS_UNMAPPED})
            {join_sql}
            WHERE {where_sql}
            """,
            params,
        )
        row                   = cur.fetchone()
        affected_master_count = int(row[0])
        affected_source_count = int(row[1])

        warnings = []
        if affected_master_count > 500:
            warnings.append(f"Буде оновлено {affected_master_count} master-статей — це велика операція.")
        if affected_master_count == 0 and affected_source_count == 0:
            warnings.append("Жодного рядка не знайдено за поточними фільтрами.")

        return {
            "status":               "ok",
            "affected_master_count": affected_master_count,
            "affected_source_count": affected_source_count,
            "total_affected_count":  affected_master_count + affected_source_count,
            "field_label":           _FIELD_LABELS[req.field],
            "value":                 req.value,
            "warnings":              warnings,
        }
    finally:
        cur.close()
        conn.close()


# ── bulk fill apply ───────────────────────────────────────────────────────────

@router.post("/bulk-fill")
def bulk_fill(req: BulkFillRequest):
    _ensure_staging_default_columns()
    if not req.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if req.field not in _ALLOWED_FIELDS:
        return {"status": "error", "message": f"Поле '{req.field}' не дозволено для bulk-fill"}
    if not _has_any_filter(req.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur  = conn.cursor()
    try:
        join_sql          = _bulk_join()
        where_sql, params = _build_bulk_base_where(req.filters)

        # 1. Update mapped master articles in dim_article
        cur.execute(
            f"SELECT DISTINCT asm.master_article_id {join_sql} WHERE {where_sql} AND {_IS_MAPPED}",
            params,
        )
        master_ids      = [r[0] for r in cur.fetchall()]
        updated_masters = 0
        if master_ids:
            db_field = _ALLOWED_FIELDS[req.field]
            value    = int(req.value) if req.field == "pnl_id" else req.value
            cur.execute(
                f"UPDATE dim_article SET {db_field} = %s WHERE article_id = ANY(%s)",
                (value, master_ids),
            )
            updated_masters = cur.rowcount

        # 2. Write default values to unmapped dim_article_source rows
        staging_col   = _STAGING_DEFAULT_FIELDS[req.field]
        staging_value = int(req.value) if req.field == "pnl_id" else req.value
        cur.execute(
            f"""
            UPDATE dim_article_source SET {staging_col} = %s
            WHERE (source_id, source_article_id) IN (
                SELECT das.source_id, das.source_article_id
                {join_sql}
                WHERE {where_sql} AND {_IS_UNMAPPED}
            )
            """,
            [staging_value] + params,
        )
        updated_staging = cur.rowcount

        conn.commit()
        return {
            "status":          "ok",
            "updated_masters": updated_masters,
            "updated_staging": updated_staging,
            "field_label":     _FIELD_LABELS[req.field],
            "value":           req.value,
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── bulk create helpers ───────────────────────────────────────────────────────

class BulkCreateRequest(BaseModel):
    filters: BulkFillFilters
    confirm: bool = False


def _build_bulk_create_select(where_sql: str) -> str:
    """Returns SELECT that picks eligible unmapped rows with all required fields."""
    join_sql = _bulk_join()
    return f"""
        SELECT
            das.source_id,
            das.source_article_id,
            COALESCE(NULLIF(das.default_master_article_name, ''), NULLIF(das.source_article_name, '')) AS article_name,
            COALESCE(NULLIF(das.default_article_type, ''),        NULLIF(das.source_article_type, '')) AS article_type,
            das.default_pnl_id                                                                          AS pnl_id,
            COALESCE(NULLIF(das.default_master_level1, ''),       '')                                   AS level1,
            COALESCE(NULLIF(das.default_master_level2, ''),       '')                                   AS level2,
            das.uid_expense_article,
            das.expense_element,
            das.expense_company,
            das.level1_olap,
            das.level2_olap
        {join_sql}
        WHERE {where_sql}
          AND {_IS_UNMAPPED}
          AND COALESCE(NULLIF(das.default_article_type, ''), NULLIF(das.source_article_type, '')) IS NOT NULL
          AND das.default_pnl_id IS NOT NULL
          AND COALESCE(NULLIF(das.default_master_article_name, ''), NULLIF(das.source_article_name, '')) IS NOT NULL
    """


# ── bulk create preview ───────────────────────────────────────────────────────

@router.post("/bulk-create-preview")
def bulk_create_preview(req: BulkCreateRequest):
    _ensure_staging_default_columns()
    if not _has_any_filter(req.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur  = conn.cursor()
    try:
        join_sql          = _bulk_join()
        where_sql, params = _build_bulk_base_where(req.filters)

        # Total unmapped in current filter
        cur.execute(
            f"SELECT COUNT(*) {join_sql} WHERE {where_sql} AND {_IS_UNMAPPED}",
            params,
        )
        total_unmapped = int(cur.fetchone()[0])

        # Eligible (all required fields present)
        cur.execute(_build_bulk_create_select(where_sql), params)
        eligible_rows = cur.fetchall()
        eligible_count = len(eligible_rows)

        # Already exist in dim_article
        existing_ids = []
        if eligible_rows:
            article_ids = [r[1] for r in eligible_rows]
            cur.execute(
                "SELECT article_id FROM dim_article WHERE article_id = ANY(%s)",
                (article_ids,),
            )
            existing_ids = [r[0] for r in cur.fetchall()]

        skipped_existing  = len(existing_ids)
        will_create       = eligible_count - skipped_existing
        not_eligible      = total_unmapped - eligible_count

        missing_reasons = []
        if not_eligible > 0:
            missing_reasons.append(f"{not_eligible} рядків не мають обов'язкових полів (Тип, PNL структура, Назва)")

        return {
            "status":           "ok",
            "total_unmapped":   total_unmapped,
            "eligible_count":   eligible_count,
            "will_create":      will_create,
            "skipped_existing": skipped_existing,
            "missing_reasons":  missing_reasons,
        }
    finally:
        cur.close()
        conn.close()


# ── bulk create apply ─────────────────────────────────────────────────────────

@router.post("/bulk-create")
def bulk_create(req: BulkCreateRequest):
    _ensure_staging_default_columns()
    if not req.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if not _has_any_filter(req.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur  = conn.cursor()
    try:
        where_sql, params = _build_bulk_base_where(req.filters)
        cur.execute(_build_bulk_create_select(where_sql), params)
        rows = cur.fetchall()

        # Skip already existing
        if rows:
            article_ids = [r[1] for r in rows]
            cur.execute(
                "SELECT article_id FROM dim_article WHERE article_id = ANY(%s)",
                (article_ids,),
            )
            existing = {r[0] for r in cur.fetchall()}
        else:
            existing = set()

        created = 0
        bound   = 0
        for r in rows:
            source_id, article_id, article_name, article_type, pnl_id, \
                level1, level2, uid, expense_element, expense_company, \
                level1_olap, level2_olap = r

            if article_id in existing:
                continue

            cur.execute(
                """
                INSERT INTO dim_article
                    (article_id, article_name, article_type, pnl_id,
                     level1, level2, uid_expense_article,
                     expense_element, expense_company, level1_olap, level2_olap,
                     is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
                ON CONFLICT (article_id) DO NOTHING
                """,
                (article_id, article_name, article_type, pnl_id,
                 level1 or None, level2 or None, uid or None,
                 expense_element or None, expense_company or None,
                 level1_olap or None, level2_olap or None),
            )
            if cur.rowcount:
                created += 1
                cur.execute(
                    """
                    INSERT INTO article_source_mapping
                        (source_id, source_article_id, master_article_id,
                         mapping_status, confidence, updated_at)
                    VALUES (%s, %s, %s, 'mapped', 100, NOW())
                    ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                        master_article_id = EXCLUDED.master_article_id,
                        mapping_status    = 'mapped',
                        confidence        = 100,
                        updated_at        = NOW()
                    """,
                    (source_id, article_id, article_id),
                )
                bound += 1

        conn.commit()
        return {"status": "ok", "created": created, "bound": bound, "skipped": len(existing)}
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
