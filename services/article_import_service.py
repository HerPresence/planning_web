import os
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

from db import get_connection
from routers.pnl_import import _run_ssas_ps

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH",
    r"T:\planning_web\google_credentials.json",
)

OLAP_TYPES   = {"olap_ssas_dax", "sql_odbc", "olap_sql"}
GSHEET_TYPES = {"google_sheet", "google_sheets"}

# Extra fields shared between staging and master
_EXTRA_FIELDS = [
    ("uid_expense_article_col", "uid_expense_article"),
    ("expense_element_col",     "expense_element"),
    ("expense_company_col",     "expense_company"),
    ("level2_olap_col",         "level2_olap"),
    ("level1_olap_col",         "level1_olap"),
]

# ── migrations (idempotent) ───────────────────────────────────────────────────

_columns_ensured   = False
_staging_ensured   = False
_legacy_migrated   = False


def ensure_article_columns() -> None:
    """Add new dim_article / import_sources columns if not yet present."""
    global _columns_ensured
    if _columns_ensured:
        return

    stmts = [
        "ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS uid_expense_article TEXT",
        "ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS expense_element      TEXT",
        "ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS expense_company      TEXT",
        "ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS level2_olap          TEXT",
        "ALTER TABLE dim_article ADD COLUMN IF NOT EXISTS level1_olap          TEXT",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS uid_expense_article_field TEXT DEFAULT ''",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS expense_element_field     TEXT DEFAULT ''",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS expense_company_field     TEXT DEFAULT ''",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level2_olap_field         TEXT DEFAULT ''",
        "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS level1_olap_field         TEXT DEFAULT ''",
    ]

    conn = get_connection()
    cur  = conn.cursor()
    try:
        for stmt in stmts:
            cur.execute(stmt)
        conn.commit()
        _columns_ensured = True
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_article_columns failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


def ensure_source_staging_tables() -> None:
    """Create dim_article_source and article_source_mapping if not yet present."""
    global _staging_ensured
    if _staging_ensured:
        return

    stmts = [
        """
        CREATE TABLE IF NOT EXISTS dim_article_source (
            id                  SERIAL PRIMARY KEY,
            source_id           INTEGER NOT NULL,
            source_name         TEXT    DEFAULT '',
            source_article_id   TEXT    NOT NULL,
            source_article_name TEXT    DEFAULT '',
            source_article_type TEXT    DEFAULT '',
            source_level1       TEXT    DEFAULT '',
            source_level2       TEXT    DEFAULT '',
            uid_expense_article TEXT    DEFAULT '',
            expense_element     TEXT    DEFAULT '',
            expense_company     TEXT    DEFAULT '',
            level1_olap         TEXT    DEFAULT '',
            level2_olap         TEXT    DEFAULT '',
            loaded_at           TIMESTAMP DEFAULT NOW(),
            is_active           BOOLEAN   DEFAULT TRUE,
            UNIQUE (source_id, source_article_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS article_source_mapping (
            id                SERIAL PRIMARY KEY,
            source_id         INTEGER NOT NULL,
            source_article_id TEXT    NOT NULL,
            master_article_id TEXT,
            confidence        NUMERIC(5,2) DEFAULT 0,
            mapping_status    TEXT    DEFAULT 'pending',
            created_at        TIMESTAMP DEFAULT NOW(),
            updated_at        TIMESTAMP DEFAULT NOW(),
            UNIQUE (source_id, source_article_id)
        )
        """,
        # Indexes for pagination / filter / search performance
        "CREATE INDEX IF NOT EXISTS idx_das_source_id         ON dim_article_source(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_das_source_article_id ON dim_article_source(source_article_id)",
        "CREATE INDEX IF NOT EXISTS idx_das_expense_company   ON dim_article_source(expense_company)",
        "CREATE INDEX IF NOT EXISTS idx_das_is_active         ON dim_article_source(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_asm_source_id         ON article_source_mapping(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_asm_mapping_status    ON article_source_mapping(mapping_status)",
        "CREATE INDEX IF NOT EXISTS idx_asm_master_article_id ON article_source_mapping(master_article_id)",
    ]

    conn = get_connection()
    cur  = conn.cursor()
    try:
        for stmt in stmts:
            cur.execute(stmt)
        conn.commit()
        _staging_ensured = True
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_source_staging_tables failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


