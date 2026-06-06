import uuid
from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
from auth.dependencies import get_current_user
from db import get_connection
from services.rls_service import build_scope_filter

router = APIRouter(prefix="/api/departments")


def ensure_department_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dim_department (
                department_id     TEXT PRIMARY KEY,
                holding_name      TEXT,
                organization_name TEXT,
                region_name       TEXT,
                branch_name       TEXT,
                department_name   TEXT,
                is_active         BOOLEAN DEFAULT true
            )
            """
        )
        for col, typ in [
            ("parent_department_id",   "TEXT"),
            ("parent_department_name", "TEXT"),
            ("is_deleted",             "BOOLEAN DEFAULT FALSE"),
            ("deleted_at",             "TIMESTAMP"),
            ("holding_id",             "INTEGER"),
            ("organization_id",        "INTEGER"),
            ("region_id",              "INTEGER"),
            ("branch_id",              "INTEGER"),
        ]:
            cur.execute(
                f"ALTER TABLE dim_department ADD COLUMN IF NOT EXISTS {col} {typ}"
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


@router.get("")
def get_departments(user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()

    scope_sql, scope_params = ("", [])
    if not user["is_admin"]:
        scope_sql, scope_params = build_scope_filter(user["id"], table_prefix="d.")

    base_where = "COALESCE(d.is_deleted, FALSE) = FALSE"
    where = f"WHERE {base_where}" + (f" AND {scope_sql}" if scope_sql else "")
    cur.execute(
        f"""
        SELECT d.department_id, d.holding_name, d.organization_name, d.region_name, d.branch_name,
               d.department_name, d.is_active, d.parent_department_id, d.parent_department_name,
               COALESCE(cc.child_count, 0)::int AS child_count,
               (d.parent_department_id IS NULL OR p.department_id IS NOT NULL) AS parent_exists
        FROM dim_department d
        LEFT JOIN (
            SELECT parent_department_id, COUNT(*)::int AS child_count
            FROM dim_department
            WHERE COALESCE(is_deleted, FALSE) = FALSE
            GROUP BY parent_department_id
        ) cc ON cc.parent_department_id = d.department_id
        LEFT JOIN dim_department p
               ON p.department_id = d.parent_department_id
              AND COALESCE(p.is_deleted, FALSE) = FALSE
        {where}
        ORDER BY
            COALESCE(d.parent_department_id, d.department_id),
            CASE WHEN d.parent_department_id IS NULL THEN 0 ELSE 1 END,
            d.department_name
        """,
        scope_params
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        child_count   = int(r[9] or 0)
        parent_exists = bool(r[10]) if r[10] is not None else True
        result.append(
            {
                "department_id":          r[0],
                "holding_name":           r[1],
                "organization_name":      r[2],
                "region_name":            r[3],
                "branch_name":            r[4],
                "department_name":        r[5],
                "is_active":              r[6],
                "parent_department_id":   r[7],
                "parent_department_name": r[8],
                "child_count":            child_count,
                "has_children":           child_count > 0,
                "hierarchy_level":        0 if not r[7] else 1,
                "parent_exists":          parent_exists,
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_department(
    holding_name:           str = Form(""),
    organization_name:      str = Form(""),
    region_name:            str = Form(""),
    branch_name:            str = Form(""),
    department_name:        str = Form(""),
    parent_department_id:   str = Form(""),
    parent_department_name: str = Form(""),
):
    parent_id_s   = parent_department_id.strip()   or None
    parent_name_s = parent_department_name.strip() or None

    conn = get_connection()
    cur = conn.cursor()
    try:
        if parent_id_s:
            cur.execute(
                "SELECT 1 FROM dim_department WHERE department_id = %s AND COALESCE(is_deleted, false) = false",
                (parent_id_s,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Parent підрозділ '{parent_id_s}' не знайдено")

        new_dept_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO dim_department
                (department_id, holding_name, organization_name, region_name, branch_name,
                 department_name, is_active, parent_department_id, parent_department_name)
            VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s)
            RETURNING department_id
            """,
            (new_dept_id, holding_name, organization_name, region_name, branch_name,
             department_name, parent_id_s, parent_name_s),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return {
        "status": "ok",
        "department": {
            "department_id":          new_id,
            "holding_name":           holding_name,
            "organization_name":      organization_name,
            "region_name":            region_name,
            "branch_name":            branch_name,
            "department_name":        department_name,
            "is_active":              True,
            "parent_department_id":   parent_id_s,
            "parent_department_name": parent_name_s,
        },
    }


