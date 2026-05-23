"""
Soft delete migration helper.
Adds is_deleted / deleted_at / deleted_by columns to tables that support it.
"""
from db import get_connection

_TABLES = [
    "dim_article",
    "dim_department",
    "dim_holding",
    "dim_organization",
    "dim_region",
    "dim_branch",
    "dim_source",
]

_ensured = False


def ensure_soft_delete_columns():
    global _ensured
    if _ensured:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        for table in _TABLES:
            # Check if table exists first
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
                (table,)
            )
            if not cur.fetchone()[0]:
                continue
            for col_sql in [
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_deleted   BOOLEAN   DEFAULT FALSE",
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_at   TIMESTAMP",
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_by   INTEGER",
            ]:
                cur.execute(col_sql)
        # Also for users and roles (already in users table management)
        for table in ("users", "roles"):
            for col_sql in [
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE",
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_by INTEGER",
            ]:
                cur.execute(col_sql)
        conn.commit()
        _ensured = True
    finally:
        cur.close()
        conn.close()
