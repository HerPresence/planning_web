import uuid
from fastapi import APIRouter, Depends, Form
from auth.dependencies import get_current_user
from db import get_connection
from services.rls_service import build_scope_filter

router = APIRouter(prefix="/api/departments")


def ensure_department_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_department (
            department_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            holding_name TEXT,
            organization_name TEXT,
            region_name TEXT,
            branch_name TEXT,
            department_name TEXT,
            is_active BOOLEAN DEFAULT true
        )
        """
    )

    conn.commit()
    cur.close()
    conn.close()


@router.get("")
def get_departments(user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()

    scope_sql, scope_params = ("", [])
    if not user["is_admin"]:
        scope_sql, scope_params = build_scope_filter(user["id"])

    base_where = "COALESCE(is_deleted, FALSE) = FALSE"
    where = f"WHERE {base_where}" + (f" AND {scope_sql}" if scope_sql else "")
    cur.execute(
        f"""
        SELECT department_id, holding_name, organization_name, region_name, branch_name, department_name, is_active
        FROM dim_department
        {where}
        ORDER BY holding_name, organization_name, region_name, branch_name, department_name
        """,
        scope_params
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "department_id": r[0],
                "holding_name": r[1],
                "organization_name": r[2],
                "region_name": r[3],
                "branch_name": r[4],
                "department_name": r[5],
                "is_active": r[6],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_department(
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_name: str = Form("")
):
    conn = get_connection()
    cur = conn.cursor()

    new_dept_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO dim_department
        (department_id, holding_name, organization_name, region_name, branch_name, department_name, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, true)
        RETURNING department_id
        """,
        (new_dept_id, holding_name, organization_name, region_name, branch_name, department_name),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "department": {
            "department_id": new_id,
            "holding_name": holding_name,
            "organization_name": organization_name,
            "region_name": region_name,
            "branch_name": branch_name,
            "department_name": department_name,
            "is_active": True,
        },
    }


@router.put("/{old_department_id}")
def update_department(
    old_department_id: str,
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_name: str = Form(""),
    is_active: bool = Form(True)
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE dim_department
        SET holding_name = %s,
            organization_name = %s,
            region_name = %s,
            branch_name = %s,
            department_name = %s,
            is_active = %s
        WHERE department_id = %s
        """,
        (
            holding_name,
            organization_name,
            region_name,
            branch_name,
            department_name,
            is_active,
            old_department_id,
        ),
    )

    conn.commit()
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
