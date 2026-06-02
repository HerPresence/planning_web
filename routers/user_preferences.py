import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import get_connection
from routers.auth_router import get_current_user

router = APIRouter(prefix="/user-preferences", tags=["user-preferences"])


def ensure_user_preferences_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_table_preferences (
                id              SERIAL PRIMARY KEY,
                user_id         INTEGER     NOT NULL,
                page_key        VARCHAR(100) NOT NULL,
                visible_columns JSONB       NOT NULL DEFAULT '[]',
                column_order    JSONB       NOT NULL DEFAULT '[]',
                density         VARCHAR(20) NOT NULL DEFAULT 'normal',
                my_preset       JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(user_id, page_key)
            )
        """)
        conn.commit()
    finally:
        cur.close(); conn.close()


class TablePreferencesBody(BaseModel):
    page_key:        str
    visible_columns: List[str]
    column_order:    List[str]
    density:         str           = "normal"
    my_preset:       Optional[Any] = None


@router.get("/table")
def get_table_preferences(page_key: str, _u=Depends(get_current_user)):
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT visible_columns, column_order, density, my_preset
               FROM user_table_preferences
               WHERE user_id = %s AND page_key = %s""",
            (_u["id"], page_key),
        )
        row = cur.fetchone()
        if not row:
            return {"found": False}
        vc, co, dn, mp = row
        return {
            "found":           True,
            "visible_columns": vc or [],
            "column_order":    co or [],
            "density":         dn or "normal",
            "my_preset":       mp,
        }
    finally:
        cur.close(); conn.close()


@router.post("/table")
def save_table_preferences(body: TablePreferencesBody, _u=Depends(get_current_user)):
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO user_table_preferences
                   (user_id, page_key, visible_columns, column_order, density, my_preset, updated_at)
               VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, NOW())
               ON CONFLICT (user_id, page_key) DO UPDATE SET
                   visible_columns = EXCLUDED.visible_columns,
                   column_order    = EXCLUDED.column_order,
                   density         = EXCLUDED.density,
                   my_preset       = EXCLUDED.my_preset,
                   updated_at      = NOW()""",
            (
                _u["id"], body.page_key,
                json.dumps(body.visible_columns),
                json.dumps(body.column_order),
                body.density,
                json.dumps(body.my_preset) if body.my_preset is not None else None,
            ),
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()
