from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from db import get_connection

router = APIRouter(prefix="/api/brands")


class BrandBody(BaseModel):
    brand_uid:   Optional[str] = None
    brand_name:  str
    brand_group: Optional[str] = None
    is_active:   bool = True


def ensure_brand_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_brand (
                id          SERIAL PRIMARY KEY,
                brand_uid   TEXT,
                brand_name  TEXT NOT NULL,
                brand_group TEXT,
                is_active   BOOLEAN DEFAULT true,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        print("[startup] ensure_brand_table: done")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_brand_table failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


def _row_to_dict(r) -> dict:
    return {
        "id":          r[0],
        "brand_uid":   r[1],
        "brand_name":  r[2],
        "brand_group": r[3],
        "is_active":   r[4],
        "created_at":  str(r[5]) if r[5] else None,
        "updated_at":  str(r[6]) if r[6] else None,
    }


@router.get("")
def list_brands(include_inactive: bool = False, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        where = "" if include_inactive else "WHERE is_active = TRUE"
        cur.execute(
            f"""SELECT id, brand_uid, brand_name, brand_group, is_active, created_at, updated_at
                FROM dim_brand {where}
                ORDER BY brand_name""",
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("")
def create_brand(body: BrandBody, _u=Depends(get_current_user)):
    if not body.brand_name.strip():
        raise HTTPException(400, "brand_name is required")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO dim_brand (brand_uid, brand_name, brand_group, is_active)
               VALUES (%s, %s, %s, true) RETURNING id""",
            (body.brand_uid or None, body.brand_name.strip(), body.brand_group or None),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"ok": True, "id": new_id}
    finally:
        cur.close()
        conn.close()


@router.put("/{brand_id}")
def update_brand(brand_id: int, body: BrandBody, _u=Depends(get_current_user)):
    if not body.brand_name.strip():
        raise HTTPException(400, "brand_name is required")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE dim_brand
               SET brand_uid = %s, brand_name = %s, brand_group = %s,
                   is_active = %s, updated_at = NOW()
               WHERE id = %s""",
            (body.brand_uid or None, body.brand_name.strip(), body.brand_group or None,
             body.is_active, brand_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/{brand_id}")
def deactivate_brand(brand_id: int, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE dim_brand SET is_active = false, updated_at = NOW() WHERE id = %s",
            (brand_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.patch("/{brand_id}/restore")
def restore_brand(brand_id: int, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE dim_brand SET is_active = true, updated_at = NOW() WHERE id = %s",
            (brand_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()
