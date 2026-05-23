"""Audit log service — records all significant user actions."""
import json
from datetime import datetime
from db import get_connection

_table_ensured = False


def ensure_audit_table():
    global _table_ensured
    if _table_ensured:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER,
                user_email  TEXT,
                action      TEXT NOT NULL,
                entity_type TEXT,
                entity_id   TEXT,
                menu_key    TEXT,
                old_value   JSONB,
                new_value   JSONB,
                ip_address  TEXT,
                user_agent  TEXT,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_user  ON audit_log(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_action ON audit_log(action)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_created ON audit_log(created_at)")
        conn.commit()
        _table_ensured = True
    finally:
        cur.close(); conn.close()


def log_action(
    action: str,
    user_id: int | None = None,
    user_email: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    menu_key: str | None = None,
    old_value=None,
    new_value=None,
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """Insert one audit record. Silently ignores DB errors to never block the main request."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO audit_log
                    (user_id, user_email, action, entity_type, entity_id,
                     menu_key, old_value, new_value, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, user_email, action, entity_type, str(entity_id) if entity_id is not None else None,
                menu_key,
                json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None,
                ip_address, user_agent,
            ))
            conn.commit()
        finally:
            cur.close(); conn.close()
    except Exception:
        pass  # audit must never break the main flow
