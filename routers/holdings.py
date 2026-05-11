from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/holdings")


def ensure_holding_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_holding (
            holding_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            holding_name TEXT NOT NULL,
            is_active BOOLEAN DEFAULT true
        )
        """
    )

    conn.commit()
    cur.close()
    conn.close()


@router.get("")
def get_holdings():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT holding_id, holding_name, is_active FROM dim_holding ORDER BY holding_name"
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "holding_id": r[0],
                "holding_name": r[1],
                "is_active": r[2],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_holding(
    holding_name: str = Form(...),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dim_holding (holding_name, is_active) VALUES (%s, true) RETURNING holding_id",
        (holding_name,),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "holding": {
            "holding_id": new_id,
            "holding_name": holding_name,
            "is_active": True,
        },
    }


@router.put("/{old_holding_id}")
def update_holding(
    old_holding_id: int,
    holding_name: str = Form(...),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_holding
        SET holding_name = %s, is_active = %s
        WHERE holding_id = %s
        """,
        (holding_name, is_active_bool, old_holding_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{holding_id}")
def delete_holding(holding_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE dim_holding SET is_active = false WHERE holding_id = %s",
        (holding_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
