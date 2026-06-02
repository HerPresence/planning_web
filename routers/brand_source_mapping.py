"""
Brand / НГ source mapping — HTTP endpoints.
Manages dim_brand_source ↔ brand_source_mapping ↔ dim_brand correspondence.
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

log = logging.getLogger("brand_mapping")

from auth.dependencies import get_current_user, require_admin, require_superadmin
from db import get_connection
from services.audit_service import log_action
from services.brand_matching_service import (
    normalize_brand_name,
    find_best_match,
    batch_find_suggestions,
    get_recommendation,
    compute_match_score,
)

router = APIRouter(prefix="/api/brand-source-mapping")


# ── Request models ────────────────────────────────────────────────────────────

class BindRequest(BaseModel):
    source_id:       int
    source_brand_id: str
    master_brand_id: int


class RejectRequest(BaseModel):
    source_id:       int
    source_brand_id: str


class AutoBindRequest(BaseModel):
    source_id: Optional[int] = None


class BulkFillFilters(BaseModel):
    source_id:       Optional[int] = None
    brand_group:     Optional[str] = None
    master_brand_id: Optional[int] = None
    mapping_status:  Optional[str] = None
    search:          Optional[str] = None


class BulkFillRequest(BaseModel):
    filters: BulkFillFilters
    field:   str
    value:   str
    confirm: bool = False


class BulkCreateRequest(BaseModel):
    filters: BulkFillFilters
    confirm: bool = False


class CreateAndBindRequest(BaseModel):
    source_id:         int
    source_brand_id:   str
    brand_uid:         Optional[str] = None
    brand_name:        str
    brand_group:       Optional[str] = None
    parent_brand_uid:  Optional[str] = None
    parent_brand_name: Optional[str] = None


class CreateFromMappingRequest(BaseModel):
    source_id:       int
    source_brand_id: str


class UnmapRequest(BaseModel):
    source_id:       int
    source_brand_id: str


# ── Bulk-fill field config ────────────────────────────────────────────────────

_ALLOWED_FIELDS = {
    "brand_group":       "brand_group",
    "brand_name":        "brand_name",
    "parent_brand_uid":  "parent_brand_uid",
    "parent_brand_name": "parent_brand_name",
}

_FIELD_LABELS = {
    "brand_group":       "Група бренду",
    "brand_name":        "Назва master-бренду",
    "parent_brand_uid":  "Parent UID",
    "parent_brand_name": "Parent name",
}

_STAGING_DEFAULT_FIELDS = {
    "brand_group":       "default_brand_group",
    "brand_name":        "default_brand_name",
    "parent_brand_uid":  "default_parent_brand_uid",
    "parent_brand_name": "default_parent_brand_name",
}

# Rows with an active master-brand binding
_IS_MAPPED  = "bsm.master_brand_id IS NOT NULL AND bsm.mapping_status IN ('mapped', 'auto')"
# Rows not yet mapped (pending or no mapping row)
_IS_PENDING = "(bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)"

# Computed status priority: rejected > mapped > parent_missing > duplicate_id > source_changed > ready_to_create > pending
_COMPUTED_STATUS = """CASE
    WHEN bsm.mapping_status = 'rejected' THEN 'rejected'
    WHEN bsm.mapping_status IN ('mapped', 'auto') THEN 'mapped'
    WHEN dbs.source_parent_uid IS NOT NULL AND dbs.source_parent_uid <> ''
         AND NOT EXISTS (SELECT 1 FROM dim_brand _pb WHERE _pb.brand_uid = dbs.source_parent_uid)
         THEN 'parent_missing'
    WHEN (bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)
         AND EXISTS (SELECT 1 FROM dim_brand _eb WHERE _eb.brand_uid = dbs.source_brand_id)
         THEN 'duplicate_id'
    WHEN (bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)
         AND dbs.source_changed = TRUE
         THEN 'source_changed'
    WHEN (bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)
         AND dbs.source_brand_id IS NOT NULL AND dbs.source_brand_id <> ''
         AND COALESCE(NULLIF(dbs.default_brand_name,  ''), NULLIF(dbs.source_brand_name,  '')) IS NOT NULL
         AND COALESCE(NULLIF(dbs.default_brand_group, ''), NULLIF(dbs.source_brand_group, '')) IS NOT NULL
         THEN 'ready_to_create'
    ELSE 'pending'
