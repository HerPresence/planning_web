from fastapi import APIRouter
from pydantic import BaseModel

from db import get_connection

router = APIRouter(prefix="/api/import-sources")


class ImportSourceCreate(BaseModel):
    source_name: str
    source_type: str
    source_url: str | None = ""
    article_id_field: str
    article_name_field: str
    article_type_field: str | None = ""
    level1_field: str | None = ""
    level2_field: str | None = ""
    pnl_id_field: str | None = ""


def ensure_import_sources_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS import_sources (
            id SERIAL PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT,
            article_id_field TEXT NOT NULL,
            article_name_field TEXT NOT NULL,
            article_type_field TEXT,
            level1_field TEXT,
            level2_field TEXT,
            pnl_id_field TEXT,
            is_active BOOLEAN DEFAULT TRUE
        )
        """
    )

    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS source_type TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS source_url TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_id_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_name_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_type_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level1_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level2_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS pnl_id_field TEXT;")
    cur.execute("ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;")


@router.get("")
def get_import_sources():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            source_name,
            source_type,
            source_url,
            article_id_field,
            article_name_field,
            article_type_field,
            level1_field,
            level2_field,
            pnl_id_field,
            is_active
        FROM import_sources
        ORDER BY id DESC
        """
    )

    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "id": r[0],
                "source_name": r[1],
                "source_type": r[2],
                "source_url": r[3],
                "article_id_field": r[4],
                "article_name_field": r[5],
                "article_type_field": r[6],
                "level1_field": r[7],
                "level2_field": r[8],
                "pnl_id_field": r[9],
                "is_active": r[10],
            }
        )

    cur.close()
    conn.close()

    return result


def ensure_import_sources_standalone():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS import_sources (
                id               SERIAL PRIMARY KEY,
                source_name      TEXT NOT NULL,
                source_type      TEXT,
                source_url       TEXT,
                article_id_field TEXT,
                article_name_field TEXT,
                article_type_field TEXT,
                level1_field     TEXT,
                level2_field     TEXT,
                pnl_id_field     TEXT,
                is_active        BOOLEAN DEFAULT TRUE
            )
            """
        )
        conn.commit()
    except Exception as exc:
        print(f"[startup] ensure_import_sources warning: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


@router.put("/{source_id}")
def update_import_source(
    source_id: int,
    data: ImportSourceCreate,
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE import_sources
        SET source_name = %s,
            source_type = %s,
            source_url = %s,
            article_id_field = %s,
            article_name_field = %s,
            article_type_field = %s,
            level1_field = %s,
            level2_field = %s,
            pnl_id_field = %s
        WHERE id = %s
        """,
        (
            data.source_name,
            data.source_type,
            data.source_url,
            data.article_id_field,
            data.article_name_field,
            data.article_type_field,
            data.level1_field,
            data.level2_field,
            data.pnl_id_field,
            source_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/{source_id}")
def delete_import_source(source_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE import_sources SET is_active = FALSE WHERE id = %s",
        (source_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.post("")
def create_import_source(data: ImportSourceCreate):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO import_sources (
            source_name,
            source_type,
            source_url,
            article_id_field,
            article_name_field,
            article_type_field,
            level1_field,
            level2_field,
            pnl_id_field,
            is_active
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        """,
        (
            data.source_name,
            data.source_type,
            data.source_url,
            data.article_id_field,
            data.article_name_field,
            data.article_type_field,
            data.level1_field,
            data.level2_field,
            data.pnl_id_field,
        ),
    )

    conn.commit()

    cur.close()
    conn.close()

    return {"status": "ok", "message": "Схему відповідності збережено"}