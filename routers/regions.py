from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/regions")


def ensure_region_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dim_region (
                region_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
                region_name TEXT NOT NULL,
                is_active BOOLEAN DEFAULT true
            )
            """
        )
        cur.execute("SET lock_timeout = '3s'")
        cur.execute("ALTER TABLE dim_region DROP COLUMN IF EXISTS organization_id")
        conn.commit()
    except Exception as exc:
        print(f"[startup] ensure_region_table warning: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


@router.get("")
def get_regions():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT region_id, region_name, is_active FROM dim_region ORDER BY region_name"
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "region_id": r[0],
                "region_name": r[1],
                "is_active": r[2],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_region(
    region_name: str = Form(...),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dim_region (region_name, is_active) VALUES (%s, true) RETURNING region_id",
        (region_name,),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "region": {
            "region_id": new_id,
            "region_name": region_name,
            "is_active": True,
        },
    }


@router.put("/{old_region_id}")
def update_region(
    old_region_id: int,
    region_name: str = Form(...),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_region
        SET region_name = %s, is_active = %s
        WHERE region_id = %s
        """,
        (region_name, is_active_bool, old_region_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{region_id}")
def delete_region(region_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE dim_region SET is_active = false WHERE region_id = %s",
        (region_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