def migrate_legacy_article_mappings() -> None:
    """One-time migration: copy old article_mapping rows into the staging tables.

    For every active row in article_mapping that has both source_id and
    source_article_id set:
      • Insert a dim_article_source row (staging row) — ON CONFLICT DO NOTHING
        so existing OLAP/import rows are never overwritten.
      • Insert an article_source_mapping row — ON CONFLICT DO NOTHING.
        If article_id is set → status 'mapped', confidence 100.
        If article_id is null → status 'pending'.

    Safe to run multiple times; new data is never replaced.
    """
    global _legacy_migrated
    if _legacy_migrated:
        return

    # Staging tables must exist before we can insert into them.
    ensure_source_staging_tables()

    conn = get_connection()
    cur  = conn.cursor()
    try:
        # ── 1. ensure dim_article_source rows ────────────────────────────────
        cur.execute(
            """
            INSERT INTO dim_article_source (
                source_id,
                source_name,
                source_article_id,
                source_article_name
            )
            SELECT
                am.source_id,
                COALESCE(s.source_name, am.source_system, ''),
                am.source_article_id,
                COALESCE(am.source_article_name, '')
            FROM article_mapping am
            LEFT JOIN import_sources s ON s.id = am.source_id
            WHERE am.is_active          = TRUE
              AND am.source_id          IS NOT NULL
              AND am.source_article_id  IS NOT NULL
              AND am.source_article_id  != ''
            ON CONFLICT (source_id, source_article_id) DO NOTHING
            """
        )

        # ── 2. ensure article_source_mapping rows ─────────────────────────────
        cur.execute(
            """
            INSERT INTO article_source_mapping (
                source_id,
                source_article_id,
                master_article_id,
                mapping_status,
                confidence
            )
            SELECT
                am.source_id,
                am.source_article_id,
                CASE WHEN am.article_id IS NOT NULL
                     THEN am.article_id::TEXT
                     ELSE NULL
                END,
                CASE WHEN am.article_id IS NOT NULL THEN 'mapped' ELSE 'pending' END,
                CASE WHEN am.article_id IS NOT NULL THEN 100.0   ELSE 0.0       END
            FROM article_mapping am
            WHERE am.is_active          = TRUE
              AND am.source_id          IS NOT NULL
              AND am.source_article_id  IS NOT NULL
              AND am.source_article_id  != ''
            ON CONFLICT (source_id, source_article_id) DO NOTHING
            """
        )

        conn.commit()
        _legacy_migrated = True
        print("[startup] migrate_legacy_article_mappings: done")
    except Exception as exc:
        conn.rollback()
        print(f"[startup] migrate_legacy_article_mappings warning: {exc}")
    finally:
        cur.close()
        conn.close()


# ── helpers ───────────────────────────────────────────────────────────────────

def safe_text(value) -> str:
    return str(value).strip() if value is not None else ""


def safe_int(value) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except Exception:
        return 0


def get_google_sheet_rows(sheet_url: str) -> list:
    if not sheet_url:
        raise ValueError("Не вказано посилання на Google Sheet")
    if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"Файл Google credentials не знайдено: {GOOGLE_CREDENTIALS_PATH}"
        )
    credentials = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    gc = gspread.authorize(credentials)
    return gc.open_by_url(sheet_url).sheet1.get_all_records()


# ── raw data fetch ────────────────────────────────────────────────────────────

def _fetch_source_data(source: dict, max_rows: int = 0) -> dict:
    stype = source.get("source_type", "")

    if stype in OLAP_TYPES:
        db_query = (source.get("db_query") or "").strip()
        if not db_query:
            raise ValueError(
                "db_query порожній. Вкажіть DAX/SQL у налаштуваннях джерела "
                "(Відповідність → редагувати)."
            )
        return _run_ssas_ps(
            source.get("db_server", "") or "",
            source.get("db_port", "")   or "",
            source.get("db_database", "") or "",
            source.get("db_login", "")  or "",
            source.get("db_password", "") or "",
            db_query,
            max_rows,
        )

    if stype in GSHEET_TYPES:
        all_rows = get_google_sheet_rows(source.get("source_url", ""))
        cols     = list(all_rows[0].keys()) if all_rows else []
        preview  = all_rows[:max_rows] if max_rows > 0 else all_rows
        return {"columns": cols, "rows": preview, "count": len(all_rows)}

    raise ValueError(f"Тип джерела не підтримується: {stype}")


# ── mapping helpers ───────────────────────────────────────────────────────────

