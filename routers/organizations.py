from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/organizations")


def ensure_organization_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_organization (
            organization_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            organization_name TEXT NOT NULL,
            holding_id INTEGER,
            is_active BOOLEAN DEFAULT true
        )
        """
    )

    conn.commit()
    cur.close()
    conn.close()


@router.get("")
def get_organizations():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT o.organization_id, o.organization_name, o.holding_id, h.holding_name, o.is_active
        FROM dim_organization o
        LEFT JOIN dim_holding h ON o.holding_id = h.holding_id
        ORDER BY o.organization_name
        """
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "organization_id": r[0],
                "organization_name": r[1],
                "holding_id": r[2],
                "holding_name": r[3] if r[3] is not None else "",
                "is_active": r[4],
            }
        )

    cur.close()
    conn.close()

    return result


@router.post("")
def create_organization(
    organization_name: str = Form(...),
    holding_id: int | None = Form(None),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dim_organization (organization_name, holding_id, is_active) VALUES (%s, %s, true) RETURNING organization_id",
        (organization_name, holding_id if holding_id else None),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "organization": {
            "organization_id": new_id,
            "organization_name": organization_name,
            "holding_id": holding_id if holding_id else None,
            "is_active": True,
        },
    }


@router.put("/{old_organization_id}")
def update_organization(
    old_organization_id: int,
    organization_name: str = Form(...),
    holding_id: int | None = Form(None),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_organization
        SET organization_name = %s, holding_id = %s, is_active = %s
        WHERE organization_id = %s
        """,
        (organization_name, holding_id if holding_id else None, is_active_bool, old_organization_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{organization_id}")
def delete_organization(organization_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE dim_organization SET is_active = false WHERE organization_id = %s",
        (organization_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
