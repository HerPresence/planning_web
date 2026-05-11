from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/sources")


def ensure_source_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_source (
            source_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            source_name TEXT NOT NULL,
            source_type TEXT,
            is_active BOOLEAN DEFAULT true
        )
        """
    )

    conn.commit()
    cur.close()
    conn.close()


@router.get("")
def get_sources():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT source_id, source_name, source_type, is_active FROM dim_source ORDER BY source_name"
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "source_id": r[0],
                "source_name": r[1],
                "source_type": r[2],
                "is_active": r[3],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_source(
    source_name: str = Form(...),
    source_type: str = Form(""),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dim_source (source_name, source_type, is_active) VALUES (%s, %s, true) RETURNING source_id",
        (source_name, source_type if source_type else None),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "source": {
            "source_id": new_id,
            "source_name": source_name,
            "source_type": source_type if source_type else None,
            "is_active": True,
        },
    }


@router.put("/{old_source_id}")
def update_source(
    old_source_id: int,
    source_name: str = Form(...),
    source_type: str = Form(""),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_source
        SET source_name = %s, source_type = %s, is_active = %s
        WHERE source_id = %s
        """,
        (source_name, source_type if source_type else None, is_active_bool, old_source_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{source_id}")
def delete_source(source_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE dim_source SET is_active = false WHERE source_id = %s",
        (source_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