END"""


# ── Column migration ──────────────────────────────────────────────────────────

_defaults_initialized = False


def _ensure_brand_columns():
    """Add optional columns to dim_brand_source and dim_brand once per process."""
    global _defaults_initialized
    if _defaults_initialized:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        for ddl in [
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS default_brand_name TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS default_brand_group TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS default_parent_brand_uid TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS default_parent_brand_name TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS last_batch_id INTEGER",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS seen_count INTEGER DEFAULT 1",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_changed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS changed_fields JSONB DEFAULT '[]'::JSONB",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS previous_snapshot JSONB",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_level TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_company_name TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_is_active TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_brand_ref_id TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived_by INTEGER",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archive_reason TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS parent_brand_uid TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS parent_brand_name TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS source_level TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS source_company_name TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS source_is_active TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS source_brand_ref_id TEXT",
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS normalized_name TEXT",
        ]:
            cur.execute(ddl)
        # Index cannot be inside a transaction on some Postgres versions, but IF NOT EXISTS is safe
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_dim_brand_normalized"
            " ON dim_brand(normalized_name) WHERE normalized_name IS NOT NULL"
        )
        conn.commit()
        _defaults_initialized = True
    finally:
        cur.close()
        conn.close()


# ── Bulk-fill helpers ─────────────────────────────────────────────────────────

def _has_any_filter(f: BulkFillFilters) -> bool:
    return bool(
        f.source_id or f.brand_group or f.master_brand_id
        or (f.mapping_status and f.mapping_status not in ("all",))
        or f.search
    )


def _build_bulk_where(f: BulkFillFilters):
    """Build WHERE clause for bulk operations.  References aliases dbs / bsm / b."""
    where  = []
    params = []

    if f.source_id:
        where.append("dbs.source_id = %s")
        params.append(f.source_id)

    if f.brand_group:
        where.append("dbs.source_brand_group ILIKE %s")
        params.append(f"%{f.brand_group}%")

    if f.master_brand_id:
        where.append("bsm.master_brand_id = %s")
        params.append(f.master_brand_id)

    if f.search:
        where.append(
            "(dbs.source_brand_name ILIKE %s"
            " OR dbs.source_brand_id  ILIKE %s"
            " OR dbs.source_brand_group ILIKE %s)"
        )
        params += [f"%{f.search}%", f"%{f.search}%", f"%{f.search}%"]

    if f.mapping_status and f.mapping_status not in ("all",):
        if f.mapping_status == "pending":
            where.append("(bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)")
        else:
            where.append("bsm.mapping_status = %s")
            params.append(f.mapping_status)

    return (" AND ".join(where)) if where else "TRUE", params


_BULK_JOIN = """
    FROM dim_brand_source dbs
    LEFT JOIN brand_source_mapping bsm
           ON bsm.source_id = dbs.source_id
          AND bsm.source_brand_id = dbs.source_brand_id
    LEFT JOIN dim_brand b ON b.id = bsm.master_brand_id
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_staged(r) -> dict:
    mapping_status  = r[11] or "pending"
    master_brand_id = r[12]
    # r[17] = default_brand_name, r[18] = default_brand_group
    eff_name  = ((r[17] or "").strip() or (r[4] or "").strip())
    eff_group = ((r[18] or "").strip() or (r[5] or "").strip())

    exists_in_master = (master_brand_id is not None) and mapping_status in ("mapped", "auto")
    # r[24] = computed_status from SQL CASE expression (when present)
    computed_status  = r[24] if len(r) > 24 and r[24] else mapping_status
    is_ready_for_create = computed_status == "ready_to_create"

    return {
        "id":                    r[0],
        "source_id":             r[1],
        "source_name":           r[2],
        "source_brand_id":       r[3],
        "source_brand_name":     r[4],
        "source_brand_group":    r[5],
        "source_parent_uid":     r[6],
        "source_parent_name":    r[7],
        "loaded_at":             str(r[8]) if r[8] else None,
        "is_active":             r[9],
        "mapping_id":            r[10],
        "mapping_status":        mapping_status,
        "master_brand_id":       master_brand_id,
        "confidence":            float(r[13]) if r[13] is not None else 0,
        "master_brand_name":     r[14],
        "master_brand_uid":      r[15],
        "master_brand_group":    r[16],
        "exists_in_master":      exists_in_master,
        "is_ready_for_create":   is_ready_for_create,
        "effective_brand_name":  eff_name,
        "effective_brand_group": eff_group,
        "extra_fields":          r[19] or {},
        "source_changed":        bool(r[20]) if len(r) > 20 and r[20] is not None else False,
        "seen_count":            int(r[21]) if len(r) > 21 and r[21] is not None else 1,
        "last_seen_at":          str(r[22]) if len(r) > 22 and r[22] else None,
        "last_batch_id":         r[23] if len(r) > 23 else None,
        "computed_status":       computed_status,
        "changed_fields":        r[25] if len(r) > 25 and r[25] is not None else [],
        "previous_snapshot":     r[26] if len(r) > 26 else None,
        "source_level":          r[27] if len(r) > 27 else None,
        "source_company_name":   r[28] if len(r) > 28 else None,
        "source_is_active":      r[29] if len(r) > 29 else None,
        "source_brand_ref_id":   r[30] if len(r) > 30 else None,
        "archived":              bool(r[31]) if len(r) > 31 and r[31] is not None else False,
        "archived_at":           str(r[32]) if len(r) > 32 and r[32] else None,
        "archive_reason":        r[33] if len(r) > 33 else None,
        "mapped_at":             str(r[34]) if len(r) > 34 and r[34] else None,
        "mapped_by":             r[35] if len(r) > 35 else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/staged")
def get_staged(
    source_id:          Optional[int] = None,
    mapping_status:     Optional[str] = None,
    brand_group:        Optional[str] = None,
    master_brand_id:    Optional[int] = None,
    master_brand_group: Optional[str] = None,
    search:             Optional[str] = None,
    computed_status:    Optional[str] = None,
    source_changed:     Optional[bool] = None,
    company:            Optional[str] = None,
    source_level:       Optional[str] = None,
    source_is_active:   Optional[str] = None,
    visibility:         str = "active",
    recommendation:     Optional[str] = None,
    page:               int = 1,
    page_size:          int = 100,
    _u=Depends(get_current_user),
):
    """Return source brands with their mapping status and master brand info.
    visibility: active | inactive | archived | all

    Pagination contract:
    - COUNT and data queries use the IDENTICAL CTE — they are always consistent.
    - `recommendation` filter is computed server-side per page and returned in each row;
      filtering by it is a CLIENT-SIDE operation (do not re-fetch on recommendation change).
    - Counters are split into two groups:
        Processing status (global, DB):  cnt_unprocessed / cnt_linked / cnt_rejected
        Recommendation  (this page only): rec_auto / rec_match / rec_review / rec_create
    """
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        # ── Build WHERE conditions ────────────────────────────────────────────
        conds  = []
        params = []

        if visibility == "active":
            conds.append("dbs.is_active = TRUE AND COALESCE(dbs.archived, FALSE) = FALSE")
        elif visibility == "inactive":
            conds.append("dbs.is_active = FALSE AND COALESCE(dbs.archived, FALSE) = FALSE")
        elif visibility == "archived":
            conds.append("COALESCE(dbs.archived, FALSE) = TRUE")
        # "all" → no visibility filter

        if source_id:
            conds.append("dbs.source_id = %s"); params.append(source_id)

        if mapping_status:
            if mapping_status == "pending":
                conds.append("(bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)")
            elif mapping_status == "linked":
                conds.append("bsm.mapping_status IN ('mapped', 'auto')")
            else:
                conds.append("bsm.mapping_status = %s"); params.append(mapping_status)

        if brand_group:
            conds.append("dbs.source_brand_group ILIKE %s"); params.append(f"%{brand_group}%")

        if master_brand_id:
            conds.append("bsm.master_brand_id = %s"); params.append(master_brand_id)

        if master_brand_group:
            conds.append("b.brand_group ILIKE %s"); params.append(f"%{master_brand_group}%")

        if search:
            conds.append(
                "(dbs.source_brand_name ILIKE %s"
                " OR dbs.source_brand_id ILIKE %s"
                " OR dbs.source_brand_group ILIKE %s)"
            )
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]

        if source_changed is not None:
            conds.append("dbs.source_changed = %s"); params.append(source_changed)

        if computed_status:
            conds.append(f"({_COMPUTED_STATUS}) = %s"); params.append(computed_status)

        if company:
            conds.append("dbs.source_company_name ILIKE %s"); params.append(f"%{company}%")

        if source_level:
            conds.append("dbs.source_level ILIKE %s"); params.append(f"%{source_level}%")

        if source_is_active is not None and source_is_active != "":
            conds.append("LOWER(dbs.source_is_active) = LOWER(%s)"); params.append(source_is_active)

        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        log.debug(
            "[get_staged] filters=%s visibility=%s page=%s page_size=%s where=%s",
            params, visibility, page, page_size, where,
        )

        # ── CTE: single source-of-truth for all rows matching current filters ─
        # Both the COUNT query and the data query reference this exact same expression.
        # This guarantees that total == number of rows across all pages.
        _JOIN = """
            FROM dim_brand_source dbs
            LEFT JOIN brand_source_mapping bsm
                   ON bsm.source_id       = dbs.source_id
                  AND bsm.source_brand_id = dbs.source_brand_id
            LEFT JOIN dim_brand b ON b.id = bsm.master_brand_id
        """

        cte_body = f"""
            WITH filtered AS (
                SELECT
                    dbs.id,           dbs.source_id,    dbs.source_name,
                    dbs.source_brand_id,  dbs.source_brand_name,
                    dbs.source_brand_group, dbs.source_parent_uid, dbs.source_parent_name,
                    dbs.loaded_at,    dbs.is_active,
                    bsm.id           AS mapping_id,
                    bsm.mapping_status,
                    bsm.master_brand_id,
                    bsm.confidence,
                    b.brand_name     AS master_brand_name,
                    b.brand_uid      AS master_brand_uid,
                    b.brand_group    AS master_brand_group,
                    dbs.default_brand_name,
                    dbs.default_brand_group,
                    dbs.extra_fields,
                    dbs.source_changed,
                    dbs.seen_count,
                    dbs.last_seen_at,
                    dbs.last_batch_id,
                    {_COMPUTED_STATUS} AS computed_status,
                    dbs.changed_fields,
                    dbs.previous_snapshot,
                    dbs.source_level,
                    dbs.source_company_name,
                    dbs.source_is_active,
                    dbs.source_brand_ref_id,
                    COALESCE(dbs.archived, FALSE) AS archived,
                    dbs.archived_at,
                    dbs.archive_reason,
                    bsm.updated_at  AS mapped_at,
                    bsm.mapped_by
                {_JOIN}
                {where}
            )
        """

        # ── 1. Total count (identical WHERE as data query) ────────────────────
        cur.execute(f"{cte_body} SELECT COUNT(*) FROM filtered", params)
        total = int((cur.fetchone() or [0])[0])
        page_size = min(max(page_size, 1), 500)
        total_pages = max(1, (total + page_size - 1) // page_size)
        offset = (max(page, 1) - 1) * page_size

        log.debug("[get_staged] total=%s total_pages=%s page=%s offset=%s", total, total_pages, page, offset)

        # ── 2. Paginated data (same CTE body) ────────────────────────────────
        cur.execute(
            f"""{cte_body}
            SELECT * FROM filtered
            ORDER BY
                CASE WHEN mapping_status = 'pending' OR mapping_status IS NULL THEN 0
                     WHEN mapping_status = 'mapped'  THEN 1
                     WHEN mapping_status = 'auto'    THEN 2
                     ELSE 3 END,
                source_brand_name
            LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = [_row_to_staged(r) for r in cur.fetchall()]

        log.debug("[get_staged] returned_rows=%s", len(rows))

        # ── 3. Global processing-status counters (DB, all rows for current non-visibility filters)
        _VISIBILITY_KEYWORDS = ("dbs.is_active", "COALESCE(dbs.archived")
        base_conds  = [c for c in conds if not any(kw in c for kw in _VISIBILITY_KEYWORDS)]
        base_params = params  # visibility conds never add %s params
        base_where  = ("WHERE " + " AND ".join(base_conds)) if base_conds else ""

        cur.execute(
            f"""SELECT
                    -- lifecycle (ignoring visibility filter so all tabs see correct totals)
                    COUNT(*) FILTER (WHERE dbs.is_active = TRUE  AND COALESCE(dbs.archived,FALSE)=FALSE),
                    COUNT(*) FILTER (WHERE dbs.is_active = FALSE AND COALESCE(dbs.archived,FALSE)=FALSE),
                    COUNT(*) FILTER (WHERE COALESCE(dbs.archived,FALSE) = TRUE),
                    -- processing status (unprocessed / linked / rejected)
                    COUNT(*) FILTER (WHERE bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL),
                    COUNT(*) FILTER (WHERE bsm.mapping_status IN ('mapped', 'auto')),
                    COUNT(*) FILTER (WHERE bsm.mapping_status = 'rejected'),
                    -- detail breakdown (for legacy pill buttons)
                    COUNT(*) FILTER (WHERE bsm.mapping_status = 'mapped'),
                    COUNT(*) FILTER (WHERE bsm.mapping_status = 'auto'),
                    COUNT(*) FILTER (WHERE dbs.source_changed = TRUE)
               {_JOIN}
               {base_where}""",
            base_params,
        )
        kpi = cur.fetchone()
        active_total   = int(kpi[0] or 0)
        inactive_total = int(kpi[1] or 0)
        archived_total = int(kpi[2] or 0)
        cnt_unprocessed = int(kpi[3] or 0)  # processing status: not yet handled
        cnt_linked      = int(kpi[4] or 0)  # processing status: bound to master
        cnt_rejected    = int(kpi[5] or 0)  # processing status: rejected
        cnt_mapped_only = int(kpi[6] or 0)
        cnt_auto_only   = int(kpi[7] or 0)
        source_changed_count = int(kpi[8] or 0)

        # ── 4. Suggestion engine for pending rows on this page ────────────────
        if rows and any(r.get("mapping_status") in ("pending", None) for r in rows):
            cur.execute(
                """SELECT id, brand_name, brand_group,
                          COALESCE(normalized_name, '') as normalized_name
                   FROM dim_brand WHERE is_active = TRUE"""
            )
            masters_raw = cur.fetchall()
            cur.execute(
                """SELECT master_brand_id, COUNT(*) FROM brand_source_mapping
                   WHERE master_brand_id IS NOT NULL GROUP BY master_brand_id"""
            )
            sources_count = {r[0]: r[1] for r in cur.fetchall()}
            masters = [
                {
                    "id": r[0], "brand_name": r[1] or "", "brand_group": r[2] or "",
                    "normalized_name": r[3] or normalize_brand_name(r[1] or ""),
                    "mapped_sources_count": sources_count.get(r[0], 0),
                }
                for r in masters_raw
            ]
            pending_rows = {r["source_brand_id"]: r["source_brand_name"] for r in rows
                            if r.get("mapping_status") in ("pending", None)}
            if pending_rows and masters:
                source_list = [{"source_brand_id": k, "source_brand_name": v}
                                for k, v in pending_rows.items()]
                suggestions = batch_find_suggestions(source_list, masters)
                for row in rows:
                    if row.get("source_brand_id") in suggestions:
                        row.update(suggestions[row["source_brand_id"]])
                    else:
                        row.setdefault("suggested_master_brand_id", None)
                        row.setdefault("match_score", None)
                        row.setdefault("recommendation", None)
                        row.setdefault("normalized_source_name", None)

        # ── 5. Recommendation counters from THIS PAGE rows only ───────────────
        # NOTE: These count rows on the current page, not globally.
        # Clients must not rely on them for global KPIs.
        rec_auto   = sum(1 for r in rows if r.get("recommendation") == "AUTO_BIND")
        rec_match  = sum(1 for r in rows if r.get("recommendation") == "RECOMMEND_BIND")
        rec_review = sum(1 for r in rows if r.get("recommendation") == "REVIEW")
        rec_create = sum(1 for r in rows if r.get("recommendation") == "CREATE")

        # ── 6. Filter dropdown options ────────────────────────────────────────
        cur.execute("SELECT DISTINCT dbs.source_id, dbs.source_name FROM dim_brand_source dbs ORDER BY dbs.source_name")
        sources = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT source_brand_group FROM dim_brand_source WHERE source_brand_group IS NOT NULL AND source_brand_group != '' ORDER BY source_brand_group")
        source_groups = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT source_company_name FROM dim_brand_source WHERE source_company_name IS NOT NULL AND source_company_name != '' ORDER BY source_company_name")
        companies = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT source_level FROM dim_brand_source WHERE source_level IS NOT NULL AND source_level != '' ORDER BY source_level")
        levels = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT source_is_active FROM dim_brand_source WHERE source_is_active IS NOT NULL AND source_is_active != '' ORDER BY source_is_active")
        active_values = [r[0] for r in cur.fetchall()]

        return {
            # Pagination
            "total":       total,
            "total_pages": total_pages,
            "page":        page,
            "page_size":   page_size,

            # Lifecycle (global, ignoring visibility)
            "active_total":   active_total,
            "inactive_total": inactive_total,
            "archived_total": archived_total,

            # Processing status counters (global, DB — two clearly separated concepts)
            "cnt_unprocessed": cnt_unprocessed,  # pending / null — not yet handled
            "cnt_linked":      cnt_linked,        # mapped + auto — bound to master
            "cnt_rejected":    cnt_rejected,       # rejected

            # Legacy names kept for backward compat with existing filters/buttons
            "pending":    cnt_unprocessed,
            "mapped":     cnt_mapped_only,
            "auto_bound": cnt_auto_only,
            "rejected":   cnt_rejected,
            "source_changed_count": source_changed_count,

            # Recommendation counters — CURRENT PAGE ONLY, not global
            "rec_auto":   rec_auto,    # AUTO_BIND   (score >= 95)
            "rec_match":  rec_match,   # RECOMMEND_BIND (score >= 80)
            "rec_review": rec_review,  # REVIEW      (score >= 60)
            "rec_create": rec_create,  # CREATE      (score < 60)

            # Legacy recommendation names (for "Bind all AUTO" button)
            "auto_bind_candidates": rec_auto,
            "recommend_bind_count": rec_match,
            "review_count":         rec_review,
            "create_candidates":    rec_create,

            "rows":          rows,
            "sources":       sources,
            "source_groups": source_groups,
            "companies":     companies,
            "levels":        levels,
            "active_values": active_values,
        }
    finally:
        cur.close()
        conn.close()


@router.get("/masters")
def get_masters(_u=Depends(get_current_user)):
    """Return active master brands from dim_brand."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, brand_uid, brand_name, brand_group
               FROM dim_brand
               WHERE is_active = TRUE
               ORDER BY brand_name"""
        )
        return [
            {"id": r[0], "brand_uid": r[1], "brand_name": r[2], "brand_group": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


@router.post("/bind")
def bind_brand(body: BindRequest, _u=Depends(get_current_user)):
    """Bind a source brand to a master brand (mapping_status = mapped)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM dim_brand WHERE id = %s AND is_active = TRUE", (body.master_brand_id,))
        if not cur.fetchone():
            raise HTTPException(404, f"Master brand #{body.master_brand_id} not found or inactive")

        cur.execute(
            """INSERT INTO brand_source_mapping
                   (source_id, source_brand_id, master_brand_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
               ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                   master_brand_id = EXCLUDED.master_brand_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_brand_id, body.master_brand_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True, "source_brand_id": body.source_brand_id, "master_brand_id": body.master_brand_id}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/reject")
def reject_brand(body: RejectRequest, _u=Depends(get_current_user)):
    """Mark a source brand as rejected (no master match)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO brand_source_mapping
                   (source_id, source_brand_id, master_brand_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, NULL, 'rejected', 0, %s, NOW())
               ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                   master_brand_id = NULL,
                   mapping_status  = 'rejected',
                   confidence      = 0,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_brand_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True, "source_brand_id": body.source_brand_id}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/unmap")
def unmap_brand(body: UnmapRequest, _u=Depends(get_current_user)):
    """Reset a source brand mapping back to pending (remove master binding)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE brand_source_mapping
               SET master_brand_id = NULL,
                   mapping_status  = 'pending',
                   confidence      = 0,
                   updated_at      = NOW()
               WHERE source_id = %s AND source_brand_id = %s""",
            (body.source_id, body.source_brand_id),
        )
        conn.commit()
        return {"ok": True, "source_brand_id": body.source_brand_id}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


# ── Cleanup center (SuperAdmin only) ─────────────────────────────────────────

_ARCHIVABLE_COND = """
    dbs.is_active = FALSE
    AND COALESCE(dbs.archived, FALSE) = FALSE
    AND (bsm.mapping_status IS NULL OR bsm.mapping_status IN ('pending', 'rejected'))
    AND bsm.master_brand_id IS NULL
"""


def _cleanup_base_query(source_id):
    src_filter = "AND dbs.source_id = %s" if source_id else ""
    params = [int(source_id)] if source_id else []
    return src_filter, params


@router.get("/cleanup-preview")
def cleanup_preview(source_id: Optional[int] = None, _u=Depends(require_superadmin)):
    """Preview what would be archived — no changes made."""
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        src_filter, params = _cleanup_base_query(source_id)
        base_join = """FROM dim_brand_source dbs
            LEFT JOIN brand_source_mapping bsm
                   ON bsm.source_id = dbs.source_id
                  AND bsm.source_brand_id = dbs.source_brand_id"""

        cur.execute(
            f"""SELECT COUNT(*) {base_join}
                WHERE {_ARCHIVABLE_COND} {src_filter}""",
            params,
        )
        can_archive = (cur.fetchone() or [0])[0]

        cur.execute(
            f"""SELECT COUNT(*) {base_join}
                WHERE dbs.is_active = FALSE AND COALESCE(dbs.archived,FALSE)=FALSE {src_filter}""",
            params,
        )
        inactive_total = (cur.fetchone() or [0])[0]

        cur.execute(
            f"""SELECT COUNT(*) {base_join}
                WHERE dbs.is_active = FALSE AND COALESCE(dbs.archived,FALSE)=FALSE
                  AND bsm.master_brand_id IS NOT NULL {src_filter}""",
            params,
        )
        skipped_mapped = (cur.fetchone() or [0])[0]

        cur.execute(
            f"""SELECT dbs.source_brand_id, dbs.source_brand_name, dbs.source_brand_group,
                       dbs.source_level, dbs.last_seen_at
                {base_join}
                WHERE {_ARCHIVABLE_COND} {src_filter}
                ORDER BY dbs.source_brand_name
                LIMIT 20""",
            params,
        )
        examples = [
            {"source_brand_id": r[0], "source_brand_name": r[1],
             "source_brand_group": r[2], "source_level": r[3],
             "last_seen_at": str(r[4]) if r[4] else None}
            for r in cur.fetchall()
        ]
        return {
            "inactive_total":  int(inactive_total),
            "can_archive":     int(can_archive),
            "skipped_mapped":  int(skipped_mapped),
            "examples":        examples,
        }
    finally:
        cur.close()
        conn.close()


@router.post("/cleanup-inactive-brands")
def cleanup_inactive_brands(
    source_id: Optional[int] = None,
    archive_reason: Optional[str] = "superadmin_cleanup",
    _u=Depends(require_superadmin),
):
    """Archive inactive unbound source brands. SuperAdmin only.
    Uses CTE with re-check to prevent race conditions.
    """
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        src_filter, params = _cleanup_base_query(source_id)
        base_join = """FROM dim_brand_source dbs
            LEFT JOIN brand_source_mapping bsm
                   ON bsm.source_id = dbs.source_id
                  AND bsm.source_brand_id = dbs.source_brand_id"""

        # Count inactive total (for reporting)
        cur.execute(
            f"""SELECT COUNT(*) {base_join}
                WHERE dbs.is_active = FALSE AND COALESCE(dbs.archived,FALSE)=FALSE {src_filter}""",
            params,
        )
        inactive_total = (cur.fetchone() or [0])[0]

        # Atomic re-check + archive in one statement (prevents race condition)
        # CTE selects archivable IDs at the moment of execution — not at preview time
        cur.execute(
            f"""WITH archivable AS (
                    SELECT dbs.id {base_join}
                    WHERE {_ARCHIVABLE_COND} {src_filter}
                    FOR UPDATE OF dbs SKIP LOCKED
                )
                UPDATE dim_brand_source
                SET archived = TRUE, archived_at = NOW(),
                    archived_by = %s, archive_reason = %s
                WHERE id IN (SELECT id FROM archivable)
                RETURNING id""",
            [_u["id"], archive_reason or "superadmin_cleanup"] + params,
        )
        archived_ids = [r[0] for r in cur.fetchall()]
        archived_count = len(archived_ids)

        conn.commit()

        # Audit log
        log_action(
            action="brand_source_archive",
            user_id=_u["id"],
            user_email=_u.get("email"),
            entity_type="dim_brand_source",
            entity_id=str(source_id) if source_id else "all",
            menu_key="brand_correspondence",
            new_value={
                "archived_count": archived_count,
                "inactive_total": int(inactive_total),
                "skipped_mapped": int(inactive_total) - archived_count,
                "archive_reason": archive_reason or "superadmin_cleanup",
                "source_id": source_id,
            },
        )
        return {
            "inactive_total": int(inactive_total),
            "archived_count": archived_count,
            "skipped_mapped": int(inactive_total) - archived_count,
        }
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/restore-from-archive")
def restore_from_archive(body: UnmapRequest, _u=Depends(require_superadmin)):
    """Restore an archived source brand. SuperAdmin only."""
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE dim_brand_source
               SET archived = FALSE, archived_at = NULL, archived_by = NULL,
                   archive_reason = NULL, updated_at = NOW()
               WHERE source_id = %s AND source_brand_id = %s""",
            (body.source_id, body.source_brand_id),
        )
        conn.commit()

        log_action(
            action="brand_source_restore",
            user_id=_u["id"],
            user_email=_u.get("email"),
            entity_type="dim_brand_source",
            entity_id=body.source_brand_id,
            menu_key="brand_correspondence",
            new_value={"source_id": body.source_id, "source_brand_id": body.source_brand_id},
        )
        return {"ok": True, "source_brand_id": body.source_brand_id}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


class CreateParentBrandRequest(BaseModel):
    source_id:       int
    source_brand_id: str   # child row — used to look up its source_parent_uid / source_parent_name
    brand_group:     Optional[str] = None


@router.post("/create-parent-brand")
def create_parent_brand(body: CreateParentBrandRequest, _u=Depends(get_current_user)):
    """
    Create a parent dim_brand entry for a parent_missing source brand.
    Reads source_parent_uid / source_parent_name from dim_brand_source for the given child row.
    Does NOT bind the child source brand — only inserts the parent into dim_brand.
    After creation the child row's computed_status transitions from parent_missing to ready_to_create.
    """
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT source_parent_uid, source_parent_name"
            " FROM dim_brand_source WHERE source_id = %s AND source_brand_id = %s",
            (body.source_id, body.source_brand_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Source brand not found")

        parent_uid  = (row[0] or "").strip()
        parent_name = (row[1] or "").strip()

        if not parent_uid:
            raise HTTPException(400, "Source brand has no source_parent_uid")
        if not parent_name:
            raise HTTPException(400, "Source brand has no source_parent_name")

        cur.execute("SELECT id, brand_name FROM dim_brand WHERE brand_uid = %s", (parent_uid,))
        dup = cur.fetchone()
        if dup:
            raise HTTPException(
                409,
                f"brand_uid «{parent_uid}» вже існує (id={dup[0]}, назва=«{dup[1]}»)",
            )

        cur.execute(
            "SELECT id FROM dim_brand WHERE LOWER(TRIM(brand_name)) = %s",
            (parent_name.lower(),),
        )
        dup_name = cur.fetchone()
        if dup_name:
            raise HTTPException(
                409,
                f"Бренд з назвою «{parent_name}» вже існує (id={dup_name[0]})",
            )

        cur.execute(
            """INSERT INTO dim_brand (brand_uid, brand_name, brand_group, is_active)
               VALUES (%s, %s, %s, TRUE)
               RETURNING id""",
            (parent_uid, parent_name, (body.brand_group or "").strip() or None),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"ok": True, "master_brand_id": new_id, "brand_uid": parent_uid, "brand_name": parent_name}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/auto-bind")
def auto_bind(body: AutoBindRequest, _u=Depends(get_current_user)):
    """
    Auto-bind pending source brands where source_brand_id exactly matches dim_brand.brand_uid.
    Never overwrites mapped or rejected rows.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        source_cond  = "AND dbs.source_id = %s" if body.source_id else ""
        source_param = [body.source_id] if body.source_id else []

        cur.execute(
            f"""WITH candidates AS (
                    SELECT dbs.source_id, dbs.source_brand_id, b.id AS master_brand_id
                    FROM dim_brand_source dbs
                    JOIN dim_brand b
                      ON b.brand_uid = dbs.source_brand_id
                     AND b.is_active = TRUE
                    LEFT JOIN brand_source_mapping bsm
                           ON bsm.source_id = dbs.source_id
                          AND bsm.source_brand_id = dbs.source_brand_id
                    WHERE (bsm.mapping_status = 'pending' OR bsm.mapping_status IS NULL)
                    {source_cond}
                )
                INSERT INTO brand_source_mapping
                    (source_id, source_brand_id, master_brand_id,
                     mapping_status, confidence, mapped_by, updated_at)
                SELECT source_id, source_brand_id, master_brand_id, 'auto', 95, %s, NOW()
                FROM candidates
                ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                    master_brand_id = EXCLUDED.master_brand_id,
                    mapping_status  = 'auto',
                    confidence      = 95,
                    mapped_by       = EXCLUDED.mapped_by,
                    updated_at      = NOW()
                WHERE brand_source_mapping.mapping_status IN ('pending')
                   OR brand_source_mapping.mapping_status IS NULL""",
            source_param + [_u["id"]],
        )
        bound = cur.rowcount
        conn.commit()
        return {"ok": True, "auto_bound": bound}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


# ── Duplicate check ───────────────────────────────────────────────────────────

@router.get("/duplicate-check")
def duplicate_check(
    brand_uid:  Optional[str] = None,
    brand_name: Optional[str] = None,
    _u=Depends(get_current_user),
):
    """Check if a brand_uid or brand_name already exists in dim_brand."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        uid_exists  = False
        name_exists = False
        matches: list = []

        if brand_uid and brand_uid.strip():
            cur.execute(
                "SELECT id, brand_uid, brand_name FROM dim_brand WHERE brand_uid = %s",
                (brand_uid.strip(),),
            )
            for r in cur.fetchall():
                uid_exists = True
                matches.append({"id": r[0], "brand_uid": r[1], "brand_name": r[2]})

        if brand_name and brand_name.strip():
            cur.execute(
                "SELECT id, brand_uid, brand_name FROM dim_brand WHERE LOWER(TRIM(brand_name)) = %s",
                (brand_name.strip().lower(),),
            )
            for r in cur.fetchall():
                name_exists = True
                entry = {"id": r[0], "brand_uid": r[1], "brand_name": r[2]}
                if entry not in matches:
                    matches.append(entry)

        return {"uid_exists": uid_exists, "name_exists": name_exists, "matches": matches}
    finally:
        cur.close()
        conn.close()


# ── Create and bind ───────────────────────────────────────────────────────────

@router.post("/create-and-bind")
def create_and_bind(body: CreateAndBindRequest, _u=Depends(get_current_user)):
    """
    Create a new master brand in dim_brand and immediately bind the source brand to it.
    Rejects if brand_uid or normalized brand_name already exists.
    """
    _ensure_brand_columns()

    uid  = (body.brand_uid  or "").strip() or None
    name = (body.brand_name or "").strip()
    if not name:
        raise HTTPException(400, "brand_name обов'язкова")

    conn = get_connection()
    cur  = conn.cursor()
    try:
        # Verify source row exists
        cur.execute(
            "SELECT 1 FROM dim_brand_source WHERE source_id = %s AND source_brand_id = %s",
            (body.source_id, body.source_brand_id),
        )
        if not cur.fetchone():
            raise HTTPException(
                404,
                f"Source brand '{body.source_brand_id}' not found in source {body.source_id}",
            )

        # Check uid duplicate
        if uid:
            cur.execute(
                "SELECT id, brand_name FROM dim_brand WHERE brand_uid = %s",
                (uid,),
            )
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    409,
                    f"brand_uid «{uid}» вже існує (id={existing[0]}, назва=«{existing[1]}»)",
                )

        # Check name duplicate
        cur.execute(
            "SELECT id, brand_uid FROM dim_brand WHERE LOWER(TRIM(brand_name)) = %s",
            (name.lower(),),
        )
        existing_name = cur.fetchone()
        if existing_name:
            raise HTTPException(
                409,
                f"Бренд з назвою «{name}» вже існує (id={existing_name[0]})",
            )

        # Insert new master brand
        cur.execute(
            """INSERT INTO dim_brand
                   (brand_uid, brand_name, brand_group, parent_brand_uid, parent_brand_name, is_active)
               VALUES (%s, %s, %s, %s, %s, TRUE)
               RETURNING id""",
            (
                uid,
                name,
                (body.brand_group       or "").strip() or None,
                (body.parent_brand_uid  or "").strip() or None,
                (body.parent_brand_name or "").strip() or None,
            ),
        )
        new_id = cur.fetchone()[0]

        # Bind source brand → new master (overwrites any prior status)
        cur.execute(
            """INSERT INTO brand_source_mapping
                   (source_id, source_brand_id, master_brand_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
               ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                   master_brand_id = EXCLUDED.master_brand_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_brand_id, new_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True, "master_brand_id": new_id, "brand_uid": uid, "brand_name": name}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


# ── Bulk fill preview ─────────────────────────────────────────────────────────

@router.post("/bulk-fill-preview")
def bulk_fill_preview(body: BulkFillRequest, _u=Depends(get_current_user)):
    """Return counts of rows that would be affected by bulk fill, without applying."""
    _ensure_brand_columns()
    if body.field not in _ALLOWED_FIELDS:
        return {"status": "error", "message": f"Поле '{body.field}' не дозволено для bulk-fill"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)
        cur.execute(
            f"""SELECT
                    COUNT(DISTINCT bsm.master_brand_id) FILTER (WHERE {_IS_MAPPED}),
                    COUNT(*)                            FILTER (WHERE {_IS_PENDING})
                {_BULK_JOIN}
                WHERE {where_sql}""",
            params,
        )
        row = cur.fetchone()
        affected_master_count = int(row[0])
        affected_source_count = int(row[1])

        warnings = []
        if affected_master_count > 500:
            warnings.append(f"Буде оновлено {affected_master_count} master-брендів — велика операція.")
        if affected_master_count == 0 and affected_source_count == 0:
            warnings.append("Жодного рядка не знайдено за поточними фільтрами.")

        return {
            "status":                "ok",
            "affected_master_count": affected_master_count,
            "affected_source_count": affected_source_count,
            "total_affected_count":  affected_master_count + affected_source_count,
            "field_label":           _FIELD_LABELS[body.field],
            "value":                 body.value,
            "warnings":              warnings,
        }
    finally:
        cur.close()
        conn.close()


# ── Bulk fill apply ───────────────────────────────────────────────────────────

@router.post("/bulk-fill")
def bulk_fill(body: BulkFillRequest, _u=Depends(get_current_user)):
    """
    Apply a field=value to all rows matching the current filters.
    Mapped rows: update dim_brand.
    Pending rows: write default to dim_brand_source.
    Rejected rows: never touched.
    """
    _ensure_brand_columns()
    if not body.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if body.field not in _ALLOWED_FIELDS:
        return {"status": "error", "message": f"Поле '{body.field}' не дозволено для bulk-fill"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)
        db_field  = _ALLOWED_FIELDS[body.field]

        # 1. Update dim_brand for mapped/auto rows
        cur.execute(
            f"SELECT DISTINCT bsm.master_brand_id {_BULK_JOIN} WHERE {where_sql} AND {_IS_MAPPED}",
            params,
        )
        master_ids = [r[0] for r in cur.fetchall()]
        updated_masters = 0
        if master_ids:
            cur.execute(
                f"UPDATE dim_brand SET {db_field} = %s WHERE id = ANY(%s)",
                (body.value, master_ids),
            )
            updated_masters = cur.rowcount

        # 2. Write default value to pending dim_brand_source rows
        staging_col = _STAGING_DEFAULT_FIELDS[body.field]
        cur.execute(
            f"""UPDATE dim_brand_source SET {staging_col} = %s
                WHERE (source_id, source_brand_id) IN (
                    SELECT dbs.source_id, dbs.source_brand_id
                    {_BULK_JOIN}
                    WHERE {where_sql} AND {_IS_PENDING}
                )""",
            [body.value] + params,
        )
        updated_staging = cur.rowcount

        conn.commit()
        return {
            "status":          "ok",
            "updated_masters": updated_masters,
            "updated_staging": updated_staging,
            "field_label":     _FIELD_LABELS[body.field],
            "value":           body.value,
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── Bulk create preview ───────────────────────────────────────────────────────

@router.post("/bulk-create-preview")
def bulk_create_preview(body: BulkCreateRequest, _u=Depends(get_current_user)):
    """
    Validate pending rows in current filter and report eligibility.
    Eligible = uid + name + group all present AND no duplicate in dim_brand.
    """
    _ensure_brand_columns()
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)

        # Total pending
        cur.execute(f"SELECT COUNT(*) {_BULK_JOIN} WHERE {where_sql} AND {_IS_PENDING}", params)
        total_pending = int(cur.fetchone()[0])

        # Fetch all pending rows with effective values
        cur.execute(
            f"""SELECT
                    dbs.source_brand_id,
                    dbs.source_brand_name,
                    COALESCE(NULLIF(dbs.default_brand_name,  ''), NULLIF(dbs.source_brand_name,  '')) AS eff_name,
                    COALESCE(NULLIF(dbs.default_brand_group, ''), NULLIF(dbs.source_brand_group, '')) AS eff_group
                {_BULK_JOIN}
                WHERE {where_sql} AND {_IS_PENDING}
                ORDER BY dbs.source_brand_name""",
            params,
        )
        pending_rows = cur.fetchall()

        # Classify each row
        missing_uid_rows:  list = []
        missing_name_rows: list = []
        missing_group_rows: list = []
        candidate_rows:    list = []

        for source_brand_id, source_brand_name, eff_name, eff_group in pending_rows:
            uid   = (source_brand_id or "").strip()
            name  = (eff_name  or "").strip()
            group = (eff_group or "").strip()
            entry = {
                "source_brand_id":   source_brand_id,
                "source_brand_name": source_brand_name,
                "eff_group":         eff_group,
            }
            has_problem = False
            if not uid:
                missing_uid_rows.append({**entry, "problem": "Немає UID"})
                has_problem = True
            if not name:
                missing_name_rows.append({**entry, "problem": "Немає назви"})
                has_problem = True
            if not group:
                missing_group_rows.append({**entry, "problem": "Немає групи"})
                has_problem = True
            if not has_problem:
                candidate_rows.append((uid, name, group, source_brand_name))

        # Batch-check duplicates for field-valid candidates
        uid_set  = {r[0] for r in candidate_rows}
        name_set = {r[1].lower() for r in candidate_rows}

        existing_uids:  set = set()
        existing_names: set = set()
        if uid_set:
            cur.execute("SELECT brand_uid FROM dim_brand WHERE brand_uid = ANY(%s)", (list(uid_set),))
            existing_uids = {r[0] for r in cur.fetchall()}
        if name_set:
            cur.execute(
                "SELECT LOWER(TRIM(brand_name)) FROM dim_brand"
                " WHERE LOWER(TRIM(brand_name)) = ANY(%s)",
                (list(name_set),),
            )
            existing_names = {r[0] for r in cur.fetchall()}

        will_create_rows: list = []
        dup_uid_rows:     list = []
        dup_name_rows:    list = []
        seen_uids:  set = set()
        seen_names: set = set()

        for uid, name, group, source_name in candidate_rows:
            if uid in existing_uids or uid in seen_uids:
                dup_uid_rows.append({
                    "source_brand_id": uid, "source_brand_name": source_name,
                    "eff_group": group, "problem": "UID вже існує в dim_brand",
                })
            elif name.lower() in existing_names or name.lower() in seen_names:
                dup_name_rows.append({
                    "source_brand_id": uid, "source_brand_name": source_name,
                    "eff_group": group, "problem": "Назва вже існує в dim_brand",
                })
            else:
                will_create_rows.append({
                    "source_brand_id": uid, "source_brand_name": name, "eff_group": group,
                })
                seen_uids.add(uid)
                seen_names.add(name.lower())

        all_missing = (missing_uid_rows + missing_name_rows + missing_group_rows)[:10]
        all_dups    = (dup_uid_rows + dup_name_rows)[:10]

        return {
            "status":                "ok",
            "total_pending":         total_pending,
            "will_create":           len(will_create_rows),
            "skipped_existing_uid":  len(dup_uid_rows),
            "skipped_existing_name": len(dup_name_rows),
            "missing_uid":           len(missing_uid_rows),
            "missing_name":          len(missing_name_rows),
            "missing_group":         len(missing_group_rows),
            "examples": {
                "will_create": will_create_rows[:10],
                "missing":     all_missing,
                "duplicates":  all_dups,
            },
            "can_apply": len(will_create_rows) > 0,
        }
    finally:
        cur.close()
        conn.close()


# ── Bulk create apply ─────────────────────────────────────────────────────────

@router.post("/bulk-create")
def bulk_create(body: BulkCreateRequest, _u=Depends(get_current_user)):
    """
    Create dim_brand entries for pending rows where uid + name + group are all present.
    Skips duplicates.  Never touches rejected or mapped/auto rows.
    """
    _ensure_brand_columns()
    if not body.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Спочатку звузьте вибірку фільтром або пошуком."}
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)

        cur.execute(
            f"""SELECT
                    dbs.source_id,
                    dbs.source_brand_id,
                    COALESCE(NULLIF(dbs.default_brand_name,        ''), NULLIF(dbs.source_brand_name,  '')) AS eff_name,
                    COALESCE(NULLIF(dbs.default_brand_group,       ''), NULLIF(dbs.source_brand_group, '')) AS eff_group,
                    COALESCE(NULLIF(dbs.default_parent_brand_uid,  ''), NULLIF(dbs.source_parent_uid,  '')) AS eff_parent_uid,
                    COALESCE(NULLIF(dbs.default_parent_brand_name, ''), NULLIF(dbs.source_parent_name, '')) AS eff_parent_name
                {_BULK_JOIN}
                WHERE {where_sql} AND {_IS_PENDING}
                ORDER BY dbs.source_brand_name""",
            params,
        )
        candidates = cur.fetchall()

        # Keep only rows where uid + name + group are all non-empty
        eligible = [
            r for r in candidates
            if (r[1] or "").strip()   # source_brand_id → brand_uid
            and (r[2] or "").strip()  # eff_name
            and (r[3] or "").strip()  # eff_group
        ]

        if not eligible:
            conn.commit()
            return {"status": "ok", "created": 0, "bound": 0, "skipped": len(candidates)}

        # Batch-check duplicates
        uid_set  = {r[1].strip() for r in eligible}
        name_set = {r[2].strip().lower() for r in eligible}

        if uid_set:
            cur.execute("SELECT brand_uid FROM dim_brand WHERE brand_uid = ANY(%s)", (list(uid_set),))
            existing_uids: set = {r[0] for r in cur.fetchall()}
        else:
            existing_uids = set()

        if name_set:
            cur.execute(
                "SELECT LOWER(TRIM(brand_name)) FROM dim_brand"
                " WHERE LOWER(TRIM(brand_name)) = ANY(%s)",
                (list(name_set),),
            )
            existing_names: set = {r[0] for r in cur.fetchall()}
        else:
            existing_names = set()

        created = bound = skipped = 0

        for source_id, source_brand_id, eff_name, eff_group, eff_parent_uid, eff_parent_name in eligible:
            uid   = source_brand_id.strip()
            name  = eff_name.strip()
            group = eff_group.strip()

            if uid in existing_uids or name.lower() in existing_names:
                skipped += 1
                continue

            cur.execute(
                """INSERT INTO dim_brand
                       (brand_uid, brand_name, brand_group, parent_brand_uid, parent_brand_name, is_active)
                   VALUES (%s, %s, %s, %s, %s, TRUE)
                   RETURNING id""",
                (
                    uid, name, group or None,
                    (eff_parent_uid  or "").strip() or None,
                    (eff_parent_name or "").strip() or None,
                ),
            )
            row = cur.fetchone()
            if not row:
                skipped += 1
                continue
            new_brand_id = row[0]
            created += 1
            existing_uids.add(uid)
            existing_names.add(name.lower())

            cur.execute(
                """INSERT INTO brand_source_mapping
                       (source_id, source_brand_id, master_brand_id,
                        mapping_status, confidence, mapped_by, updated_at)
                   VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
                   ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                       master_brand_id = EXCLUDED.master_brand_id,
                       mapping_status  = 'mapped',
                       confidence      = 100,
                       mapped_by       = EXCLUDED.mapped_by,
                       updated_at      = NOW()
                   WHERE brand_source_mapping.mapping_status IN ('pending')
                      OR brand_source_mapping.mapping_status IS NULL""",
                (source_id, source_brand_id, new_brand_id, _u["id"]),
            )
            bound += 1

        conn.commit()
        return {"status": "ok", "created": created, "bound": bound, "skipped": skipped}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── Create master from mapping (one-click) ────────────────────────────────────

