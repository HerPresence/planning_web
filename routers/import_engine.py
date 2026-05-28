"""
Universal Import Engine -- HTTP endpoints.
New routes: /api/import-engine/...
Existing /api/pnl-import/... routes are untouched.
"""

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from db import get_connection
from services.import_engine import (
    IMPORT_TYPES,
    DEFAULT_FIELDS_BY_TYPE,
    SALES_FACT_DEFAULT_FIELDS,
    DEPARTMENTS_DEFAULT_FIELDS,
    BRANDS_DEFAULT_FIELDS,
    bulk_update_staging_sales_fact,
    commit_sales_fact,
    create_batch,
    delete_batch as svc_delete_batch,
    get_batch,
    get_batches,
    get_fact_turnover as svc_get_fact_turnover,
    get_field_mapping,
    get_staging_preview,
    load_sales_fact_to_staging,
    save_field_mapping,
    update_batch,
    universal_load_to_staging,
    universal_get_staging_preview,
    universal_commit,
    rollback_batch as svc_rollback_batch,
)
from routers.pnl_import import _read_ssas_dax, _read_sql_odbc

router = APIRouter(prefix="/api/import-engine")


# ---- Request models ----------------------------------------------------------

class FieldMappingItem(BaseModel):
    source_field:   str
    target_field:   str
    required:       bool = False
    transform_rule: str  = ""


class FieldMappingBody(BaseModel):
    mappings: List[FieldMappingItem]


class StagingBulkUpdateFilters(BaseModel):
    status:              Optional[str] = None
    period_from:         Optional[str] = None
    period_to:           Optional[str] = None
    department_name:     Optional[str] = None
    product_group_name:  Optional[str] = None
    search:              Optional[str] = None


class StagingBulkUpdateBody(BaseModel):
    filters:      StagingBulkUpdateFilters = StagingBulkUpdateFilters()
    target_field: str   # 'department' or 'brand'
    master_id:    str   # TEXT for departments (e.g. "OV_LVIV_002"), numeric string for brands


# ---- Import Types ------------------------------------------------------------

@router.get("/types")
def get_import_types(_u=Depends(get_current_user)):
    return IMPORT_TYPES


# ---- Sources ----------------------------------------------------------------

