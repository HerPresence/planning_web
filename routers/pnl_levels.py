from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import get_connection

router = APIRouter(prefix="/api/pnl-levels")

_tables_ensured = False


def ensure_pnl_level_tables() -> None:
    global _tables_ensured
    if _tables_ensured:
        return
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dim_pnl_level2 (
                id         SERIAL PRIMARY KEY,
                name       TEXT        NOT NULL,
                is_active  BOOLEAN     DEFAULT TRUE,
                created_at TIMESTAMP   DEFAULT NOW(),
                updated_at TIMESTAMP   DEFAULT NOW(),
                UNIQUE (name)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dim_pnl_level1 (
                id         SERIAL PRIMARY KEY,
                level2_id  INTEGER     NOT NULL REFERENCES dim_pnl_level2(id),
                name       TEXT        NOT NULL,
                is_active  BOOLEAN     DEFAULT TRUE,
                created_at TIMESTAMP   DEFAULT NOW(),
                updated_at TIMESTAMP   DEFAULT NOW(),
                UNIQUE (level2_id, name)
            )
            """
        )
        # Column migration: add is_active if missing (for older DB schemas)
        cur.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dim_pnl_level2' AND column_name = 'is_active'
                ) THEN
                    ALTER TABLE dim_pnl_level2 ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dim_pnl_level1' AND column_name = 'is_active'
                ) THEN
                    ALTER TABLE dim_pnl_level1 ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
                END IF;
            END $$;
            """
        )
        # Fix any NULL or FALSE records that were inserted before is_active was tracked.
        # Only resets FALSE→TRUE here for records that have no intentional deactivation
        # (intentional deactivations will be re-applied via migrate_pnl_levels_from_articles
        # which only touches records that exist in dim_article).
        cur.execute("UPDATE dim_pnl_level2 SET is_active = TRUE WHERE is_active IS NULL")
        cur.execute("UPDATE dim_pnl_level1 SET is_active = TRUE WHERE is_active IS NULL")
        conn.commit()
        _tables_ensured = True
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_pnl_level_tables failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


def migrate_pnl_levels_from_articles() -> None:
    """Populate dim_pnl_level2/level1 from existing dim_article data (idempotent).

    Uses ON CONFLICT DO UPDATE SET is_active = TRUE so that records which were
    previously created (possibly with is_active = FALSE) get reactivated when
    they are still referenced by articles.
    """
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        # Insert / reactivate L2 records from articles
        cur.execute(
            """
            INSERT INTO dim_pnl_level2 (name, is_active)
            SELECT DISTINCT TRIM(level2), TRUE
            FROM dim_article
            WHERE level2 IS NOT NULL AND TRIM(level2) != ''
            ON CONFLICT (name) DO UPDATE SET is_active = TRUE
            """
        )
        # Insert / reactivate L1 records from articles
        cur.execute(
            """
            INSERT INTO dim_pnl_level1 (level2_id, name, is_active)
            SELECT l2.id, da.l1name, TRUE
            FROM (
                SELECT DISTINCT TRIM(level2) AS l2name, TRIM(level1) AS l1name
                FROM dim_article
                WHERE level2 IS NOT NULL AND TRIM(level2) != ''
                  AND level1 IS NOT NULL AND TRIM(level1) != ''
            ) da
            JOIN dim_pnl_level2 l2 ON l2.name = da.l2name
            ON CONFLICT (level2_id, name) DO UPDATE SET is_active = TRUE
            """
        )
        conn.commit()
        print("[startup] migrate_pnl_levels_from_articles: done")
    except Exception as exc:
        conn.rollback()
        print(f"[startup] migrate_pnl_levels_from_articles warning: {exc}")
    finally:
        cur.close()
        conn.close()


# ── request / response models ─────────────────────────────────────────────────

class Level2Body(BaseModel):
    name: str

class Level1Body(BaseModel):
    name:      str
    level2_id: int

class Level2UpdateBody(BaseModel):
    name: str

class Level1UpdateBody(BaseModel):
    name:      str
    level2_id: Optional[int] = None


# ── Level 2 endpoints ─────────────────────────────────────────────────────────

@router.get("/level2")
def get_level2(include_inactive: bool = False):
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        where = "" if include_inactive else "WHERE is_active = TRUE"
        cur.execute(f"SELECT id, name, is_active FROM dim_pnl_level2 {where} ORDER BY name")
        return [{"id": r[0], "name": r[1], "is_active": r[2]} for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/level2")
def create_level2(body: Level2Body):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Назва не може бути порожньою")
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, is_active FROM dim_pnl_level2 WHERE LOWER(TRIM(name)) = LOWER(%s)",
            (name,),
        )
        existing = cur.fetchone()
        if existing:
            return {"id": existing[0], "name": existing[1], "is_active": existing[2], "created": False}
        cur.execute(
            "INSERT INTO dim_pnl_level2 (name, is_active) VALUES (%s, TRUE) RETURNING id, name, is_active",
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "name": row[1], "is_active": row[2], "created": True}
    finally:
        cur.close()
        conn.close()


