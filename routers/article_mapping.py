from fastapi import APIRouter
from pydantic import BaseModel

from db import get_connection

router = APIRouter(prefix="/api/import-sources")


class ImportSourceCreate(BaseModel):
    source_name: str
    source_type: str
    source_url: str | None = None
    # mapping fields — optional; not required for olap_sql sources
    article_id_field: str | None = None
    article_name_field: str | None = None
    article_type_field: str | None = None
    level1_field: str | None = None
    level2_field: str | None = None
    pnl_id_field: str | None = None
    # OLAP / SQL connection settings
    db_server: str | None = ""
    db_port: str | None = ""
    db_database: str | None = ""
    db_cube_model: str | None = ""
    db_login: str | None = ""
    db_password: str | None = ""
    db_query: str | None = ""
    db_refresh_interval: str | None = ""


def ensure_import_sources_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS import_sources (
            id SERIAL PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT,
            source_url TEXT,
            article_id_field TEXT,
            article_name_field TEXT,
            article_type_field TEXT,
            level1_field TEXT,
            level2_field TEXT,
            pnl_id_field TEXT,
            is_active BOOLEAN DEFAULT TRUE
        )
        """
    )
    for col in [
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS source_type TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS source_url TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_id_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_name_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS article_type_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level1_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level2_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS pnl_id_field TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_server TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_port TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_database TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_cube_model TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_login TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_password TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_query TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_refresh_interval TEXT",
    ]:
        cur.execute(col)


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
            is_active,
            db_server,
            db_port,
            db_database,
            db_cube_model,
            db_login,
            db_password,
            db_query,
            db_refresh_interval
        FROM import_sources
        WHERE is_active IS TRUE
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
                "db_server": r[11],
                "db_port": r[12],
                "db_database": r[13],
                "db_cube_model": r[14],
                "db_login": r[15],
                "db_password": r[16],
                "db_query": r[17],
                "db_refresh_interval": r[18],
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
        # Add OLAP connection columns (safe — no-op if already exist)
        for col_sql in [
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_server TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_port TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_database TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_cube_model TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_login TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_password TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_query TEXT",
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS db_refresh_interval TEXT",
        ]:
            cur.execute(col_sql)
        # Drop NOT NULL on mapping fields so OLAP sources can omit them
        # Safe to run repeatedly — DROP NOT NULL on a nullable column is a no-op
        for col_name in [
            "article_id_field",
            "article_name_field",
            "article_type_field",
            "level1_field",
            "level2_field",
            "pnl_id_field",
        ]:
            cur.execute(
                f"ALTER TABLE import_sources ALTER COLUMN {col_name} DROP NOT NULL"
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
        SET source_name        = %s,
            source_type        = %s,
            source_url         = %s,
            article_id_field   = %s,
            article_name_field = %s,
            article_type_field = %s,
            level1_field       = %s,
            level2_field       = %s,
            pnl_id_field       = %s,
            db_server          = %s,
            db_port            = %s,
            db_database        = %s,
            db_cube_model      = %s,
            db_login           = %s,
            db_password        = %s,
            db_query           = %s,
            db_refresh_interval = %s
        WHERE id = %s
        """,
        (
            data.source_name,
            data.source_type,
            data.source_url or None,
            data.article_id_field or None,
            data.article_name_field or None,
            data.article_type_field or None,
            data.level1_field or None,
            data.level2_field or None,
            data.pnl_id_field or None,
            data.db_server or None,
            data.db_port or None,
            data.db_database or None,
            data.db_cube_model or None,
            data.db_login or None,
            data.db_password or None,
            data.db_query or None,
            data.db_refresh_interval or None,
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
            source_name, source_type, source_url,
            article_id_field, article_name_field, article_type_field,
            level1_field, level2_field, pnl_id_field,
            db_server, db_port, db_database, db_cube_model,
            db_login, db_password, db_query, db_refresh_interval,
            is_active
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        """,
        (
            data.source_name,
            data.source_type,
            data.source_url or None,
            data.article_id_field or None,
            data.article_name_field or None,
            data.article_type_field or None,
            data.level1_field or None,
            data.level2_field or None,
            data.pnl_id_field or None,
            data.db_server or None,
            data.db_port or None,
            data.db_database or None,
            data.db_cube_model or None,
            data.db_login or None,
            data.db_password or None,
            data.db_query or None,
            data.db_refresh_interval or None,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok", "message": "Джерело збережено"}