def _apply_article_mapping(data: dict, mapping: dict) -> list:
    """Map raw source rows → article dicts (used for Google Sheets → dim_article)."""
    raw_rows = data.get("rows", [])

    id_col   = (mapping.get("article_id_col")   or "").strip()
    name_col = (mapping.get("article_name_col")  or "").strip()
    type_col = (mapping.get("article_type_col")  or "").strip()
    l1_col   = (mapping.get("level1_col")        or "").strip()
    l2_col   = (mapping.get("level2_col")        or "").strip()
    pnl_col  = (mapping.get("pnl_id_col")        or "").strip()

    extra_cols = {
        db_col: (mapping.get(map_key) or "").strip()
        for map_key, db_col in _EXTRA_FIELDS
    }

    result = []
    for row in raw_rows:
        article_id = safe_text(row.get(id_col, "")) if id_col else ""
        if not article_id:
            continue

        art = {
            "article_id":   article_id,
            "article_name": safe_text(row.get(name_col, "")) if name_col else "",
            "article_type": safe_text(row.get(type_col, "")) if type_col else "",
            "level1":       safe_text(row.get(l1_col, ""))   if l1_col   else "",
            "level2":       safe_text(row.get(l2_col, ""))   if l2_col   else "",
            "pnl_id":       safe_int(row.get(pnl_col))       if pnl_col  else None,
        }
        for db_col, src_col in extra_cols.items():
            art[db_col] = safe_text(row.get(src_col, "")) if src_col else None

        result.append(art)
    return result


# ── public API ────────────────────────────────────────────────────────────────

def preview_articles_source(source: dict) -> dict:
    try:
        data = _fetch_source_data(source, max_rows=10)
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "source_name": source.get("source_name"),
        }

    count = data.get("count", 0)
    return {
        "status": "ok",
        "columns": data.get("columns", []),
        "preview_rows": data.get("rows", []),
        "total_rows": count,
        "zero_rows_warning": count == 0,
        "db_query": source.get("db_query", ""),
        "source_name": source.get("source_name"),
        "source_type": source.get("source_type", ""),
    }


def import_articles_source(source: dict, mapping: dict) -> dict:
    """Route import: OLAP → staging table, Google Sheets → dim_article master."""
    stype = source.get("source_type", "")

    try:
        data = _fetch_source_data(source, max_rows=0)
    except Exception as exc:
        return {"status": "error", "message": str(exc), "source_name": source.get("source_name")}

    if stype in OLAP_TYPES:
        return _import_to_staging(source, data, mapping)
    else:
        return _import_to_master(source, data, mapping)


# ── OLAP → staging ────────────────────────────────────────────────────────────

def _import_to_staging(source: dict, data: dict, mapping: dict) -> dict:
    ensure_source_staging_tables()

    source_id   = source.get("source_id", 0)
    source_name = source.get("source_name", "")

    id_col   = (mapping.get("article_id_col")   or "").strip()
    name_col = (mapping.get("article_name_col")  or "").strip()
    type_col = (mapping.get("article_type_col")  or "").strip()
    l1_col   = (mapping.get("level1_col")        or "").strip()
    l2_col   = (mapping.get("level2_col")        or "").strip()

    extra_src = {
        stg_col: (mapping.get(map_key) or "").strip()
        for map_key, stg_col in _EXTRA_FIELDS
    }

    raw_rows = data.get("rows", [])
    conn = get_connection()
    cur  = conn.cursor()
    inserted = updated = skipped = 0
    errors = []

    try:
        for row in raw_rows:
            src_id = safe_text(row.get(id_col, "")) if id_col else ""
            if not src_id:
                skipped += 1
                continue
            try:
                cur.execute(
                    """
                    INSERT INTO dim_article_source
                        (source_id, source_name, source_article_id, source_article_name,
                         source_article_type, source_level1, source_level2,
                         uid_expense_article, expense_element, expense_company,
                         level1_olap, level2_olap, loaded_at, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),TRUE)
                    ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                        source_name         = EXCLUDED.source_name,
                        source_article_name = EXCLUDED.source_article_name,
                        source_article_type = EXCLUDED.source_article_type,
                        source_level1       = EXCLUDED.source_level1,
                        source_level2       = EXCLUDED.source_level2,
                        uid_expense_article = EXCLUDED.uid_expense_article,
                        expense_element     = EXCLUDED.expense_element,
                        expense_company     = EXCLUDED.expense_company,
                        level1_olap         = EXCLUDED.level1_olap,
                        level2_olap         = EXCLUDED.level2_olap,
                        loaded_at           = NOW(),
                        is_active           = TRUE
                    """,
                    (
                        source_id, source_name, src_id,
                        safe_text(row.get(name_col, "")) if name_col else "",
                        safe_text(row.get(type_col, "")) if type_col else "",
                        safe_text(row.get(l1_col, ""))   if l1_col   else "",
                        safe_text(row.get(l2_col, ""))   if l2_col   else "",
                        safe_text(row.get(extra_src.get("uid_expense_article", ""), ""))
                            if extra_src.get("uid_expense_article") else "",
                        safe_text(row.get(extra_src.get("expense_element", ""), ""))
                            if extra_src.get("expense_element") else "",
                        safe_text(row.get(extra_src.get("expense_company", ""), ""))
                            if extra_src.get("expense_company") else "",
                        safe_text(row.get(extra_src.get("level1_olap", ""), ""))
                            if extra_src.get("level1_olap") else "",
                        safe_text(row.get(extra_src.get("level2_olap", ""), ""))
                            if extra_src.get("level2_olap") else "",
                    ),
                )
                if cur.statusmessage and cur.statusmessage.startswith("INSERT"):
                    inserted += 1
                else:
                    updated += 1
            except Exception as row_err:
                skipped += 1
                errors.append({"source_article_id": src_id, "error": str(row_err)})

        conn.commit()
        return {
            "status": "ok",
            "message": "Завантажено в staging",
            "target": "staging",
            "source_name": source_name,
            "total_rows": len(raw_rows),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:20],
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── Google Sheets → dim_article master ───────────────────────────────────────

