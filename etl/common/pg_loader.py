"""Common PostgreSQL loader for stg_pnl_olap.

Used by both windows_ssas and powerbi_xmla runners.
Provides: get_connection, ensure_table, replace_source_data.
"""

import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

log = logging.getLogger("etl.pg_loader")

BATCH_SIZE = 500

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stg_pnl_olap (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    article_name    TEXT,
    date            DATE,
    department_id   TEXT,
    department_name TEXT,
    article_type    TEXT,
    article_id      TEXT,
    article_level1  TEXT,
    article_level2  TEXT,
    amount          NUMERIC(20, 4),
    source_name     TEXT NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stg_pnl_olap_source ON stg_pnl_olap(source_name);
CREATE INDEX IF NOT EXISTS idx_stg_pnl_olap_date   ON stg_pnl_olap(date);
"""

_INSERT_SQL = """
INSERT INTO stg_pnl_olap
    (registrar, article_name, date, department_id, department_name,
     article_type, article_id, article_level1, article_level2,
     amount, source_name, loaded_at)
VALUES %s
"""


def get_connection(pg_host: str, pg_port: int, pg_database: str,
                   pg_user: str, pg_password: str):
    log.info("Connecting to PostgreSQL %s:%d db=%s user=%s", pg_host, pg_port, pg_database, pg_user)
    conn = psycopg2.connect(
        host=pg_host,
        port=pg_port,
        dbname=pg_database,
        user=pg_user,
        password=pg_password,
        connect_timeout=30,
    )
    log.info("PostgreSQL connected OK.")
    return conn


def ensure_table(conn) -> None:
    """Create stg_pnl_olap and indexes if they do not exist."""
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
    conn.commit()
    log.info("Table stg_pnl_olap ensured.")


def replace_source_data(conn, source_name: str, tuples: list,
                        loaded_at: datetime | None = None) -> int:
    """Replace all rows for source_name with new tuples.

    Each tuple must have exactly 12 fields in this order:
        registrar, article_name, date, department_id, department_name,
        article_type, article_id, article_level1, article_level2,
        amount, source_name, loaded_at

    Returns number of rows inserted.
    """
    if loaded_at is None:
        loaded_at = datetime.now(timezone.utc)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM stg_pnl_olap WHERE source_name = %s",
                (source_name,),
            )
            log.info("Deleted %d old rows for source '%s'.", cur.rowcount, source_name)

            total = 0
            for i in range(0, len(tuples), BATCH_SIZE):
                batch = tuples[i : i + BATCH_SIZE]
                psycopg2.extras.execute_values(
                    cur, _INSERT_SQL, batch, page_size=BATCH_SIZE
                )
                total += len(batch)
                if total % 5000 == 0 or total == len(tuples):
                    log.info("  Inserted %d / %d rows", total, len(tuples))

        conn.commit()
        log.info("Commit OK. Total inserted: %d rows.", total)
        return total

    except Exception:
        conn.rollback()
        raise
