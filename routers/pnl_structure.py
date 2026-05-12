from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/pnl-structure")

SUBTOTAL_GROUPS = {"ebitda", "ebit", "ebt", "netprofit", "net profit"}


def ensure_pnl_structure_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pnl_structure (
                id SERIAL PRIMARY KEY,
                pnl_code TEXT,
                pnl_name TEXT NOT NULL,
                pnl_group TEXT,
                pnl_level INTEGER DEFAULT 1,
                pnl_order INTEGER DEFAULT 0,
                pnl_sign INTEGER DEFAULT 1,
                parent_id INTEGER NULL,
                is_total BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE
            )
            """
        )
        cur.execute("SET lock_timeout = '3s'")
        cur.execute("ALTER TABLE pnl_structure ADD COLUMN IF NOT EXISTS pnl_order INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE pnl_structure ADD COLUMN IF NOT EXISTS pnl_sign INTEGER DEFAULT 1")
        conn.commit()
    except Exception as exc:
        print(f"[startup] ensure_pnl_structure_table warning: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


def calculate_pnl_level(cur, parent_id):
    if not parent_id:
        return 1

    level = 1
    visited = set()
    current_id = parent_id

    while current_id and current_id not in visited:
        visited.add(current_id)
        cur.execute("SELECT parent_id FROM pnl_structure WHERE id = %s", (current_id,))
        row = cur.fetchone()

        if not row:
            break

        current_id = row[0]
        level += 1

    return level


def is_subtotal_group(pnl_group):
    return bool(pnl_group and pnl_group.strip().lower() in SUBTOTAL_GROUPS)


@router.get("")
def get_pnl_structures():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, pnl_code, pnl_name, pnl_group, pnl_level, pnl_order, pnl_sign, parent_id, is_total, is_active FROM pnl_structure ORDER BY pnl_order, pnl_name"
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "id": r[0],
                "pnl_code": r[1],
                "pnl_name": r[2],
                "pnl_group": r[3],
                "pnl_level": r[4],
                "pnl_order": r[5],
                "pnl_sign": r[6],
                "pnl_parent": r[7],
                "is_total": r[8],
                "is_active": r[9],
            }
        )

    items_by_id = {item["id"]: item for item in result}
    for item in result:
        level = 1
        seen = set()
        current_id = item["pnl_parent"]

        while current_id and current_id not in seen:
            seen.add(current_id)
            parent = items_by_id.get(current_id)
            if not parent:
                break
            level += 1
            current_id = parent["pnl_parent"]

        item["pnl_level"] = level

    cur.close()
    conn.close()

    return result


@router.post("")
def create_pnl_structure(
    pnl_code: str = Form(""),
    pnl_name: str = Form(...),
    pnl_group: str = Form(""),
    pnl_order: int = Form(0),
    pnl_sign: int = Form(1),
    pnl_parent: int = Form(0),
    is_total: str = Form("false"),
):
    conn = get_connection()
    cur = conn.cursor()

    parent_id = pnl_parent if pnl_parent else None
    pnl_level = calculate_pnl_level(cur, parent_id)
    is_total_bool = is_total.lower() == "true"
    pnl_sign_value = 1 if pnl_sign == 1 else -1

    cur.execute(
        "INSERT INTO pnl_structure (pnl_code, pnl_name, pnl_group, pnl_level, pnl_order, pnl_sign, parent_id, is_total, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)",
        (
            pnl_code if pnl_code else None,
            pnl_name,
            pnl_group if pnl_group else None,
            pnl_level,
            pnl_order,
            pnl_sign_value,
            parent_id,
            is_total_bool,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.put("/{old_id}")
def update_pnl_structure(
    old_id: int,
    pnl_code: str = Form(""),
    pnl_name: str = Form(...),
    pnl_group: str = Form(""),
    pnl_order: int = Form(0),
    pnl_sign: int = Form(1),
    pnl_parent: int = Form(0),
    is_total: str = Form("false"),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    parent_id = pnl_parent if pnl_parent else None
    pnl_level = calculate_pnl_level(cur, parent_id)
    is_total_bool = is_total.lower() == "true"
    is_active_bool = is_active.lower() == "true"
    pnl_sign_value = 1 if pnl_sign == 1 else -1

    cur.execute(
        """
        UPDATE pnl_structure
        SET pnl_code = %s, pnl_name = %s, pnl_group = %s, pnl_level = %s, pnl_order = %s, pnl_sign = %s, parent_id = %s, is_total = %s, is_active = %s
        WHERE id = %s
        """,
        (
            pnl_code if pnl_code else None,
            pnl_name,
            pnl_group if pnl_group else None,
            pnl_level,
            pnl_order,
            pnl_sign_value,
            parent_id,
            is_total_bool,
            is_active_bool,
            old_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{id}")
def delete_pnl_structure(id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE pnl_structure SET is_active = false WHERE id = %s",
        (id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