@router.post("/create-master-from-mapping")
def create_master_from_mapping(body: CreateFromMappingRequest, _u=Depends(get_current_user)):
    """
    Create a dim_brand entry for a pending source brand using its saved default/source values.
    Requires uid + name + group to be resolvable; rejects if duplicates exist.
    Immediately binds the source row to the new master brand.
    """
    _ensure_brand_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        # Fetch staging row with effective values
        cur.execute(
            """SELECT
                   dbs.source_brand_id,
                   COALESCE(NULLIF(dbs.default_brand_name,        ''), NULLIF(dbs.source_brand_name,  '')) AS eff_name,
                   COALESCE(NULLIF(dbs.default_brand_group,       ''), NULLIF(dbs.source_brand_group, '')) AS eff_group,
                   COALESCE(NULLIF(dbs.default_parent_brand_uid,  ''), NULLIF(dbs.source_parent_uid,  '')) AS eff_parent_uid,
                   COALESCE(NULLIF(dbs.default_parent_brand_name, ''), NULLIF(dbs.source_parent_name, '')) AS eff_parent_name,
                   bsm.mapping_status,
                   bsm.master_brand_id,
                   dbs.source_level,
                   dbs.source_company_name,
                   dbs.source_is_active,
                   dbs.source_brand_ref_id
               FROM dim_brand_source dbs
               LEFT JOIN brand_source_mapping bsm
                      ON bsm.source_id = dbs.source_id
                     AND bsm.source_brand_id = dbs.source_brand_id
               WHERE dbs.source_id = %s AND dbs.source_brand_id = %s""",
            (body.source_id, body.source_brand_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Source brand not found")

        uid, eff_name, eff_group, eff_parent_uid, eff_parent_name, mapping_status, master_brand_id,             dbs_source_level, dbs_source_company, dbs_source_is_active, dbs_source_brand_ref_id = row

        # Guard: must be pending and not already in master
        is_mapped = master_brand_id is not None and mapping_status in ("mapped", "auto")
        if is_mapped:
            raise HTTPException(409, "Бренд вже прив'язаний до майстра")
        if not (uid or "").strip():
            raise HTTPException(400, "Відсутній UID (source_brand_id)")
        if not (eff_name or "").strip():
            raise HTTPException(400, "Відсутня назва бренду")
        if not (eff_group or "").strip():
            raise HTTPException(400, "Відсутня група бренду")

        uid   = uid.strip()
        name  = eff_name.strip()
        group = eff_group.strip()

        # Duplicate check
        cur.execute("SELECT id, brand_name FROM dim_brand WHERE brand_uid = %s", (uid,))
        dup = cur.fetchone()
        if dup:
            raise HTTPException(409, f"brand_uid «{uid}» вже існує (id={dup[0]}, назва=«{dup[1]}»)")

        cur.execute(
            "SELECT id FROM dim_brand WHERE LOWER(TRIM(brand_name)) = %s",
            (name.lower(),),
        )
        dup_name = cur.fetchone()
        if dup_name:
            raise HTTPException(409, f"Бренд з назвою «{name}» вже існує (id={dup_name[0]})")

        # Insert new master brand
        cur.execute(
            """INSERT INTO dim_brand
                   (brand_uid, brand_name, brand_group, parent_brand_uid, parent_brand_name, is_active,
                    source_level, source_company_name, source_is_active, source_brand_ref_id)
               VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s)
               RETURNING id""",
            (
                uid, name, group,
                (eff_parent_uid  or "").strip() or None,
                (eff_parent_name or "").strip() or None,
                dbs_source_level or None,
                dbs_source_company or None,
                dbs_source_is_active or None,
                dbs_source_brand_ref_id or None,
            ),
        )
        new_id = cur.fetchone()[0]

        # Bind source → new master
        cur.execute(
            """INSERT INTO brand_source_mapping
                   (source_id, source_brand_id, master_brand_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
               ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                   master_brand_id = EXCLUDED.master_brand_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_brand_id, new_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True, "master_brand_id": new_id, "brand_uid": uid, "brand_name": name}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


# ── Similar brands lookup ──────────────────────────────────────────────────────

@router.get("/similar-brands")
def get_similar_brands(
    source_brand_name: str,
    limit: int = 10,
    _u=Depends(get_current_user),
):
    """Return top N similar master brands for a given source brand name."""
    _ensure_brand_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, brand_name, brand_group,
                      COALESCE(normalized_name, '') as normalized_name
               FROM dim_brand WHERE is_active = TRUE"""
        )
        masters_raw = cur.fetchall()
        cur.execute(
            """SELECT master_brand_id, COUNT(*) FROM brand_source_mapping
               WHERE master_brand_id IS NOT NULL GROUP BY master_brand_id"""
        )
        sources_count = {r[0]: r[1] for r in cur.fetchall()}
        masters = [
            {
                "id": r[0],
                "brand_name": r[1] or "",
                "brand_group": r[2] or "",
                "normalized_name": r[3] or normalize_brand_name(r[1] or ""),
                "mapped_sources_count": sources_count.get(r[0], 0),
            }
            for r in masters_raw
        ]

        src_norm = normalize_brand_name(source_brand_name)
        scored = []
        for m in masters:
            score = compute_match_score(src_norm, m["normalized_name"])
            if score >= 50:
                scored.append({
                    "master_brand_id":       m["id"],
                    "master_brand_name":     m["brand_name"],
                    "master_brand_group":    m["brand_group"],
                    "match_score":           score,
                    "recommendation":        get_recommendation(score),
                    "mapped_sources_count":  m["mapped_sources_count"],
                })
        scored.sort(key=lambda x: -x["match_score"])
        return scored[:limit]
    finally:
        cur.close()
        conn.close()


# ── Bulk auto-bind ─────────────────────────────────────────────────────────────

class AutoBindPair(BaseModel):
    source_id:       int
    source_brand_id: str
    master_brand_id: int


class BulkAutoBindRequest(BaseModel):
    pairs: List[AutoBindPair]


@router.post("/bulk-auto-bind")
def bulk_auto_bind(body: BulkAutoBindRequest, _u=Depends(get_current_user)):
    """Bind multiple source brands to master brands in one transaction."""
    if not body.pairs:
        return {"bound": 0}
    conn = get_connection()
    cur = conn.cursor()
    try:
        bound = 0
        for pair in body.pairs:
            cur.execute(
                """INSERT INTO brand_source_mapping
                       (source_id, source_brand_id, master_brand_id, mapping_status, confidence, mapped_by)
                   VALUES (%s, %s, %s, 'auto', 100, %s)
                   ON CONFLICT (source_id, source_brand_id) DO UPDATE
                   SET master_brand_id = EXCLUDED.master_brand_id,
                       mapping_status = 'auto',
                       confidence = 100,
                       mapped_by = EXCLUDED.mapped_by,
                       updated_at = NOW()
                   WHERE brand_source_mapping.mapping_status IN ('pending', 'rejected')
                      OR brand_source_mapping.master_brand_id IS NULL""",
                (pair.source_id, pair.source_brand_id, pair.master_brand_id, _u["id"]),
            )
            bound += cur.rowcount or 0
        conn.commit()
        log_action(
            action="brand_bulk_auto_bind",
            user_id=_u["id"],
            user_email=_u.get("email"),
            entity_type="brand_source_mapping",
            menu_key="brand_correspondence",
            new_value={"bound_count": bound, "pairs_count": len(body.pairs)},
        )
        return {"bound": bound}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()