@router.put("/level2/{level2_id}")
def update_level2(level2_id: int, body: Level2UpdateBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Назва не може бути порожньою")
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM dim_pnl_level2 WHERE LOWER(TRIM(name)) = LOWER(%s) AND id != %s",
            (name, level2_id),
        )
        if cur.fetchone():
            raise HTTPException(409, f"Master L2 з назвою '{name}' вже існує")
        cur.execute(
            "UPDATE dim_pnl_level2 SET name = %s, updated_at = NOW() WHERE id = %s RETURNING id, name, is_active",
            (name, level2_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Запис не знайдено")
        conn.commit()
        return {"id": row[0], "name": row[1], "is_active": row[2]}
    finally:
        cur.close()
        conn.close()


@router.patch("/level2/{level2_id}/toggle")
def toggle_level2(level2_id: int):
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE dim_pnl_level2 SET is_active = NOT is_active, updated_at = NOW() WHERE id = %s RETURNING id, name, is_active",
            (level2_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Запис не знайдено")
        conn.commit()
        return {"id": row[0], "name": row[1], "is_active": row[2]}
    finally:
        cur.close()
        conn.close()


# ── Level 1 endpoints ─────────────────────────────────────────────────────────

@router.get("/level1")
def get_level1(level2_id: Optional[int] = None, include_inactive: bool = False):
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        conditions = []
        params: list = []
        if not include_inactive:
            conditions.append("l1.is_active = TRUE")
        if level2_id:
            conditions.append("l1.level2_id = %s")
            params.append(level2_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(
            f"""
            SELECT l1.id, l1.name, l1.level2_id, l1.is_active, l2.name AS level2_name
            FROM dim_pnl_level1 l1
            JOIN dim_pnl_level2 l2 ON l2.id = l1.level2_id
            {where}
            ORDER BY l2.name, l1.name
            """,
            params,
        )
        return [
            {"id": r[0], "name": r[1], "level2_id": r[2], "is_active": r[3], "level2_name": r[4]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


@router.post("/level1")
def create_level1(body: Level1Body):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Назва не може бути порожньою")
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id, name FROM dim_pnl_level2 WHERE id = %s", (body.level2_id,))
        l2 = cur.fetchone()
        if not l2:
            raise HTTPException(404, f"Master L2 з ID {body.level2_id} не знайдено")
        cur.execute(
            "SELECT id, name FROM dim_pnl_level1 WHERE level2_id = %s AND LOWER(TRIM(name)) = LOWER(%s)",
            (body.level2_id, name),
        )
        existing = cur.fetchone()
        if existing:
            return {"id": existing[0], "name": existing[1], "level2_id": body.level2_id, "level2_name": l2[1], "is_active": True, "created": False}
        cur.execute(
            "INSERT INTO dim_pnl_level1 (level2_id, name, is_active) VALUES (%s, %s, TRUE) RETURNING id, name, is_active",
            (body.level2_id, name),
        )
        row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "name": row[1], "level2_id": body.level2_id, "level2_name": l2[1], "is_active": row[2], "created": True}
    finally:
        cur.close()
        conn.close()


@router.put("/level1/{level1_id}")
def update_level1(level1_id: int, body: Level1UpdateBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Назва не може бути порожньою")
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT level2_id FROM dim_pnl_level1 WHERE id = %s", (level1_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(404, "Запис не знайдено")
        target_l2_id = body.level2_id if body.level2_id else existing[0]
        cur.execute(
            "SELECT id FROM dim_pnl_level1 WHERE level2_id = %s AND LOWER(TRIM(name)) = LOWER(%s) AND id != %s",
            (target_l2_id, name, level1_id),
        )
        if cur.fetchone():
            raise HTTPException(409, f"Master L1 з назвою '{name}' вже існує в цьому Master L2")
        cur.execute(
            "UPDATE dim_pnl_level1 SET name = %s, level2_id = %s, updated_at = NOW() WHERE id = %s RETURNING id, name, level2_id, is_active",
            (name, target_l2_id, level1_id),
        )
        row = cur.fetchone()
        conn.commit()
        cur.execute("SELECT name FROM dim_pnl_level2 WHERE id = %s", (row[2],))
        l2row = cur.fetchone()
        return {"id": row[0], "name": row[1], "level2_id": row[2], "is_active": row[3], "level2_name": l2row[0] if l2row else ""}
    finally:
        cur.close()
        conn.close()


@router.patch("/level1/{level1_id}/toggle")
def toggle_level1(level1_id: int):
    ensure_pnl_level_tables()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE dim_pnl_level1 SET is_active = NOT is_active, updated_at = NOW() WHERE id = %s RETURNING id, name, level2_id, is_active",
            (level1_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Запис не знайдено")
        conn.commit()
        cur.execute("SELECT name FROM dim_pnl_level2 WHERE id = %s", (row[2],))
        l2row = cur.fetchone()
        return {"id": row[0], "name": row[1], "level2_id": row[2], "is_active": row[3], "level2_name": l2row[0] if l2row else ""}
    finally:
        cur.close()
        conn.close()