@router.get("/sources")
def get_engine_sources(_u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, source_name, source_type, import_type_code, is_active
               FROM import_sources WHERE is_active = TRUE ORDER BY source_name"""
        )
        return [
            {"id": r[0], "source_name": r[1], "source_type": r[2],
             "import_type_code": r[3], "is_active": r[4]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


@router.patch("/sources/{source_id}/type")
def set_source_import_type(source_id: int, import_type_code: Optional[str] = None,
                            _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE import_sources SET import_type_code = %s WHERE id = %s",
            (import_type_code or None, source_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---- Field mapping ----------------------------------------------------------

@router.get("/field-mapping/{source_id}")
def get_mapping(source_id: int, _u=Depends(get_current_user)):
    mapping = get_field_mapping(source_id)
    if not mapping:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT import_type_code FROM import_sources WHERE id = %s", (source_id,)
            )
            row = cur.fetchone()
            if row and row[0]:
                defaults = DEFAULT_FIELDS_BY_TYPE.get(row[0])
                if defaults:
                    return defaults
        finally:
            cur.close()
            conn.close()
    return mapping


@router.put("/field-mapping/{source_id}")
def save_mapping(source_id: int, body: FieldMappingBody, _u=Depends(get_current_user)):
    save_field_mapping(source_id, [m.dict() for m in body.mappings])
    return {"ok": True, "count": len(body.mappings)}


# ---- Preview ----------------------------------------------------------------

@router.post("/preview/{source_id}")
def preview_source(source_id: int, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT source_type FROM import_sources WHERE id = %s AND is_active = TRUE",
            (source_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        raise HTTPException(404, "Source not found")

    source_type = row[0] or "olap_ssas_dax"
    try:
        if source_type in ("olap_ssas_dax", "olap_sql"):
            rows, _ = _read_ssas_dax(source_id)
        elif source_type == "sql_odbc":
            rows, _ = _read_sql_odbc(source_id)
        else:
            raise HTTPException(400, f"Source type '{source_type}' not supported")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))

    if not rows:
        return {"columns": [], "preview_rows": [], "total_rows": 0}

    return {
        "columns": list(rows[0].keys()),
        "preview_rows": rows[:20],
        "total_rows": len(rows),
    }


# ---- Load to staging --------------------------------------------------------

@router.post("/load/{source_id}")
def load_to_staging(
    source_id: int,
    period_from: Optional[str] = None,
    period_to:   Optional[str] = None,
    period_field: str = "period_month",
    replace_mode: str = "replace_by_period",
    _u=Depends(get_current_user),
):
    """
    Fetch data from source, filter by period_from..period_to, write to staging_sales_fact.
    period_from and period_to are required for sales_fact to avoid loading full history.
    Returns batch_id and staging summary.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT source_type, import_type_code FROM import_sources WHERE id = %s AND is_active = TRUE",
            (source_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        raise HTTPException(404, "Source not found")

    source_type = row[0] or "olap_ssas_dax"
    import_type_code = row[1] or "sales_fact"

    SUPPORTED_TYPES = {"sales_fact", "departments", "brands", "articles"}
    if import_type_code not in SUPPORTED_TYPES:
        raise HTTPException(
            400,
            f"Type '{import_type_code}' not supported here. Use /api/pnl-import/ for PnL."
        )

    # Parse period dates (required for sales_fact, optional for others)
    pf = _parse_date_str(period_from) if period_from else None
    pt = _parse_date_str(period_to)   if period_to   else None

    if pf and pt and pf > pt:
        raise HTTPException(400, "period_from must be <= period_to")

    # Fetch raw data from source
    try:
        if source_type in ("olap_ssas_dax", "olap_sql"):
            raw_rows, _ = _read_ssas_dax(source_id)
        elif source_type == "sql_odbc":
            raw_rows, _ = _read_sql_odbc(source_id)
        else:
            raise HTTPException(400, f"Source type '{source_type}' not supported")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Error reading source: {exc}")

    field_mapping = get_field_mapping(source_id)
    if not field_mapping:
        field_mapping = DEFAULT_FIELDS_BY_TYPE.get(import_type_code) or []
    elif import_type_code == "brands":
        # Patch any new default fields missing from existing saved mappings
        covered = {m["target_field"] for m in field_mapping}
        missing = [df for df in BRANDS_DEFAULT_FIELDS if df["target_field"] not in covered]
        if missing:
            field_mapping = list(field_mapping) + missing

    batch_id = create_batch(
        source_id, import_type_code,
        created_by=_u["id"],
        period_from=pf, period_to=pt,
        period_field=period_field,
        replace_mode=replace_mode,
    )

    try:
        rows_loaded, rows_failed, rows_filtered_out, rows_valid, rows_invalid = (
            universal_load_to_staging(
                batch_id, raw_rows, field_mapping, import_type_code,
                period_from=pf, period_to=pt,
            )
        )
        update_batch(
            batch_id,
            status="loaded",
            finished_at=datetime.now(),
            rows_total=len(raw_rows),
            rows_filtered_out=rows_filtered_out,
            rows_loaded=rows_loaded,
            rows_failed=rows_failed,
            rows_valid=rows_valid,
            rows_invalid=rows_invalid,
        )
    except Exception as exc:
        update_batch(batch_id, status="failed", error_message=str(exc)[:500],
                     finished_at=datetime.now())
        raise HTTPException(500, f"Error writing to staging: {exc}")

    preview_limit = 5000 if import_type_code in ("departments", "brands") else 500
    staging = universal_get_staging_preview(batch_id, import_type_code, limit=preview_limit)
    return {
        "batch_id": batch_id,
        "rows_total": len(raw_rows),
        "rows_filtered_out": rows_filtered_out,
        "rows_loaded": rows_loaded,
        "rows_failed": rows_failed,
        "rows_valid": rows_valid,
        "rows_invalid": rows_invalid,
        "period_from": str(pf) if pf else None,
        "period_to":   str(pt) if pt else None,
        "staging": staging,
    }


# ---- Staging preview --------------------------------------------------------

@router.get("/staging/{batch_id}")
def staging_preview(batch_id: int, status_filter: Optional[str] = None,
                    limit: int = 500, _u=Depends(get_current_user)):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return universal_get_staging_preview(
        batch_id, batch["import_type_code"], limit=limit, status_filter=status_filter
    )


# ---- Staging bulk-update (department / brand mapping) -----------------------

@router.post("/staging/{batch_id}/bulk-update")
def staging_bulk_update(
    batch_id: int,
    body: StagingBulkUpdateBody,
    _u=Depends(get_current_user),
):
    """
    Bulk-assign master department or brand to filtered staging rows,
    then re-validate and return refreshed staging preview.
    """
    if body.target_field not in ("department", "brand"):
        raise HTTPException(400, "target_field must be 'department' or 'brand'")

    filters = {k: v for k, v in body.filters.dict().items() if v is not None}
    try:
        result = bulk_update_staging_sales_fact(
            batch_id=batch_id,
            filters=filters,
            target_field=body.target_field,
            master_id=body.master_id,
            updated_by=_u["id"],
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

    staging = get_staging_preview(batch_id)
    return {**result, "staging": staging}


# ---- Commit -----------------------------------------------------------------

@router.post("/commit/{batch_id}")
def commit_batch_endpoint(batch_id: int, _u=Depends(get_current_user)):
    """
    Commit valid staging rows to fact_turnover.
    Uses period_from/period_to stored in the batch for targeted DELETE before INSERT.
    """
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    if batch["status"] == "committed":
        raise HTTPException(400, "Batch already committed")

    import_type_code = batch["import_type_code"]
    SUPPORTED_COMMIT = {"sales_fact", "departments", "brands", "articles"}
    if import_type_code not in SUPPORTED_COMMIT:
        raise HTTPException(400, f"Type '{import_type_code}' commit not supported here")

    pf = _parse_date_str(batch["period_from"]) if batch.get("period_from") else None
    pt = _parse_date_str(batch["period_to"])   if batch.get("period_to")   else None
    source_id = batch["source_id"]

    update_batch(batch_id, status="committing")
    try:
        result = universal_commit(
            batch_id, import_type_code, source_id=source_id,
            period_from=pf, period_to=pt,
        )
        rows_to_target = (
            result.get("committed")
            or result.get("upserted")
            or (result.get("inserted", 0) + result.get("updated", 0))
            or 0
        )
        update_batch(
            batch_id,
            status="committed",
            finished_at=datetime.now(),
            rows_loaded_to_target=rows_to_target,
        )
    except Exception as exc:
        update_batch(batch_id, status="failed", error_message=str(exc)[:500],
                     finished_at=datetime.now())
        raise HTTPException(500, f"Error committing: {exc}")

    return {
        "ok": True,
        "batch_id": batch_id,
        "import_type_code": import_type_code,
        **result,
        "period_from": str(pf) if pf else None,
        "period_to":   str(pt) if pt else None,
    }


# ---- Batch management -------------------------------------------------------

@router.get("/batches")
def list_batches(limit: int = 50, _u=Depends(get_current_user)):
    return get_batches(limit=limit)


@router.get("/batches/{batch_id}")
def get_batch_detail(batch_id: int, _u=Depends(get_current_user)):
    b = get_batch(batch_id)
    if not b:
        raise HTTPException(404, "Batch not found")
    return b


@router.delete("/batches/{batch_id}")
def delete_batch_endpoint(batch_id: int, delete_fact: bool = False,
                          _u=Depends(get_current_user)):
    """
    Delete batch + staging rows.
    If delete_fact=True and batch was committed, also removes data from fact_turnover
    using the batch's period_from..period_to for the given source.
    """
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    pf = _parse_date_str(batch["period_from"]) if batch.get("period_from") else None
    pt = _parse_date_str(batch["period_to"])   if batch.get("period_to")   else None

    try:
        result = svc_delete_batch(
            batch_id=batch_id,
            source_id=batch["source_id"],
            delete_fact=delete_fact,
            period_from=pf,
            period_to=pt,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return result


# ---- Rollback ---------------------------------------------------------------

@router.post("/batches/{batch_id}/rollback")
def rollback_batch_endpoint(batch_id: int, _u=Depends(get_current_user)):
    """Delete staging rows; for sales_fact also removes from target table."""
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    try:
        result = svc_rollback_batch(batch_id)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"ok": True, **result}


# ---- Fact Turnover view -----------------------------------------------------

@router.get("/fact-turnover")
def get_fact_turnover(
    period_from:       Optional[str] = None,
    period_to:         Optional[str] = None,
    source_id:         Optional[int] = None,
    limit:             int = 5000,
    _u=Depends(get_current_user),
):
    return svc_get_fact_turnover(
        period_from=period_from,
        period_to=period_to,
        source_id=source_id,
        limit=limit,
    )


# ---- Helpers ----------------------------------------------------------------

def _parse_date_str(s) -> Optional[date]:
    if not s:
        return None
    import re
    s = str(s).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    return None