@router.put("/{old_department_id}")
def update_department(
    old_department_id:      str,
    holding_name:           str  = Form(""),
    organization_name:      str  = Form(""),
    region_name:            str  = Form(""),
    branch_name:            str  = Form(""),
    department_name:        str  = Form(""),
    is_active:              bool = Form(True),
    parent_department_id:   str  = Form(""),
    parent_department_name: str  = Form(""),
):
    parent_id_s   = parent_department_id.strip()   or None
    parent_name_s = parent_department_name.strip() or None

    conn = get_connection()
    cur = conn.cursor()
    try:
        if parent_id_s:
            if parent_id_s == old_department_id:
                raise HTTPException(status_code=400, detail="Підрозділ не може бути власним батьком")
            cur.execute(
                "SELECT 1 FROM dim_department WHERE department_id = %s AND COALESCE(is_deleted, false) = false",
                (parent_id_s,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Parent підрозділ '{parent_id_s}' не знайдено")

        cur.execute(
            """
            UPDATE dim_department
            SET holding_name           = %s,
                organization_name      = %s,
                region_name            = %s,
                branch_name            = %s,
                department_name        = %s,
                is_active              = %s,
                parent_department_id   = %s,
                parent_department_name = %s
            WHERE department_id = %s
            """,
            (holding_name, organization_name, region_name, branch_name, department_name,
             is_active, parent_id_s, parent_name_s, old_department_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return {"status": "ok"}


@router.delete("/{department_id}")
def deactivate_department(department_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE dim_department SET is_active = false, is_deleted = true, deleted_at = NOW() WHERE department_id = %s",
        (department_id,),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok"}


@router.patch("/{department_id}/restore")
def restore_department(department_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE dim_department SET is_active = true, is_deleted = false, deleted_at = NULL WHERE department_id = %s",
        (department_id,),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok"}


# ── Bulk fill attributes ───────────────────────────────────────────────────────

_DEPT_FILL_COLS = {
    "holding_name":      "holding_name",
    "organization_name": "organization_name",
    "region_name":       "region_name",
    "branch_name":       "branch_name",
}


class DeptBulkFillRequest(BaseModel):
    department_ids: List[str] = []
    updates:        Dict[str, Any] = {}


# ── Bulk update filtered departments ──────────────────────────────────────────

_BULK_UPD_ALLOWED = {
    "holding_name",
    "organization_name",
    "region_name",
    "branch_name",
    "parent_department_id",
    "parent_department_name",
    "is_active",
}


class DeptBulkUpdateFilteredRequest(BaseModel):
    department_ids: List[str] = []
    updates:        Dict[str, Any] = {}


@router.post("/bulk-fill")
def bulk_fill_departments(body: DeptBulkFillRequest, _u=Depends(get_current_user)):
    if not body.updates:
        raise HTTPException(400, "Не вибрано жодного поля для оновлення")
    unknown = set(body.updates.keys()) - set(_DEPT_FILL_COLS.keys())
    if unknown:
        raise HTTPException(400, f"Недозволені поля: {', '.join(sorted(unknown))}")
    if not body.department_ids:
        raise HTTPException(400, "Не вибрано жодного підрозділу")

    set_parts, set_params = [], []
    for key, col in _DEPT_FILL_COLS.items():
        if key in body.updates:
            set_parts.append(f"{col} = %s")
            set_params.append(str(body.updates[key]) if body.updates[key] is not None else "")

    placeholders = ",".join(["%s"] * len(body.department_ids))
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE dim_department SET {', '.join(set_parts)} "
            f"WHERE department_id IN ({placeholders})",
            set_params + list(body.department_ids),
        )
        updated = cur.rowcount
        conn.commit()
        return {"updated": updated}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


@router.post("/bulk-update-filtered")
def bulk_update_filtered_departments(body: DeptBulkUpdateFilteredRequest, _u=Depends(get_current_user)):
    if not body.updates:
        raise HTTPException(400, "Не вибрано жодного поля для оновлення")
    unknown = set(body.updates.keys()) - _BULK_UPD_ALLOWED
    if unknown:
        raise HTTPException(400, f"Недозволені поля: {', '.join(sorted(unknown))}")
    if not body.department_ids:
        raise HTTPException(400, "Не передано жодного підрозділу")

    set_parts, set_params = [], []
    for key in _BULK_UPD_ALLOWED:
        if key not in body.updates:
            continue
        val = body.updates[key]
        if key == "is_active":
            set_parts.append("is_active = %s")
            set_params.append(bool(val))
        else:
            set_parts.append(f"{key} = %s")
            set_params.append(str(val) if val is not None else None)

    placeholders = ",".join(["%s"] * len(body.department_ids))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM dim_department"
            f" WHERE department_id IN ({placeholders}) AND COALESCE(is_deleted, FALSE) = FALSE",
            list(body.department_ids),
        )
        matched_rows = int(cur.fetchone()[0])

        cur.execute(
            f"UPDATE dim_department SET {', '.join(set_parts)}"
            f" WHERE department_id IN ({placeholders}) AND COALESCE(is_deleted, FALSE) = FALSE",
            set_params + list(body.department_ids),
        )
        updated_rows = cur.rowcount
        conn.commit()

        if updated_rows == 0:
            return {
                "status":       "warning",
                "message":      "Жоден рядок не було змінено.",
                "matched_rows": matched_rows,
                "updated_rows": 0,
            }

        return {
            "status":       "ok",
            "matched_rows": matched_rows,
            "updated_rows": updated_rows,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
