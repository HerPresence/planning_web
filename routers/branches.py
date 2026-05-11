from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/branches")


def ensure_branch_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_branch (
            branch_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            branch_name TEXT NOT NULL,
            region_id INTEGER,
            is_active BOOLEAN DEFAULT true
        )
        """
    )

    conn.commit()
    cur.close()
    conn.close()


@router.get("")
def get_branches():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT b.branch_id, b.branch_name, b.region_id, r.region_name, b.is_active
        FROM dim_branch b
        LEFT JOIN dim_region r ON b.region_id = r.region_id
        ORDER BY b.branch_name
        """
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "branch_id": r[0],
                "branch_name": r[1],
                "region_id": r[2],
                "region_name": r[3],
                "is_active": r[4],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_branch(
    branch_name: str = Form(...),
    region_id: int | None = Form(None),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dim_branch (branch_name, region_id, is_active) VALUES (%s, %s, true) RETURNING branch_id",
        (branch_name, region_id),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "branch": {
            "branch_id": new_id,
            "branch_name": branch_name,
            "region_id": region_id,
            "is_active": True,
        },
    }


@router.put("/{old_branch_id}")
def update_branch(
    old_branch_id: int,
    branch_name: str = Form(...),
    region_id: int | None = Form(None),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_branch
        SET branch_name = %s, region_id = %s, is_active = %s
        WHERE branch_id = %s
        """,
        (branch_name, region_id, is_active_bool, old_branch_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{branch_id}")
def delete_branch(branch_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE dim_branch SET is_active = false WHERE branch_id = %s",
        (branch_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