def _import_to_master(source: dict, data: dict, mapping: dict) -> dict:
    articles = _apply_article_mapping(data, mapping)
    has_pnl  = bool((mapping.get("pnl_id_col") or "").strip())

    extra_mapped = {
        db_col: bool((mapping.get(map_key) or "").strip())
        for map_key, db_col in _EXTRA_FIELDS
    }

    conn = get_connection()
    cur  = conn.cursor()
    imported = updated = skipped = 0
    errors: list = []

    try:
        for art in articles:
            if not art["article_id"]:
                skipped += 1
                continue
            try:
                cur.execute(
                    "SELECT article_id FROM dim_article WHERE article_id = %s",
                    (art["article_id"],),
                )
                exists = cur.fetchone()

                if exists:
                    set_parts = [
                        "article_name = %s", "article_type = %s",
                        "level1 = %s", "level2 = %s", "is_active = TRUE",
                    ]
                    set_vals = [
                        art["article_name"], art["article_type"],
                        art["level1"], art["level2"],
                    ]
                    if has_pnl and art["pnl_id"]:
                        set_parts.append("pnl_id = %s")
                        set_vals.append(art["pnl_id"])
                    for db_col in extra_mapped:
                        if extra_mapped[db_col]:
                            set_parts.append(f"{db_col} = %s")
                            set_vals.append(art.get(db_col) or "")

                    cur.execute(
                        f"UPDATE dim_article SET {', '.join(set_parts)} WHERE article_id = %s",
                        set_vals + [art["article_id"]],
                    )
                    updated += 1

                else:
                    ins_cols = ["article_id", "article_name", "article_type",
                                "level1", "level2", "is_active"]
                    ins_vals = [art["article_id"], art["article_name"], art["article_type"],
                                art["level1"], art["level2"], True]

                    if has_pnl and art["pnl_id"]:
                        ins_cols.append("pnl_id")
                        ins_vals.append(art["pnl_id"])
                    for db_col in extra_mapped:
                        if extra_mapped[db_col]:
                            ins_cols.append(db_col)
                            ins_vals.append(art.get(db_col) or "")

                    placeholders = ", ".join(["%s"] * len(ins_vals))
                    cur.execute(
                        f"INSERT INTO dim_article ({', '.join(ins_cols)}) "
                        f"VALUES ({placeholders})",
                        ins_vals,
                    )
                    imported += 1

            except Exception as row_err:
                skipped += 1
                errors.append({"article_id": art["article_id"], "error": str(row_err)})

        conn.commit()
        return {
            "status": "ok",
            "message": "Імпорт виконано",
            "target": "master",
            "source_name": source.get("source_name"),
            "total_rows": len(articles),
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:20],
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()
