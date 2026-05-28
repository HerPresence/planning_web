"""
Universal Import Engine -- service layer.

Handles: import_types, import_field_mapping, import_batches,
         staging_sales_fact, fact_turnover.

Existing PnL import (pnl_import.py) is untouched.
"""

import json
import re
import unicodedata
from datetime import date, datetime
from typing import Optional, List

from db import get_connection

# ---- Static catalogue -------------------------------------------------------

IMPORT_TYPES = [
    {"code": "pnl_plan",              "name": "План PnL",                      "target_table": "plan_pnl",            "staging_table": None},
    {"code": "pnl_fact",              "name": "Факт PnL",                      "target_table": "fact_pnl",            "staging_table": None},
    {"code": "sales_fact",            "name": "Факт продажів (товарооборот)",   "target_table": "fact_turnover",       "staging_table": "staging_sales_fact"},
    {"code": "departments",           "name": "Підрозділи",                     "target_table": "dim_department",      "staging_table": "staging_departments"},
    {"code": "brands",                "name": "Бренди / Номенклатурні групи",   "target_table": "dim_brand_source",    "staging_table": "staging_brands"},
    {"code": "sales_plan",            "name": "План товарообороту",             "target_table": "plan_turnover",       "staging_table": None},
    {"code": "expense_budget",        "name": "Бюджети витрат",                 "target_table": "budgets",             "staging_table": None},
    {"code": "articles",              "name": "Статті PnL",                     "target_table": "dim_article_source",  "staging_table": "staging_articles"},
    {"code": "article_mapping",       "name": "Відповідність статей",           "target_table": "article_mapping",     "staging_table": None},
    {"code": "commercial_conditions", "name": "Комерційні умови",               "target_table": "commercial_conditions","staging_table": None},
]

# Map import_type_code -> target_table
_TYPE_TARGET = {t["code"]: t["target_table"] for t in IMPORT_TYPES}
_TYPE_STAGING = {t["code"]: t["staging_table"] for t in IMPORT_TYPES if t["staging_table"]}

DEPARTMENTS_DEFAULT_FIELDS = [
    {"source_field": "UIDПідрозділ",               "target_field": "department_uid",            "required": True},
    {"source_field": "Підрозділ",                  "target_field": "department_name",           "required": True},
    {"source_field": "UIDОсновнийПідрозділ",       "target_field": "parent_department_uid",     "required": False},
    {"source_field": "ОсновнийПідрозділ",          "target_field": "parent_department_name",    "required": False},
    {"source_field": "UIDВідокремленийПідрозділ",  "target_field": "separated_department_uid",  "required": False},
    {"source_field": "ВідокремленийПідрозділ",     "target_field": "separated_department_name", "required": False},
    {"source_field": "Організація",                "target_field": "organization_name",         "required": False},
    {"source_field": "Філія",                      "target_field": "branch_name",               "required": False},
    {"source_field": "Регіон",                     "target_field": "region_name",               "required": False},
    {"source_field": "Холдинг",                    "target_field": "holding_name",              "required": False},
]

BRANDS_DEFAULT_FIELDS = [
    {"source_field": "UIDНоменклатурнаГрупаВитрат",        "target_field": "brand_uid",         "required": False},
    {"source_field": "НоменклатурнаГрупаВитрат",           "target_field": "brand_name",        "required": True},
    {"source_field": "UIDОсновнаНоменклатурнаГрупаВитрат", "target_field": "parent_brand_uid",  "required": False},
    {"source_field": "ОсновнаНоменклатурнаГрупаВитрат",    "target_field": "parent_brand_name", "required": False},
    {"source_field": "Level1",                              "target_field": "brand_group",       "required": False},
    {"source_field": "Level_1",                             "target_field": "source_level",      "required": False},
    {"source_field": "Company",                             "target_field": "source_company_name", "required": False},
    {"source_field": "valid",                               "target_field": "source_is_active",    "required": False},
    {"source_field": "brand_id",                            "target_field": "source_brand_ref_id", "required": False},
]

ARTICLES_DEFAULT_FIELDS = [
    {"source_field": "UIDСтаттяВитрат",                  "target_field": "article_uid",      "required": True},
    {"source_field": "СтаттяВитрат",                     "target_field": "article_name",     "required": True},
    {"source_field": "ТипСтатті",                        "target_field": "article_type",     "required": False},
    {"source_field": "Level1",                            "target_field": "level1",           "required": False},
    {"source_field": "Level2",                            "target_field": "level2",           "required": False},
    {"source_field": "КодPnL",                           "target_field": "pnl_code",         "required": False},
    {"source_field": "НоменклатурнаГрупаВитрат",        "target_field": "expense_element",  "required": False},
    {"source_field": "Компанія",                          "target_field": "expense_company",  "required": False},
]

DEFAULT_FIELDS_BY_TYPE = {
    "sales_fact":  None,   # filled below after SALES_FACT_DEFAULT_FIELDS
    "departments": DEPARTMENTS_DEFAULT_FIELDS,
    "brands":      BRANDS_DEFAULT_FIELDS,
    "articles":    ARTICLES_DEFAULT_FIELDS,
}

SALES_FACT_DEFAULT_FIELDS = [
    {"source_field": "Pidrozdil UIDPidrozdil",              "target_field": "department_uid",    "required": True},
    {"source_field": "Pidrozdil Pidrozdil",                 "target_field": "department_name",   "required": True},
    {"source_field": "NGV IDNomenklaturnaiaGruppaVytrat",   "target_field": "product_group_id",  "required": False},
    {"source_field": "NGV UIDNomenklaturnaGrupaVytrat",     "target_field": "product_group_uid", "required": True},
    {"source_field": "NGV NomenklaturnaGrupaVytrat",        "target_field": "product_group_name","required": True},
    {"source_field": "Kalendar Pochatok misiatsia",         "target_field": "period_month",      "required": True},
    {"source_field": "Prodazhi hrn z PDV",                  "target_field": "sales_vat",         "required": False},
    {"source_field": "Prodazhi hrn rozdribni",              "target_field": "sales_retail",      "required": False},
    {"source_field": "Aksyz hrn",                           "target_field": "excise",            "required": False},
    {"source_field": "Prodazhi dal",                        "target_field": "sales_dal",         "required": False},
    {"source_field": "Prodazhi kh",                         "target_field": "sales_kg",          "required": False},
]
DEFAULT_FIELDS_BY_TYPE["sales_fact"] = SALES_FACT_DEFAULT_FIELDS

# Canonical staging columns per import type.
# ONLY these keys may map to physical columns; all others go into extra_fields JSONB.
CANONICAL_STAGING_FIELDS: dict[str, frozenset] = {
    "departments": frozenset({
        "department_uid", "department_name", "organization_name",
        "branch_name", "region_name", "holding_name",
        "parent_department_uid", "parent_department_name",
        "separated_department_uid", "separated_department_name",
    }),
    "brands": frozenset({
        "brand_uid", "brand_name", "brand_group",
        "parent_brand_uid", "parent_brand_name",
        "source_level", "source_company_name", "source_is_active", "source_brand_ref_id",
        # accepted aliases — so user-typed targets don't get extra_fields yellow label
        "Level_1", "Company", "valid", "brand_id", "company_name", "is_active",
    }),
    "articles": frozenset({
        "article_uid", "article_name", "article_type",
        "level1", "level2", "pnl_code",
        "expense_element", "expense_company",
    }),
    "sales_fact": frozenset({
        "department_uid", "department_name", "product_group_id",
        "product_group_uid", "product_group_name", "period_month",
        "sales_vat", "sales_retail", "excise", "sales_dal", "sales_kg",
    }),
}

_INTERNAL_KEYS = frozenset({"validation_status", "validation_error", "raw_row", "batch_id"})


def _split_canonical_extra(mapped: dict, canonical: frozenset) -> tuple:
    """Split mapping result into (canonical_dict, extra_dict).

    canonical_dict  — keys in the staging table schema
    extra_dict      — everything else (stored in extra_fields JSONB, never ALTER TABLE)
    """
    extra = {k: v for k, v in mapped.items() if k not in canonical and k not in _INTERNAL_KEYS}
    return mapped, extra


# ---- Table setup ------------------------------------------------------------

def ensure_import_engine_tables():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "ALTER TABLE import_sources ADD COLUMN IF NOT EXISTS import_type_code TEXT DEFAULT NULL"
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS import_field_mapping (
                id               SERIAL PRIMARY KEY,
                import_source_id INTEGER NOT NULL,
                source_field     TEXT NOT NULL,
                target_field     TEXT NOT NULL,
                required         BOOLEAN DEFAULT FALSE,
                transform_rule   TEXT DEFAULT '',
                is_active        BOOLEAN DEFAULT TRUE,
                UNIQUE (import_source_id, source_field)
            )
        """)

        # Drop old constraint that prevents same source_field → multiple target_fields
        cur.execute("""
            ALTER TABLE import_field_mapping
            DROP CONSTRAINT IF EXISTS import_field_mapping_import_source_id_source_field_key
        """)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'import_field_mapping'::regclass
                    AND conname = 'uq_ifm_source_src_tgt'
                ) THEN
                    ALTER TABLE import_field_mapping
                    ADD CONSTRAINT uq_ifm_source_src_tgt
                    UNIQUE (import_source_id, source_field, target_field);
                END IF;
            END $$
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS import_batches (
                id                    SERIAL PRIMARY KEY,
                import_source_id      INTEGER NOT NULL,
                import_type_code      TEXT NOT NULL,
                target_table          TEXT,
                status                TEXT NOT NULL DEFAULT 'pending',
                replace_mode          TEXT NOT NULL DEFAULT 'replace_by_period',
                period_from           DATE,
                period_to             DATE,
                period_field          TEXT DEFAULT 'period_month',
                started_at            TIMESTAMP DEFAULT NOW(),
                finished_at           TIMESTAMP,
                rows_total            INTEGER DEFAULT 0,
                rows_filtered_out     INTEGER DEFAULT 0,
                rows_loaded           INTEGER DEFAULT 0,
                rows_failed           INTEGER DEFAULT 0,
                rows_valid            INTEGER DEFAULT 0,
                rows_invalid          INTEGER DEFAULT 0,
                rows_loaded_to_target INTEGER DEFAULT 0,
                error_message         TEXT,
                created_by            INTEGER
            )
        """)

        # Add new columns to existing import_batches (idempotent)
        for col_def in [
            "target_table          TEXT",
            "replace_mode          TEXT NOT NULL DEFAULT 'replace_by_period'",
            "period_from           DATE",
            "period_to             DATE",
            "period_field          TEXT DEFAULT 'period_month'",
            "rows_filtered_out     INTEGER DEFAULT 0",
            "rows_valid            INTEGER DEFAULT 0",
            "rows_invalid          INTEGER DEFAULT 0",
            "rows_loaded_to_target INTEGER DEFAULT 0",
        ]:
            col_name = col_def.split()[0]
            try:
                cur.execute(f"ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS {col_def}")
            except Exception:
                pass  # column may already exist with different definition

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging_sales_fact (
                id                SERIAL PRIMARY KEY,
                batch_id          INTEGER NOT NULL,
                period_month      DATE,
                department_uid    TEXT,
                department_name   TEXT,
                product_group_id  TEXT,
                product_group_uid TEXT,
                product_group_name TEXT,
                sales_vat         NUMERIC(18,4),
                sales_retail      NUMERIC(18,4),
                excise            NUMERIC(18,4),
                sales_dal         NUMERIC(18,4),
                sales_kg          NUMERIC(18,4),
                raw_row           JSONB,
                validation_status TEXT DEFAULT 'pending',
                validation_error  TEXT,
                created_at        TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            "ALTER TABLE staging_sales_fact ADD COLUMN IF NOT EXISTS raw_row JSONB"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_sf_batch ON staging_sales_fact (batch_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_sf_status ON staging_sales_fact (batch_id, validation_status)"
        )

        # Mapping columns added for master-reference bulk-update
        for _col in [
            "master_department_id   TEXT",
            "master_department_name TEXT",
            "master_brand_id        INTEGER",
            "master_brand_name      TEXT",
            "master_brand_uid       TEXT",
            "mapping_status         TEXT DEFAULT 'not_set'",
        ]:
            cur.execute(
                f"ALTER TABLE staging_sales_fact ADD COLUMN IF NOT EXISTS {_col}"
            )

        # Fix pre-existing deployments where master_department_id was INTEGER
        cur.execute(
            "ALTER TABLE staging_sales_fact "
            "ALTER COLUMN master_department_id TYPE TEXT USING master_department_id::text"
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sf_bulk_update_log (
                id             SERIAL PRIMARY KEY,
                batch_id       INTEGER,
                updated_by     INTEGER,
                target_field   TEXT,
                master_id      TEXT,
                master_value   TEXT,
                rows_updated   INTEGER DEFAULT 0,
                filter_summary JSONB,
                created_at     TIMESTAMP DEFAULT NOW()
            )
        """)
        # Fix pre-existing deployments where sf_bulk_update_log.master_id was INTEGER
        cur.execute(
            "ALTER TABLE sf_bulk_update_log "
            "ALTER COLUMN master_id TYPE TEXT USING master_id::text"
        )

        # staging_departments
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging_departments (
                id                       SERIAL PRIMARY KEY,
                batch_id                 INTEGER NOT NULL,
                import_type_code         TEXT DEFAULT 'departments',
                department_uid           TEXT,
                department_name          TEXT,
                organization_name        TEXT,
                branch_name              TEXT,
                region_name              TEXT,
                holding_name             TEXT,
                parent_department_uid    TEXT,
                parent_department_name   TEXT,
                separated_department_uid  TEXT,
                separated_department_name TEXT,
                raw_row                  JSONB,
                validation_status        TEXT DEFAULT 'pending',
                validation_error         TEXT,
                created_at               TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_dept_batch ON staging_departments (batch_id)"
        )

        # staging_brands
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging_brands (
                id                SERIAL PRIMARY KEY,
                batch_id          INTEGER NOT NULL,
                import_type_code  TEXT DEFAULT 'brands',
                brand_uid         TEXT,
                brand_name        TEXT,
                brand_group       TEXT,
                parent_brand_uid  TEXT,
                parent_brand_name TEXT,
                raw_row           JSONB,
                validation_status TEXT DEFAULT 'pending',
                validation_error  TEXT,
                created_at        TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_brands_batch ON staging_brands (batch_id)"
        )
        for _col, _typ in [
            ("source_level",        "TEXT"),
            ("source_company_name", "TEXT"),
            ("source_is_active",    "TEXT"),
            ("source_brand_ref_id", "TEXT"),
        ]:
            cur.execute(
                f"ALTER TABLE staging_brands ADD COLUMN IF NOT EXISTS {_col} {_typ}"
            )

        # staging_articles
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging_articles (
                id                SERIAL PRIMARY KEY,
                batch_id          INTEGER NOT NULL,
                import_type_code  TEXT DEFAULT 'articles',
                article_uid       TEXT,
                article_name      TEXT,
                article_type      TEXT,
                level1            TEXT,
                level2            TEXT,
                pnl_code          TEXT,
                expense_element   TEXT,
                expense_company   TEXT,
                raw_row           JSONB,
                validation_status TEXT DEFAULT 'pending',
                validation_error  TEXT,
                created_at        TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_art_batch ON staging_articles (batch_id)"
        )

        # Add extra_fields JSONB to all staging tables (idempotent)
        for _stg in ("staging_departments", "staging_brands",
                     "staging_articles", "staging_sales_fact"):
            cur.execute(
                f"ALTER TABLE {_stg} ADD COLUMN IF NOT EXISTS extra_fields JSONB"
            )

        # ── Phase 0: Brand source registry (no data migration, no UI yet) ────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_brand_source (
                id                 SERIAL PRIMARY KEY,
                source_id          INTEGER NOT NULL,
                source_name        TEXT    DEFAULT '',
                source_brand_id    TEXT    NOT NULL,
                source_brand_name  TEXT    DEFAULT '',
                source_brand_group TEXT    DEFAULT '',
                source_parent_uid  TEXT    DEFAULT '',
                source_parent_name TEXT    DEFAULT '',
                extra_fields       JSONB,
                loaded_at          TIMESTAMP DEFAULT NOW(),
                is_active          BOOLEAN   DEFAULT TRUE,
                UNIQUE (source_id, source_brand_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brand_source_mapping (
                id               SERIAL PRIMARY KEY,
                source_id        INTEGER NOT NULL,
                source_brand_id  TEXT    NOT NULL,
                master_brand_id  INTEGER,
                mapping_status   TEXT    DEFAULT 'pending',
                confidence       NUMERIC(5,2) DEFAULT 0,
                mapped_by        INTEGER,
                created_at       TIMESTAMP DEFAULT NOW(),
                updated_at       TIMESTAMP DEFAULT NOW(),
                UNIQUE (source_id, source_brand_id)
            )
        """)
        for _idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_dbs_source_id        ON dim_brand_source(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_dbs_source_brand_id  ON dim_brand_source(source_brand_id)",
            "CREATE INDEX IF NOT EXISTS idx_dbs_is_active        ON dim_brand_source(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_bsm_source_id        ON brand_source_mapping(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_bsm_mapping_status   ON brand_source_mapping(mapping_status)",
            "CREATE INDEX IF NOT EXISTS idx_bsm_master_brand_id  ON brand_source_mapping(master_brand_id)",
        ):
            cur.execute(_idx_sql)

        # Ensure dim_brand has UNIQUE brand_uid + parent columns
        cur.execute(
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS parent_brand_uid  TEXT"
        )
        cur.execute(
            "ALTER TABLE dim_brand ADD COLUMN IF NOT EXISTS parent_brand_name TEXT"
        )
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'dim_brand'::regclass
                    AND conname = 'dim_brand_brand_uid_key'
                ) THEN
                    ALTER TABLE dim_brand ADD CONSTRAINT dim_brand_brand_uid_key
                        UNIQUE (brand_uid);
                END IF;
            END $$
        """)

        # ── Department source registry ─────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_department_source (
                id                               SERIAL PRIMARY KEY,
                source_id                        INTEGER NOT NULL,
                source_name                      TEXT    DEFAULT '',
                source_department_id             TEXT    NOT NULL,
                source_department_name           TEXT    DEFAULT '',
                source_parent_department_id      TEXT    DEFAULT '',
                source_parent_department_name    TEXT    DEFAULT '',
                source_separated_department_id   TEXT    DEFAULT '',
                source_separated_department_name TEXT    DEFAULT '',
                organization_name                TEXT    DEFAULT '',
                branch_name                      TEXT    DEFAULT '',
                region_name                      TEXT    DEFAULT '',
                holding_name                     TEXT    DEFAULT '',
                extra_fields                     JSONB,
                loaded_at                        TIMESTAMP DEFAULT NOW(),
                is_active                        BOOLEAN   DEFAULT TRUE,
                UNIQUE (source_id, source_department_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS department_source_mapping (
                id                   SERIAL PRIMARY KEY,
                source_id            INTEGER NOT NULL,
                source_department_id TEXT    NOT NULL,
                master_department_id TEXT,
                mapping_status       TEXT    DEFAULT 'pending',
                confidence           NUMERIC(5,2) DEFAULT 0,
                mapped_by            INTEGER,
                created_at           TIMESTAMP DEFAULT NOW(),
                updated_at           TIMESTAMP DEFAULT NOW(),
                UNIQUE (source_id, source_department_id)
            )
        """)
        for _idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_dds_source_id         ON dim_department_source(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_dds_source_dept_id    ON dim_department_source(source_department_id)",
            "CREATE INDEX IF NOT EXISTS idx_dsm_source_id         ON department_source_mapping(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_dsm_mapping_status    ON department_source_mapping(mapping_status)",
            "CREATE INDEX IF NOT EXISTS idx_dsm_master_dept_id    ON department_source_mapping(master_department_id)",
        ):
            cur.execute(_idx_sql)

        # Add change-tracking columns to dim_department_source (idempotent)
        for _col_def in [
            "source_changed   BOOLEAN DEFAULT FALSE",
            "changed_fields   JSONB",
            "previous_snapshot JSONB",
            "last_batch_id    INTEGER",
            "seen_count       INTEGER DEFAULT 1",
            "last_seen_at     TIMESTAMP",
        ]:
            cur.execute(
                f"ALTER TABLE dim_department_source ADD COLUMN IF NOT EXISTS {_col_def}"
            )

        # Add parent + is_deleted columns to dim_department
        for _col, _typ in [
            ("parent_department_id",   "TEXT"),
            ("parent_department_name", "TEXT"),
            ("is_deleted",             "BOOLEAN DEFAULT FALSE"),
        ]:
            cur.execute(f"ALTER TABLE dim_department ADD COLUMN IF NOT EXISTS {_col} {_typ}")

        # staging_table column on import_batches
        cur.execute(
            "ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS staging_table TEXT"
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_turnover (
                id                SERIAL PRIMARY KEY,
                period_month      DATE NOT NULL,
                department_uid    TEXT,
                department_name   TEXT,
                product_group_id  TEXT,
                product_group_uid TEXT,
                product_group_name TEXT,
                sales_vat         NUMERIC(18,4),
                sales_retail      NUMERIC(18,4),
                excise            NUMERIC(18,4),
                sales_dal         NUMERIC(18,4),
                sales_kg          NUMERIC(18,4),
                source_id         INTEGER,
                batch_id          INTEGER,
                created_at        TIMESTAMP DEFAULT NOW(),
                UNIQUE (period_month, department_uid, product_group_uid, source_id)
            )
        """)

        conn.commit()
        print("[startup] ensure_import_engine_tables: done")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_import_engine_tables failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


# ---- Helpers ----------------------------------------------------------------

_WS_RE = re.compile(r'\s+')


def _normalize_field_name(s) -> str:
    """NFC + strip brackets + collapse whitespace + lowercase."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    s = re.sub(r'[\[\]]', '', s)
    s = _WS_RE.sub(' ', s)
    return s.strip().lower()


def _parse_date(val) -> Optional[date]:
    if val is None:
        return None
    s = str(val).strip()
    for pattern, fmt in [
        (r"(\d{4})-(\d{2})-(\d{2})", lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", lambda m: date(int(m[3]), int(m[2]), int(m[1]))),
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
    ]:
        m = re.match(pattern, s)
        if m:
            try:
                return fmt(m)
            except ValueError:
                pass
    return None


def _parse_num(val, field_name: str = "") -> tuple:
    """
    Parse numeric from OLAP. Returns (float, error_or_None).
    Fast path for int/float (PowerShell JSON numbers).
    """
    if val is None:
        return 0.0, None
    if isinstance(val, (int, float)):
        return float(val), None
    try:
        from decimal import Decimal
        if isinstance(val, Decimal):
            return float(val), None
    except ImportError:
        pass
    s = str(val).strip()
    if s in ("", "-", "–", "—", "null", "None", "NULL", "n/a", "N/A"):
        return 0.0, None
    s = re.sub(r'\s', '', s)
    s = s.replace(',', '.')
    if s.count('.') > 1:
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    try:
        return float(s), None
    except (ValueError, TypeError):
        err = "Cannot parse number '{}'".format(val)
        if field_name:
            err += " (field: {})".format(field_name)
        return 0.0, err


def _apply_mapping(row: dict, field_mapping: list) -> tuple:
    """Exact match first, then normalized fallback. Returns (mapped_dict, debug)."""
    norm_index = {_normalize_field_name(k): k for k in row}
    mapped = {}
    debug = {"exact": [], "normalized": [], "not_found": []}

    for fm in field_mapping:
        src, tgt = fm["source_field"], fm["target_field"]
        if src in row:
            mapped[tgt] = row[src]
            debug["exact"].append(src)
        else:
            actual = norm_index.get(_normalize_field_name(src))
            if actual is not None:
                mapped[tgt] = row[actual]
                debug["normalized"].append("{!r} -> {!r}".format(src, actual))
            else:
                mapped[tgt] = None
                debug["not_found"].append(src)

    if debug["not_found"]:
        print("[mapping] NOT FOUND: {}".format(debug["not_found"]))
    if debug["normalized"]:
        print("[mapping] Normalized: {}".format(debug["normalized"]))
    return mapped, debug


# ---- Field mapping CRUD -----------------------------------------------------

def get_field_mapping(source_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, source_field, target_field, required, transform_rule, is_active
               FROM import_field_mapping
               WHERE import_source_id = %s AND is_active = TRUE ORDER BY id""",
            (source_id,),
        )
        return [
            {"id": r[0], "source_field": r[1], "target_field": r[2],
             "required": r[3], "transform_rule": r[4] or "", "is_active": r[5]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


def save_field_mapping(source_id: int, mappings: list):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM import_field_mapping WHERE import_source_id = %s", (source_id,))
        for m in mappings:
            cur.execute(
                """INSERT INTO import_field_mapping
                   (import_source_id, source_field, target_field, required, transform_rule, is_active)
                   VALUES (%s, %s, %s, %s, %s, TRUE)""",
                (source_id, m["source_field"], m["target_field"],
                 bool(m.get("required", False)), m.get("transform_rule", "")),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ---- Batch CRUD -------------------------------------------------------------

def create_batch(
    source_id: int,
    import_type_code: str,
    created_by: Optional[int] = None,
    period_from: Optional[date] = None,
    period_to: Optional[date] = None,
    period_field: str = "period_month",
    replace_mode: str = "replace_by_period",
) -> int:
    conn = get_connection()
    cur = conn.cursor()
    try:
        target_table = _TYPE_TARGET.get(import_type_code, "")
        cur.execute(
            """INSERT INTO import_batches
               (import_source_id, import_type_code, target_table, status,
                period_from, period_to, period_field, replace_mode, created_by)
               VALUES (%s, %s, %s, 'loading', %s, %s, %s, %s, %s) RETURNING id""",
            (source_id, import_type_code, target_table,
             period_from, period_to, period_field, replace_mode, created_by),
        )
        batch_id = cur.fetchone()[0]
        conn.commit()
        return batch_id
    finally:
        cur.close()
        conn.close()


def update_batch(batch_id: int, **kwargs):
    if not kwargs:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        sets = ", ".join("{} = %s".format(k) for k in kwargs)
        vals = list(kwargs.values()) + [batch_id]
        cur.execute("UPDATE import_batches SET {} WHERE id = %s".format(sets), vals)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_batch(batch_id: int) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT b.id, b.import_source_id, s.source_name, b.import_type_code,
                      b.target_table, b.status, b.replace_mode,
                      b.period_from, b.period_to, b.period_field,
                      b.started_at, b.finished_at,
                      b.rows_total, b.rows_filtered_out, b.rows_loaded,
                      b.rows_failed, b.rows_valid, b.rows_invalid,
                      b.rows_loaded_to_target, b.error_message, b.created_by
               FROM import_batches b
               LEFT JOIN import_sources s ON s.id = b.import_source_id
               WHERE b.id = %s""",
            (batch_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        return _batch_row_to_dict(r)
    finally:
        cur.close()
        conn.close()


def _batch_row_to_dict(r) -> dict:
    return {
        "id": r[0], "source_id": r[1], "source_name": r[2],
        "import_type_code": r[3], "target_table": r[4],
        "status": r[5], "replace_mode": r[6],
        "period_from": str(r[7]) if r[7] else None,
        "period_to":   str(r[8]) if r[8] else None,
        "period_field": r[9],
        "started_at":   str(r[10]) if r[10] else None,
        "finished_at":  str(r[11]) if r[11] else None,
        "rows_total": r[12], "rows_filtered_out": r[13],
        "rows_loaded": r[14], "rows_failed": r[15],
        "rows_valid": r[16], "rows_invalid": r[17],
        "rows_loaded_to_target": r[18],
        "error_message": r[19], "created_by": r[20],
    }


def get_batches(limit: int = 50) -> list:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT b.id, b.import_source_id, s.source_name, b.import_type_code,
                      b.target_table, b.status, b.replace_mode,
                      b.period_from, b.period_to, b.period_field,
                      b.started_at, b.finished_at,
                      b.rows_total, b.rows_filtered_out, b.rows_loaded,
                      b.rows_failed, b.rows_valid, b.rows_invalid,
                      b.rows_loaded_to_target, b.error_message, b.created_by
               FROM import_batches b
               LEFT JOIN import_sources s ON s.id = b.import_source_id
               ORDER BY b.id DESC LIMIT %s""",
            (limit,),
        )
        return [_batch_row_to_dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ---- Sales Fact staging -----------------------------------------------------

def load_sales_fact_to_staging(
    batch_id: int,
    rows: list,
    field_mapping: list,
    period_from: Optional[date] = None,
    period_to: Optional[date] = None,
) -> tuple:
    """
    Apply mapping, filter by period, parse, write to staging_sales_fact.
    Returns (rows_loaded, rows_failed, rows_filtered_out, rows_valid, rows_invalid).
    """
    if not rows:
        return 0, 0, 0, 0, 0

    sample_keys = list(rows[0].keys())
    print("[load_staging] Source columns ({}): {}".format(len(sample_keys), sample_keys))
    print("[load_staging] Mapping ({} rules)".format(len(field_mapping)))
    if period_from or period_to:
        print("[load_staging] Period filter: {} .. {}".format(period_from, period_to))

    numeric_targets = {"sales_vat", "sales_retail", "excise", "sales_dal", "sales_kg"}
    for i, row in enumerate(rows[:3]):
        for fm in field_mapping:
            if fm["target_field"] in numeric_targets:
                raw_val = row.get(fm["source_field"])
                print("[load_staging] row#{} {!r} = {!r} (type={})".format(
                    i, fm["source_field"], raw_val, type(raw_val).__name__))

    conn = get_connection()
    cur = conn.cursor()
    rows_loaded = rows_failed = rows_filtered_out = rows_valid = rows_invalid = 0

    try:
        for i, row in enumerate(rows):
            mapped, _debug = _apply_mapping(row, field_mapping)

            # Parse period first — needed for the period filter
            period = _parse_date(mapped.get("period_month"))

            # Period filter: skip rows outside the requested range
            if period and (period_from or period_to):
                if period_from and period < period_from:
                    rows_filtered_out += 1
                    continue
                if period_to and period > period_to:
                    rows_filtered_out += 1
                    continue

            errors = []

            if not period:
                errors.append("Cannot parse period_month: '{}'".format(
                    mapped.get("period_month")))

            dept_uid  = str(mapped.get("department_uid") or "").strip()
            dept_name = str(mapped.get("department_name") or "").strip()
            pg_id     = str(mapped.get("product_group_id") or "").strip()
            pg_uid    = str(mapped.get("product_group_uid") or "").strip()
            pg_name   = str(mapped.get("product_group_name") or "").strip()

            if not dept_uid:
                errors.append("Empty department_uid")
            if not pg_uid:
                errors.append("Empty product_group_uid")

            sales_vat,    e1 = _parse_num(mapped.get("sales_vat"),    "sales_vat")
            sales_retail, e2 = _parse_num(mapped.get("sales_retail"), "sales_retail")
            excise,       e3 = _parse_num(mapped.get("excise"),       "excise")
            sales_dal,    e4 = _parse_num(mapped.get("sales_dal"),    "sales_dal")
            sales_kg,     e5 = _parse_num(mapped.get("sales_kg"),     "sales_kg")
            for e in (e1, e2, e3, e4, e5):
                if e:
                    errors.append(e)

            v_status = "invalid" if errors else "valid"
            v_error  = "; ".join(errors) if errors else None
            if v_status == "valid":
                rows_valid += 1
            else:
                rows_invalid += 1

            _, extra = _split_canonical_extra(mapped, CANONICAL_STAGING_FIELDS["sales_fact"])

            try:
                raw_json = json.dumps(row, default=str, ensure_ascii=False)
            except Exception:
                raw_json = None
            extra_json = json.dumps(extra, default=str) if extra else None

            try:
                cur.execute(
                    """INSERT INTO staging_sales_fact
                       (batch_id, period_month, department_uid, department_name,
                        product_group_id, product_group_uid, product_group_name,
                        sales_vat, sales_retail, excise, sales_dal, sales_kg,
                        raw_row, extra_fields, validation_status, validation_error)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                    (batch_id, period, dept_uid, dept_name, pg_id, pg_uid, pg_name,
                     sales_vat, sales_retail, excise, sales_dal, sales_kg,
                     raw_json, extra_json, v_status, v_error),
                )
                rows_loaded += 1
            except Exception as exc:
                rows_failed += 1
                print("[staging] row #{} INSERT error: {}".format(i, exc))

        conn.commit()
        print("[load_staging] Done: loaded={}, valid={}, invalid={}, filtered_out={}, failed={}".format(
            rows_loaded, rows_valid, rows_invalid, rows_filtered_out, rows_failed))
    finally:
        cur.close()
        conn.close()

    return rows_loaded, rows_failed, rows_filtered_out, rows_valid, rows_invalid


def get_staging_preview(batch_id: int, limit: int = 500,
                        status_filter: Optional[str] = None) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE validation_status = 'valid'),
                COUNT(*) FILTER (WHERE validation_status = 'invalid'),
                MIN(period_month), MAX(period_month),
                COALESCE(SUM(sales_vat)    FILTER (WHERE validation_status = 'valid'), 0),
                COALESCE(SUM(sales_retail) FILTER (WHERE validation_status = 'valid'), 0),
                COALESCE(SUM(excise)       FILTER (WHERE validation_status = 'valid'), 0),
                COALESCE(SUM(sales_dal)    FILTER (WHERE validation_status = 'valid'), 0),
                COALESCE(SUM(sales_kg)     FILTER (WHERE validation_status = 'valid'), 0)
               FROM staging_sales_fact WHERE batch_id = %s""",
            (batch_id,),
        )
        agg = cur.fetchone()

        where_extra = ""
        params = [batch_id]
        if status_filter in ("valid", "invalid"):
            where_extra = " AND validation_status = %s"
            params.append(status_filter)
        params.append(limit)

        cur.execute(
            """SELECT id, period_month, department_uid, department_name,
                       product_group_uid, product_group_name,
                       sales_vat, sales_retail, excise, sales_dal, sales_kg,
                       validation_status, validation_error, raw_row,
                       master_department_id, master_department_name,
                       master_brand_id, master_brand_name, master_brand_uid,
                       mapping_status
                FROM staging_sales_fact
                WHERE batch_id = %s{extra}
                ORDER BY validation_status DESC, id
                LIMIT %s""".format(extra=where_extra),
            params,
        )
        rows = []
        for r in cur.fetchall():
            rows.append({
                "id": r[0],
                "period_month":       str(r[1])  if r[1]  else None,
                "department_uid":     r[2],
                "department_name":    r[3],
                "product_group_uid":  r[4],
                "product_group_name": r[5],
                "sales_vat":    float(r[6])  if r[6]  is not None else None,
                "sales_retail": float(r[7])  if r[7]  is not None else None,
                "excise":       float(r[8])  if r[8]  is not None else None,
                "sales_dal":    float(r[9])  if r[9]  is not None else None,
                "sales_kg":     float(r[10]) if r[10] is not None else None,
                "validation_status":      r[11],
                "validation_error":       r[12],
                "raw_row":                r[13],
                "master_department_id":   r[14],
                "master_department_name": r[15],
                "master_brand_id":        r[16],
                "master_brand_name":      r[17],
                "master_brand_uid":       r[18],
                "mapping_status":         r[19] or "not_set",
            })

        return {
            "total": int(agg[0]), "valid": int(agg[1]), "invalid": int(agg[2]),
            "period_from": str(agg[3]) if agg[3] else None,
            "period_to":   str(agg[4]) if agg[4] else None,
            "total_sales_vat":    float(agg[5]),
            "total_sales_retail": float(agg[6]),
            "total_excise":       float(agg[7]),
            "total_sales_dal":    float(agg[8]),
            "total_sales_kg":     float(agg[9]),
            "rows": rows,
        }
    finally:
        cur.close()
        conn.close()


def commit_sales_fact(batch_id: int, source_id: int,
                      period_from: Optional[date] = None,
                      period_to: Optional[date] = None) -> tuple:
    """
    Move valid staging rows -> fact_turnover.
    Deletes existing rows for source_id WHERE period_month BETWEEN period_from AND period_to.
    Returns (committed_count, deleted_from_target).
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        deleted_from_target = 0

        if period_from and period_to:
            # Replace only the specified period for this source
            cur.execute(
                "DELETE FROM fact_turnover WHERE source_id = %s AND period_month BETWEEN %s AND %s",
                (source_id, period_from, period_to),
            )
            deleted_from_target = cur.rowcount
            print("[commit] Deleted {} rows from fact_turnover (source={}, {} .. {})".format(
                deleted_from_target, source_id, period_from, period_to))
        else:
            # No period specified: replace by distinct months present in staging
            cur.execute(
                "SELECT DISTINCT period_month FROM staging_sales_fact"
                " WHERE batch_id = %s AND validation_status = 'valid'",
                (batch_id,),
            )
            periods = [r[0] for r in cur.fetchall()]
            if periods:
                cur.execute(
                    "DELETE FROM fact_turnover WHERE source_id = %s AND period_month = ANY(%s)",
                    (source_id, periods),
                )
                deleted_from_target = cur.rowcount

        cur.execute(
            """INSERT INTO fact_turnover
               (period_month, department_uid, department_name,
                product_group_id, product_group_uid, product_group_name,
                sales_vat, sales_retail, excise, sales_dal, sales_kg,
                source_id, batch_id)
               SELECT period_month, department_uid, department_name,
                      product_group_id, product_group_uid, product_group_name,
                      sales_vat, sales_retail, excise, sales_dal, sales_kg,
                      %s, batch_id
               FROM staging_sales_fact
               WHERE batch_id = %s AND validation_status = 'valid'
               ON CONFLICT (period_month, department_uid, product_group_uid, source_id)
               DO UPDATE SET
                   department_name    = EXCLUDED.department_name,
                   product_group_id   = EXCLUDED.product_group_id,
                   product_group_name = EXCLUDED.product_group_name,
                   sales_vat          = EXCLUDED.sales_vat,
                   sales_retail       = EXCLUDED.sales_retail,
                   excise             = EXCLUDED.excise,
                   sales_dal          = EXCLUDED.sales_dal,
                   sales_kg           = EXCLUDED.sales_kg,
                   batch_id           = EXCLUDED.batch_id,
                   created_at         = NOW()""",
            (source_id, batch_id),
        )
        committed = cur.rowcount
        conn.commit()
        print("[commit] Committed {} rows to fact_turnover (batch={})".format(committed, batch_id))
        return committed, deleted_from_target
    finally:
        cur.close()
        conn.close()


def delete_batch(batch_id: int, source_id: int,
                 delete_fact: bool = False,
                 period_from: Optional[date] = None,
                 period_to: Optional[date] = None) -> dict:
    """
    Delete batch record + staging rows.
    If delete_fact=True, also delete from fact_turnover for the batch's period+source.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        deleted_fact = 0
        if delete_fact:
            if period_from and period_to:
                cur.execute(
                    "DELETE FROM fact_turnover WHERE source_id = %s AND period_month BETWEEN %s AND %s",
                    (source_id, period_from, period_to),
                )
                deleted_fact = cur.rowcount
            else:
                # Fallback: delete by periods present in staging
                cur.execute(
                    "SELECT DISTINCT period_month FROM staging_sales_fact WHERE batch_id = %s",
                    (batch_id,),
                )
                periods = [r[0] for r in cur.fetchall()]
                if periods:
                    cur.execute(
                        "DELETE FROM fact_turnover WHERE source_id = %s AND period_month = ANY(%s)",
                        (source_id, periods),
                    )
                    deleted_fact = cur.rowcount

        cur.execute("DELETE FROM staging_sales_fact WHERE batch_id = %s", (batch_id,))
        deleted_staging = cur.rowcount
        cur.execute("DELETE FROM import_batches WHERE id = %s", (batch_id,))
        conn.commit()

        return {
            "ok": True,
            "batch_id": batch_id,
            "deleted_staging": deleted_staging,
            "deleted_fact": deleted_fact,
        }
    except Exception as exc:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def bulk_update_staging_sales_fact(
    batch_id: int,
    filters: dict,
    target_field: str,
    master_id: str,
    updated_by: Optional[int] = None,
) -> dict:
    """
    Bulk-assign master department or brand to staging rows matched by filters.
    Re-validates affected rows and clears resolved errors.
    Returns {rows_updated, rows_revalidated, rows_now_valid}.
    """
    if target_field not in ("department", "brand"):
        raise ValueError(f"Unknown target_field: {target_field}")

    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Resolve master record
        if target_field == "department":
            cur.execute(
                "SELECT department_id, department_name FROM dim_department WHERE department_id = %s",
                (master_id,),
            )
            rec = cur.fetchone()
            if not rec:
                raise ValueError(f"Department #{master_id} not found")
            dept_id, dept_name = rec
        else:
            cur.execute(
                "SELECT id, brand_uid, brand_name FROM dim_brand WHERE id = %s AND is_active = true",
                (master_id,),
            )
            rec = cur.fetchone()
            if not rec:
                raise ValueError(f"Brand #{master_id} not found or inactive")
            brand_id, brand_uid, brand_name = rec

        # 2. Build WHERE clause from filters
        conds  = ["batch_id = %s"]
        params = [batch_id]

        status = filters.get("status")
        if status in ("valid", "invalid"):
            conds.append("validation_status = %s")
            params.append(status)

        pf = filters.get("period_from")
        if pf:
            conds.append("period_month >= %s")
            params.append(pf)

        pt = filters.get("period_to")
        if pt:
            conds.append("period_month <= %s")
            params.append(pt)

        dept_filter = filters.get("department_name")
        if dept_filter:
            conds.append("department_name ILIKE %s")
            params.append(f"%{dept_filter}%")

        pg_filter = filters.get("product_group_name")
        if pg_filter:
            conds.append("product_group_name ILIKE %s")
            params.append(f"%{pg_filter}%")

        search = filters.get("search")
        if search:
            conds.append("(department_name ILIKE %s OR product_group_name ILIKE %s)")
            params += [f"%{search}%", f"%{search}%"]

        where = " AND ".join(conds)

        # 3. Get affected row IDs + current validation state
        cur.execute(
            f"SELECT id, validation_error, validation_status FROM staging_sales_fact WHERE {where}",
            params,
        )
        affected = cur.fetchall()
        if not affected:
            return {"rows_updated": 0, "rows_revalidated": 0, "rows_now_valid": 0}

        row_ids = [r[0] for r in affected]

        # 4. Apply master mapping
        if target_field == "department":
            cur.execute(
                """UPDATE staging_sales_fact
                   SET master_department_id   = %s,
                       master_department_name = %s,
                       department_name        = %s,
                       mapping_status         = 'manual'
                   WHERE id = ANY(%s)""",
                (dept_id, dept_name, dept_name, row_ids),
            )
        else:
            if brand_uid:
                cur.execute(
                    """UPDATE staging_sales_fact
                       SET master_brand_id   = %s,
                           master_brand_name = %s,
                           master_brand_uid  = %s,
                           product_group_uid  = %s,
                           product_group_name = %s,
                           mapping_status     = 'manual'
                       WHERE id = ANY(%s)""",
                    (brand_id, brand_name, brand_uid, brand_uid, brand_name, row_ids),
                )
            else:
                cur.execute(
                    """UPDATE staging_sales_fact
                       SET master_brand_id    = %s,
                           master_brand_name  = %s,
                           master_brand_uid   = %s,
                           product_group_name = %s,
                           mapping_status     = 'manual'
                       WHERE id = ANY(%s)""",
                    (brand_id, brand_name, brand_uid, brand_name, row_ids),
                )

        rows_updated = cur.rowcount

        # 5. Re-validate affected rows
        # Fetch fresh mapping state after update
        cur.execute(
            """SELECT id, validation_error, validation_status,
                      master_department_id, master_brand_id,
                      product_group_uid
               FROM staging_sales_fact WHERE id = ANY(%s)""",
            (row_ids,),
        )
        fresh_rows = cur.fetchall()

        rows_now_valid = 0
        for fr in fresh_rows:
            rid, verr, vstatus, mdept_id, mbrand_id, pg_uid = fr
            errs = [e.strip() for e in (verr or "").split(";") if e.strip()]

            if mdept_id is not None:
                errs = [e for e in errs if "department_uid" not in e.lower()]

            if mbrand_id is not None:
                errs = [e for e in errs if "product_group_uid" not in e.lower()]

            new_status = "invalid" if errs else "valid"
            new_error  = "; ".join(errs) if errs else None

            cur.execute(
                "UPDATE staging_sales_fact SET validation_status = %s, validation_error = %s WHERE id = %s",
                (new_status, new_error, rid),
            )
            if new_status == "valid":
                rows_now_valid += 1

        # 6. Audit log
        cur.execute(
            """INSERT INTO sf_bulk_update_log
               (batch_id, updated_by, target_field, master_id, master_value,
                rows_updated, filter_summary)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (
                batch_id, updated_by, target_field, master_id,
                dept_name if target_field == "department" else brand_name,
                rows_updated,
                json.dumps(filters, default=str, ensure_ascii=False),
            ),
        )

        # 7. Refresh batch counters
        cur.execute(
            """UPDATE import_batches SET
               rows_valid   = (SELECT COUNT(*) FROM staging_sales_fact
                               WHERE batch_id = %s AND validation_status = 'valid'),
               rows_invalid = (SELECT COUNT(*) FROM staging_sales_fact
                               WHERE batch_id = %s AND validation_status = 'invalid')
               WHERE id = %s""",
            (batch_id, batch_id, batch_id),
        )

        conn.commit()
        return {
            "rows_updated":    rows_updated,
            "rows_revalidated": len(fresh_rows),
            "rows_now_valid":  rows_now_valid,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_fact_turnover(
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    source_id: Optional[int] = None,
    limit: int = 5000,
) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        conditions, params = [], []
        if period_from:
            conditions.append("period_month >= %s")
            params.append(period_from)
        if period_to:
            conditions.append("period_month <= %s")
            params.append(period_to)
        if source_id:
            conditions.append("source_id = %s")
            params.append(source_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(
            """SELECT ft.id, ft.period_month, ft.department_uid, ft.department_name,
                       ft.product_group_uid, ft.product_group_name,
                       ft.sales_vat, ft.sales_retail, ft.excise, ft.sales_dal, ft.sales_kg,
                       ft.source_id, s.source_name, ft.batch_id
                FROM fact_turnover ft
                LEFT JOIN import_sources s ON s.id = ft.source_id
                {where}
                ORDER BY ft.period_month DESC, ft.department_name, ft.product_group_name
                LIMIT %s""".format(where=where),
            params + [limit],
        )
        rows = []
        for r in cur.fetchall():
            rows.append({
                "id": r[0],
                "period_month":       str(r[1]) if r[1] else None,
                "department_uid":     r[2],
                "department_name":    r[3],
                "product_group_uid":  r[4],
                "product_group_name": r[5],
                "sales_vat":    float(r[6])  if r[6]  is not None else 0,
                "sales_retail": float(r[7])  if r[7]  is not None else 0,
                "excise":       float(r[8])  if r[8]  is not None else 0,
                "sales_dal":    float(r[9])  if r[9]  is not None else 0,
                "sales_kg":     float(r[10]) if r[10] is not None else 0,
                "source_id":   r[11],
                "source_name": r[12],
                "batch_id":    r[13],
            })

        cur.execute(
            """SELECT COUNT(*),
                       COALESCE(SUM(sales_vat), 0),
                       COALESCE(SUM(sales_retail), 0),
                       COALESCE(SUM(excise), 0),
                       COALESCE(SUM(sales_dal), 0),
                       COALESCE(SUM(sales_kg), 0),
                       MIN(period_month), MAX(period_month)
                FROM fact_turnover ft {where}""".format(where=where),
            params,
        )
        agg = cur.fetchone()
        return {
            "rows": rows,
            "total_count": int(agg[0]),
            "total_sales_vat":    float(agg[1]),
            "total_sales_retail": float(agg[2]),
            "total_excise":       float(agg[3]),
            "total_sales_dal":    float(agg[4]),
            "total_sales_kg":     float(agg[5]),
            "period_from": str(agg[6]) if agg[6] else None,
            "period_to":   str(agg[7]) if agg[7] else None,
        }
    finally:
        cur.close()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# DEPARTMENTS IMPORT HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def load_departments_to_staging(batch_id: int, rows: list, field_mapping: list) -> tuple:
    """Apply mapping + validate + write to staging_departments.
    Returns (rows_loaded, rows_failed, rows_valid, rows_invalid).
    """
    if not rows:
        return 0, 0, 0, 0

    conn = get_connection()
    cur = conn.cursor()
    rows_loaded = rows_failed = rows_valid = rows_invalid = 0

    try:
        for i, row in enumerate(rows):
            mapped, _ = _apply_mapping(row, field_mapping)
            _, extra = _split_canonical_extra(mapped, CANONICAL_STAGING_FIELDS["departments"])

            dept_uid  = str(mapped.get("department_uid")  or "").strip()
            dept_name = str(mapped.get("department_name") or "").strip()
            org_name  = str(mapped.get("organization_name") or "").strip()
            branch    = str(mapped.get("branch_name")  or "").strip()
            region    = str(mapped.get("region_name")  or "").strip()
            holding   = str(mapped.get("holding_name") or "").strip()
            p_uid     = str(mapped.get("parent_department_uid")  or "").strip()
            p_name    = str(mapped.get("parent_department_name") or "").strip()
            s_uid     = str(mapped.get("separated_department_uid")  or "").strip()
            s_name    = str(mapped.get("separated_department_name") or "").strip()

            errors = []
            if not dept_uid:
                errors.append("Empty department_uid")
            if not dept_name:
                errors.append("Empty department_name")

            v_status = "invalid" if errors else "valid"
            v_error  = "; ".join(errors) if errors else None
            if v_status == "valid":
                rows_valid += 1
            else:
                rows_invalid += 1

            try:
                raw_json = json.dumps(row, default=str, ensure_ascii=False)
            except Exception:
                raw_json = None
            extra_json = json.dumps(extra, default=str) if extra else None

            try:
                cur.execute(
                    """INSERT INTO staging_departments
                       (batch_id, import_type_code,
                        department_uid, department_name, organization_name,
                        branch_name, region_name, holding_name,
                        parent_department_uid, parent_department_name,
                        separated_department_uid, separated_department_name,
                        raw_row, extra_fields, validation_status, validation_error)
                       VALUES (%s,'departments',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s::jsonb,%s::jsonb,%s,%s)""",
                    (batch_id, dept_uid, dept_name, org_name, branch, region, holding,
                     p_uid, p_name, s_uid, s_name, raw_json, extra_json, v_status, v_error),
                )
                rows_loaded += 1
            except Exception as exc:
                rows_failed += 1
                print(f"[staging_dept] row #{i} error: {exc}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return rows_loaded, rows_failed, rows_valid, rows_invalid


def get_departments_staging_preview(batch_id: int, limit: int = 500,
                                    status_filter: Optional[str] = None) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COUNT(*),
                      COUNT(*) FILTER (WHERE validation_status = 'valid'),
                      COUNT(*) FILTER (WHERE validation_status = 'invalid')
               FROM staging_departments WHERE batch_id = %s""",
            (batch_id,),
        )
        agg = cur.fetchone()

        where_extra = ""
        params = [batch_id]
        if status_filter in ("valid", "invalid"):
            where_extra = " AND validation_status = %s"
            params.append(status_filter)
        params.append(limit)

        cur.execute(
            f"""SELECT id, department_uid, department_name, organization_name,
                       branch_name, region_name, holding_name,
                       parent_department_uid, parent_department_name,
                       separated_department_uid, separated_department_name,
                       validation_status, validation_error, raw_row
                FROM staging_departments
                WHERE batch_id = %s{where_extra}
                ORDER BY validation_status DESC, id
                LIMIT %s""",
            params,
        )
        rows_data = cur.fetchall()

        return {
            "total":   agg[0] or 0,
            "valid":   agg[1] or 0,
            "invalid": agg[2] or 0,
            "rows": [
                {
                    "id": r[0],
                    "department_uid":             r[1],
                    "department_name":            r[2],
                    "organization_name":          r[3],
                    "branch_name":                r[4],
                    "region_name":                r[5],
                    "holding_name":               r[6],
                    "parent_department_uid":      r[7],
                    "parent_department_name":     r[8],
                    "separated_department_uid":   r[9],
                    "separated_department_name":  r[10],
                    "validation_status":          r[11],
                    "validation_error":           r[12],
                    "raw_row":                    r[13],
                }
                for r in rows_data
            ],
        }
    finally:
        cur.close()
        conn.close()


_TRACKED_SOURCE_FIELDS = [
    "department_name", "parent_department_id", "parent_department_name",
    "separated_department_id", "separated_department_name",
    "organization_name", "branch_name", "region_name", "holding_name",
]


def commit_departments(batch_id: int) -> dict:
    """Write valid staging_departments → dim_department_source + department_source_mapping.

    Rules:
    - dim_department_source: UPSERT descriptive fields; detect and record source changes.
    - department_source_mapping: insert as 'pending' only for new source departments;
      existing mapped/auto/rejected rows are preserved (ON CONFLICT DO NOTHING).
    - Does NOT write to dim_department.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT import_source_id FROM import_batches WHERE id = %s",
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Batch {batch_id} not found")
        source_id = row[0]

        cur.execute("SELECT source_name FROM import_sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        source_name = row[0] if row else ""

        # Pre-fetch all existing source records for this source_id (for change detection)
        cur.execute(
            """SELECT source_department_id,
                      source_department_name, source_parent_department_id,
                      source_parent_department_name, source_separated_department_id,
                      source_separated_department_name, organization_name,
                      branch_name, region_name, holding_name
               FROM dim_department_source
               WHERE source_id = %s""",
            (source_id,),
        )
        existing: dict = {r[0]: r[1:] for r in cur.fetchall()}

        cur.execute(
            """SELECT department_uid, department_name, organization_name,
                      branch_name, region_name, holding_name,
                      parent_department_uid, parent_department_name,
                      separated_department_uid, separated_department_name,
                      extra_fields
               FROM staging_departments
               WHERE batch_id = %s AND validation_status = 'valid' AND department_uid != ''""",
            (batch_id,),
        )
        staging_rows = cur.fetchall()
        inserted = updated = new_mappings = changed_source_count = 0

        for r in staging_rows:
            (dept_uid, dept_name, org_name, branch, region, holding,
             parent_uid, parent_name, sep_uid, sep_name, extra_fields) = r

            source_dept_id = (dept_uid or "").strip()
            if not source_dept_id:
                continue

            extra_json = json.dumps(extra_fields, default=str) if extra_fields else None

            # Detect changes vs existing record
            old_rec = existing.get(source_dept_id)
            is_changed = False
            changed_fields_list: list = []
            prev_snap: dict = {}

            if old_rec is not None:
                old_name, old_pid, old_pname, old_sid, old_sname, old_org, old_branch, old_region, old_holding = old_rec
                new_vals = {
                    "department_name":           (dept_name   or ""),
                    "parent_department_id":      (parent_uid  or ""),
                    "parent_department_name":    (parent_name or ""),
                    "separated_department_id":   (sep_uid     or ""),
                    "separated_department_name": (sep_name    or ""),
                    "organization_name":         (org_name    or ""),
                    "branch_name":               (branch      or ""),
                    "region_name":               (region      or ""),
                    "holding_name":              (holding     or ""),
                }
                old_vals = {
                    "department_name":           (old_name    or ""),
                    "parent_department_id":      (old_pid     or ""),
                    "parent_department_name":    (old_pname   or ""),
                    "separated_department_id":   (old_sid     or ""),
                    "separated_department_name": (old_sname   or ""),
                    "organization_name":         (old_org     or ""),
                    "branch_name":               (old_branch  or ""),
                    "region_name":               (old_region  or ""),
                    "holding_name":              (old_holding or ""),
                }
                for field in _TRACKED_SOURCE_FIELDS:
                    if old_vals[field] != new_vals[field]:
                        changed_fields_list.append(field)
                        prev_snap[field] = old_vals[field]
                is_changed = bool(changed_fields_list)

            changed_fields_json = json.dumps(changed_fields_list, ensure_ascii=False) if changed_fields_list else None
            prev_snap_json      = json.dumps(prev_snap,           ensure_ascii=False) if prev_snap          else None

            cur.execute(
                """INSERT INTO dim_department_source
                       (source_id, source_name, source_department_id, source_department_name,
                        source_parent_department_id, source_parent_department_name,
                        source_separated_department_id, source_separated_department_name,
                        organization_name, branch_name, region_name, holding_name,
                        extra_fields, loaded_at, is_active,
                        last_batch_id, seen_count, source_changed,
                        changed_fields, previous_snapshot, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s::jsonb, NOW(), TRUE,
                           %s, 1, FALSE, NULL, NULL, NOW())
                   ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                       source_name                      = EXCLUDED.source_name,
                       source_department_name           = EXCLUDED.source_department_name,
                       source_parent_department_id      = EXCLUDED.source_parent_department_id,
                       source_parent_department_name    = EXCLUDED.source_parent_department_name,
                       source_separated_department_id   = EXCLUDED.source_separated_department_id,
                       source_separated_department_name = EXCLUDED.source_separated_department_name,
                       organization_name                = EXCLUDED.organization_name,
                       branch_name                      = EXCLUDED.branch_name,
                       region_name                      = EXCLUDED.region_name,
                       holding_name                     = EXCLUDED.holding_name,
                       extra_fields                     = EXCLUDED.extra_fields,
                       loaded_at                        = NOW(),
                       is_active                        = TRUE,
                       last_batch_id                    = %s,
                       seen_count                       = COALESCE(dim_department_source.seen_count, 0) + 1,
                       source_changed                   = %s,
                       changed_fields                   = CASE WHEN %s
                                                               THEN %s::jsonb
                                                               ELSE dim_department_source.changed_fields END,
                       previous_snapshot                = CASE WHEN %s
                                                               THEN %s::jsonb
                                                               ELSE dim_department_source.previous_snapshot END,
                       last_seen_at                     = NOW()
                   RETURNING (xmax = 0) AS is_insert""",
                (
                    # INSERT values
                    source_id, source_name, source_dept_id,
                    dept_name or "", parent_uid or "", parent_name or "",
                    sep_uid or "", sep_name or "",
                    org_name or "", branch or "", region or "", holding or "",
                    extra_json, batch_id,
                    # ON CONFLICT UPDATE values
                    batch_id,
                    is_changed,
                    is_changed, changed_fields_json,
                    is_changed, prev_snap_json,
                ),
            )
            upsert_row = cur.fetchone()
            if upsert_row and upsert_row[0]:
                inserted += 1
            else:
                updated += 1
                if is_changed:
                    changed_source_count += 1

            cur.execute(
                """INSERT INTO department_source_mapping
                       (source_id, source_department_id, master_department_id, mapping_status, confidence)
                   VALUES (%s, %s, NULL, 'pending', 0)
                   ON CONFLICT (source_id, source_department_id) DO NOTHING""",
                (source_id, source_dept_id),
            )
            if cur.rowcount > 0:
                new_mappings += 1

        cur.execute(
            """UPDATE import_batches
               SET status = 'committed', finished_at = NOW(), rows_loaded_to_target = %s
               WHERE id = %s""",
            (inserted + updated, batch_id),
        )

        conn.commit()
        return {
            "inserted":            inserted,
            "updated":             updated,
            "new_mappings":        new_mappings,
            "changed_source_count": changed_source_count,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# BRANDS IMPORT HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def load_brands_to_staging(batch_id: int, rows: list, field_mapping: list) -> tuple:
    """Apply mapping + validate + write to staging_brands.
    Returns (rows_loaded, rows_failed, rows_valid, rows_invalid).
    """
    if not rows:
        return 0, 0, 0, 0

    conn = get_connection()
    cur = conn.cursor()
    rows_loaded = rows_failed = rows_valid = rows_invalid = 0

    try:
        for i, row in enumerate(rows):
            mapped, _ = _apply_mapping(row, field_mapping)
            _, extra = _split_canonical_extra(mapped, CANONICAL_STAGING_FIELDS["brands"])

            brand_uid    = str(mapped.get("brand_uid")    or "").strip()
            brand_name   = str(mapped.get("brand_name")   or "").strip()
            brand_group  = str(mapped.get("brand_group")  or "").strip()
            p_uid        = str(mapped.get("parent_brand_uid")  or "").strip()
            p_name       = str(mapped.get("parent_brand_name") or "").strip()
            source_level        = str(mapped.get("source_level")        or mapped.get("Level_1")   or "").strip()
            source_company_name = str(mapped.get("source_company_name") or mapped.get("Company")   or mapped.get("company_name") or "").strip()
            source_is_active    = str(mapped.get("source_is_active")    or mapped.get("valid")     or mapped.get("is_active")    or "").strip()
            source_brand_ref_id = str(mapped.get("source_brand_ref_id") or mapped.get("brand_id") or "").strip()
            if i < 3:
                print(f"[brands debug] row#{i}: raw_keys={list(row.keys())[:8]}")
                print(f"[brands debug] row#{i}: source_level={source_level!r} source_company_name={source_company_name!r} source_is_active={source_is_active!r} source_brand_ref_id={source_brand_ref_id!r}")

            errors = []
            if not brand_name:
                errors.append("Empty brand_name")

            v_status = "invalid" if errors else "valid"
            v_error  = "; ".join(errors) if errors else None
            if v_status == "valid":
                rows_valid += 1
            else:
                rows_invalid += 1

            try:
                raw_json = json.dumps(row, default=str, ensure_ascii=False)
            except Exception:
                raw_json = None
            extra_json = json.dumps(extra, default=str) if extra else None

            try:
                cur.execute(
                    """INSERT INTO staging_brands
                       (batch_id, import_type_code,
                        brand_uid, brand_name, brand_group,
                        parent_brand_uid, parent_brand_name,
                        source_level, source_company_name, source_is_active, source_brand_ref_id,
                        raw_row, extra_fields, validation_status, validation_error)
                       VALUES (%s,'brands',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                    (batch_id, brand_uid or None, brand_name, brand_group or None,
                     p_uid or None, p_name or None,
                     source_level or None, source_company_name or None, source_is_active or None, source_brand_ref_id or None,
                     raw_json, extra_json, v_status, v_error),
                )
                rows_loaded += 1
            except Exception as exc:
                rows_failed += 1
                print(f"[staging_brands] row #{i} error: {exc}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return rows_loaded, rows_failed, rows_valid, rows_invalid


def get_brands_staging_preview(batch_id: int, limit: int = 500,
                               status_filter: Optional[str] = None) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COUNT(*),
                      COUNT(*) FILTER (WHERE validation_status = 'valid'),
                      COUNT(*) FILTER (WHERE validation_status = 'invalid')
               FROM staging_brands WHERE batch_id = %s""",
            (batch_id,),
        )
        agg = cur.fetchone()

        where_extra = ""
        params = [batch_id]
        if status_filter in ("valid", "invalid"):
            where_extra = " AND validation_status = %s"
            params.append(status_filter)
        params.append(limit)

        cur.execute(
            f"""SELECT id, brand_uid, brand_name, brand_group,
                       parent_brand_uid, parent_brand_name,
                       source_level, source_company_name, source_is_active, source_brand_ref_id,
                       validation_status, validation_error, raw_row
                FROM staging_brands
                WHERE batch_id = %s{where_extra}
                ORDER BY validation_status DESC, id
                LIMIT %s""",
            params,
        )
        rows_data = cur.fetchall()

        return {
            "total":   agg[0] or 0,
            "valid":   agg[1] or 0,
            "invalid": agg[2] or 0,
            "rows": [
                {
                    "id":                r[0],
                    "brand_uid":         r[1],
                    "brand_name":        r[2],
                    "brand_group":       r[3],
                    "parent_brand_uid":  r[4],
                    "parent_brand_name": r[5],
                    "source_level":          r[6],
                    "source_company_name":   r[7],
                    "source_is_active":      r[8],
                    "source_brand_ref_id":   r[9],
                    "validation_status": r[10],
                    "validation_error":  r[11],
                    "raw_row":           r[12],
                }
                for r in rows_data
            ],
        }
    finally:
        cur.close()
        conn.close()


def commit_brands(batch_id: int) -> dict:
    """Write valid staging_brands → dim_brand_source + brand_source_mapping (Phase 1).

    Rules:
    - dim_brand_source: upsert descriptive fields; never resets mapping state.
    - brand_source_mapping: insert as 'pending' only for new source brands;
      existing mapped/rejected rows are never touched (ON CONFLICT DO NOTHING).
    - Does NOT write to dim_brand.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Resolve source_id and source_name from batch
        cur.execute(
            "SELECT import_source_id FROM import_batches WHERE id = %s",
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Batch {batch_id} not found")
        source_id = row[0]

        cur.execute("SELECT source_name FROM import_sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        source_name = row[0] if row else ""

        # Ensure columns exist in dim_brand_source
        for ddl in [
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS last_batch_id INTEGER",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS seen_count INTEGER DEFAULT 1",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_changed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS changed_fields JSONB DEFAULT '[]'::JSONB",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS previous_snapshot JSONB",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_level TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_company_name TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_is_active TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS source_brand_ref_id TEXT",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archived_by INTEGER",
            "ALTER TABLE dim_brand_source ADD COLUMN IF NOT EXISTS archive_reason TEXT",
        ]:
            cur.execute(ddl)

        # Fetch valid staging rows
        cur.execute(
            """SELECT brand_uid, brand_name, brand_group,
                      parent_brand_uid, parent_brand_name,
                      extra_fields,
                      source_level, source_company_name, source_is_active, source_brand_ref_id
               FROM staging_brands
               WHERE batch_id = %s AND validation_status = 'valid' AND brand_name != ''""",
            (batch_id,),
        )
        staging_rows = cur.fetchall()

        # Pre-fetch existing records for change detection
        cur.execute(
            """SELECT source_brand_id, source_brand_name, source_brand_group,
                      source_parent_uid, source_parent_name,
                      source_level, source_company_name, source_is_active
               FROM dim_brand_source WHERE source_id = %s""",
            (source_id,),
        )
        existing = {r[0]: r[1:] for r in cur.fetchall()}

        _TRACKED = (
            "source_brand_name", "source_brand_group",
            "source_parent_uid", "source_parent_name",
            "source_level", "source_company_name", "source_is_active",
        )
        inserted = updated = new_mappings = 0

        for (brand_uid, brand_name, brand_group, p_uid, p_name,
             extra_fields, source_level, source_company_name, source_is_active, source_brand_ref_id) in staging_rows:
            # Stable source_brand_id: prefer brand_uid, fall back to brand_name
            source_brand_id = (brand_uid or "").strip() or (brand_name or "").strip()
            if not source_brand_id:
                continue

            extra_json = json.dumps(extra_fields, default=str) if extra_fields else None
            new_vals = (
                brand_name or "", brand_group or "", p_uid or "", p_name or "",
                source_level or "", source_company_name or "", source_is_active or "",
            )

            # Source-changed detection
            existing_rec = existing.get(source_brand_id)
            if existing_rec is None:
                src_changed = False
                changed_fields_list = []
                prev_snapshot = None
            else:
                changed = [
                    fname for i, fname in enumerate(_TRACKED)
                    if str(existing_rec[i] or "") != str(new_vals[i] or "")
                ]
                src_changed = bool(changed)
                changed_fields_list = changed
                prev_snapshot = {
                    "source_brand_name":  existing_rec[0],
                    "source_brand_group": existing_rec[1],
                    "source_parent_uid":  existing_rec[2],
                    "source_parent_name": existing_rec[3],
                    "source_level":       existing_rec[4],
                    "source_company_name":existing_rec[5],
                    "source_is_active":   existing_rec[6],
                } if src_changed else None

            changed_fields_json = json.dumps(changed_fields_list)
            prev_snapshot_json = json.dumps(prev_snapshot, default=str) if prev_snapshot else None

            # Upsert descriptive fields — never overwrites mapping state
            cur.execute(
                """INSERT INTO dim_brand_source
                       (source_id, source_name, source_brand_id, source_brand_name,
                        source_brand_group, source_parent_uid, source_parent_name,
                        extra_fields, loaded_at, is_active,
                        source_level, source_company_name, source_is_active, source_brand_ref_id,
                        last_batch_id, last_seen_at, seen_count,
                        source_changed, changed_fields, previous_snapshot, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), TRUE,
                           %s, %s, %s, %s,
                           %s, NOW(), 1,
                           %s, %s::jsonb, %s::jsonb, NOW())
                   ON CONFLICT (source_id, source_brand_id) DO UPDATE SET
                       source_name         = EXCLUDED.source_name,
                       source_brand_name   = EXCLUDED.source_brand_name,
                       source_brand_group  = EXCLUDED.source_brand_group,
                       source_parent_uid   = EXCLUDED.source_parent_uid,
                       source_parent_name  = EXCLUDED.source_parent_name,
                       extra_fields        = EXCLUDED.extra_fields,
                       loaded_at           = NOW(),
                       is_active           = TRUE,
                       archived            = FALSE,
                       source_level        = EXCLUDED.source_level,
                       source_company_name = EXCLUDED.source_company_name,
                       source_is_active    = EXCLUDED.source_is_active,
                       source_brand_ref_id = EXCLUDED.source_brand_ref_id,
                       last_batch_id       = EXCLUDED.last_batch_id,
                       last_seen_at        = NOW(),
                       seen_count          = COALESCE(dim_brand_source.seen_count, 0) + 1,
                       source_changed      = EXCLUDED.source_changed,
                       changed_fields      = EXCLUDED.changed_fields,
                       previous_snapshot   = CASE WHEN EXCLUDED.source_changed
                                                  THEN EXCLUDED.previous_snapshot
                                                  ELSE dim_brand_source.previous_snapshot END,
                       updated_at          = NOW()
                   RETURNING (xmax = 0) AS is_insert""",
                (source_id, source_name, source_brand_id,
                 brand_name or "", brand_group or "",
                 p_uid or "", p_name or "",
                 extra_json,
                 source_level or None, source_company_name or None, source_is_active or None, source_brand_ref_id or None,
                 batch_id,
                 src_changed, changed_fields_json, prev_snapshot_json),
            )
            row = cur.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1

            # New pending mapping only — never overwrites mapped/rejected
            cur.execute(
                """INSERT INTO brand_source_mapping
                       (source_id, source_brand_id, master_brand_id, mapping_status, confidence)
                   VALUES (%s, %s, NULL, 'pending', 0)
                   ON CONFLICT (source_id, source_brand_id) DO NOTHING""",
                (source_id, source_brand_id),
            )
            if cur.rowcount and cur.rowcount > 0:
                new_mappings += 1

        total = inserted + updated

        # Soft-delete brands absent from current batch
        cur.execute(
            """UPDATE dim_brand_source
               SET is_active = FALSE, updated_at = NOW()
               WHERE source_id = %(source_id)s
                 AND is_active = TRUE
                 AND source_brand_id NOT IN (
                     SELECT source_brand_id FROM staging_brands WHERE batch_id = %(batch_id)s
                 )""",
            {"source_id": source_id, "batch_id": batch_id},
        )
        deactivated = cur.rowcount or 0

        cur.execute(
            """UPDATE import_batches
               SET status = 'committed', finished_at = NOW(), rows_loaded_to_target = %s
               WHERE id = %s""",
            (total, batch_id),
        )
        cur.execute(
            """UPDATE staging_brands SET validation_status = 'committed'
               WHERE batch_id = %s AND validation_status = 'valid'""",
            (batch_id,),
        )

        # Staging cleanup: delete batches older than 30 days
        cur.execute(
            "DELETE FROM staging_brands WHERE created_at < NOW() - INTERVAL '30 days'"
        )

        conn.commit()
        return {
            "inserted":    inserted,
            "updated":     updated,
            "new_mappings": new_mappings,
            "deactivated": deactivated,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ARTICLES handlers
# ─────────────────────────────────────────────────────────────────────────────

def load_articles_to_staging(batch_id: int, rows: list, field_mapping: list):
    """Map OLAP rows → staging_articles, validate, return counts."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        loaded = failed = valid = invalid = 0
        for raw in rows:
            mapped, _ = _apply_mapping(raw, field_mapping)
            _, extra = _split_canonical_extra(mapped, CANONICAL_STAGING_FIELDS["articles"])

            article_uid  = str(mapped.get("article_uid") or "").strip() or None
            article_name = str(mapped.get("article_name") or "").strip() or None
            article_type = str(mapped.get("article_type") or "").strip() or None
            level1       = str(mapped.get("level1") or "").strip() or None
            level2       = str(mapped.get("level2") or "").strip() or None
            pnl_code     = str(mapped.get("pnl_code") or "").strip() or None
            exp_elem     = str(mapped.get("expense_element") or "").strip() or None
            exp_comp     = str(mapped.get("expense_company") or "").strip() or None
            extra_json   = json.dumps(extra, default=str) if extra else None

            errors = []
            if not article_name:
                errors.append("article_name is required")

            vstatus = "invalid" if errors else "valid"
            verror  = "; ".join(errors) if errors else None
            if vstatus == "valid":
                valid += 1
            else:
                invalid += 1

            cur.execute(
                """INSERT INTO staging_articles
                   (batch_id, article_uid, article_name, article_type, level1, level2,
                    pnl_code, expense_element, expense_company, raw_row,
                    extra_fields, validation_status, validation_error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                (batch_id, article_uid, article_name, article_type, level1, level2,
                 pnl_code, exp_elem, exp_comp,
                 json.dumps(raw, default=str), extra_json, vstatus, verror),
            )
            loaded += 1

        conn.commit()
        return loaded, failed, valid, invalid
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_articles_staging_preview(batch_id: int, limit: int = 500, status_filter=None) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cond = "WHERE batch_id = %s"
        params = [batch_id]
        if status_filter:
            cond += " AND validation_status = %s"
            params.append(status_filter)

        cur.execute(
            f"""SELECT id, article_uid, article_name, article_type,
                       level1, level2, pnl_code, expense_element, expense_company,
                       validation_status, validation_error, raw_row
                FROM staging_articles {cond} ORDER BY id LIMIT %s""",
            params + [limit],
        )
        rows = [
            {
                "id": r[0], "article_uid": r[1], "article_name": r[2],
                "article_type": r[3], "level1": r[4], "level2": r[5],
                "pnl_code": r[6], "expense_element": r[7], "expense_company": r[8],
                "validation_status": r[9], "validation_error": r[10],
                "raw_row": r[11],
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT COUNT(*) FROM staging_articles WHERE batch_id = %s", (batch_id,)
        )
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM staging_articles WHERE batch_id = %s AND validation_status = 'valid'",
            (batch_id,),
        )
        valid = cur.fetchone()[0]
        return {"rows": rows, "total": total, "valid": valid, "invalid": total - valid}
    finally:
        cur.close()
        conn.close()


def commit_articles(batch_id: int) -> dict:
    """Write valid staging_articles → dim_article_source + article_source_mapping.

    Rules:
    - dim_article_source: upsert descriptive fields (never resets mapping state)
    - article_source_mapping: insert as 'pending' only if row does not yet exist
      (existing mapped/rejected rows are never touched)
    - Does NOT write to dim_article.
    """
    from services.article_import_service import ensure_source_staging_tables
    ensure_source_staging_tables()

    conn = get_connection()
    cur = conn.cursor()
    try:
        # Resolve source_id from batch
        cur.execute(
            "SELECT import_source_id FROM import_batches WHERE id = %s",
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Batch {batch_id} not found")
        source_id = row[0]

        cur.execute("SELECT source_name FROM import_sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        source_name = row[0] if row else ""

        # Fetch valid staging rows
        cur.execute(
            """SELECT article_uid, article_name, article_type, level1, level2,
                      expense_element, expense_company
               FROM staging_articles
               WHERE batch_id = %s AND validation_status = 'valid'""",
            (batch_id,),
        )
        staging_rows = cur.fetchall()
        inserted_source = updated_source = new_mappings = 0

        for (art_uid, art_name, art_type, lv1, lv2, exp_elem, exp_comp) in staging_rows:
            source_article_id = (art_uid or "").strip() or (art_name or "").strip()
            if not source_article_id:
                continue

            # Upsert descriptive fields into dim_article_source
            cur.execute(
                """INSERT INTO dim_article_source
                       (source_id, source_name, source_article_id, source_article_name,
                        source_article_type, source_level1, source_level2,
                        expense_element, expense_company, loaded_at, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),TRUE)
                   ON CONFLICT (source_id, source_article_id) DO UPDATE SET
                       source_name         = EXCLUDED.source_name,
                       source_article_name = EXCLUDED.source_article_name,
                       source_article_type = EXCLUDED.source_article_type,
                       source_level1       = EXCLUDED.source_level1,
                       source_level2       = EXCLUDED.source_level2,
                       expense_element     = EXCLUDED.expense_element,
                       expense_company     = EXCLUDED.expense_company,
                       loaded_at           = NOW(),
                       is_active           = TRUE
                   RETURNING (xmax = 0) AS is_insert""",
                (source_id, source_name, source_article_id,
                 art_name or "", art_type or "", lv1 or "", lv2 or "",
                 exp_elem or "", exp_comp or ""),
            )
            row = cur.fetchone()
            if row and row[0]:
                inserted_source += 1
            else:
                updated_source += 1

            # Create mapping row as 'pending' only for new source articles
            cur.execute(
                """INSERT INTO article_source_mapping
                       (source_id, source_article_id, master_article_id, mapping_status, confidence)
                   VALUES (%s,%s,NULL,'pending',0)
                   ON CONFLICT (source_id, source_article_id) DO NOTHING""",
                (source_id, source_article_id),
            )
            if cur.rowcount and cur.rowcount > 0:
                new_mappings += 1

        total = inserted_source + updated_source

        cur.execute(
            """UPDATE import_batches
               SET status = 'committed', finished_at = NOW(), rows_loaded_to_target = %s
               WHERE id = %s""",
            (total, batch_id),
        )
        cur.execute(
            """UPDATE staging_articles SET validation_status = 'committed'
               WHERE batch_id = %s AND validation_status = 'valid'""",
            (batch_id,),
        )
        conn.commit()
        return {
            "inserted": inserted_source,
            "updated":  updated_source,
            "new_mappings": new_mappings,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

def universal_load_to_staging(
    batch_id: int,
    rows: list,
    field_mapping: list,
    import_type_code: str,
    period_from=None,
    period_to=None,
) -> tuple:
    """Dispatch to type-specific staging loader.
    Returns (rows_loaded, rows_failed, rows_filtered_out, rows_valid, rows_invalid).
    """
    if import_type_code == "sales_fact":
        return load_sales_fact_to_staging(batch_id, rows, field_mapping, period_from, period_to)
    elif import_type_code == "departments":
        loaded, failed, valid, invalid = load_departments_to_staging(batch_id, rows, field_mapping)
        return loaded, failed, 0, valid, invalid
    elif import_type_code == "brands":
        loaded, failed, valid, invalid = load_brands_to_staging(batch_id, rows, field_mapping)
        return loaded, failed, 0, valid, invalid
    elif import_type_code == "articles":
        loaded, failed, valid, invalid = load_articles_to_staging(batch_id, rows, field_mapping)
        return loaded, failed, 0, valid, invalid
    else:
        raise ValueError(f"Import type '{import_type_code}' not yet supported in staging engine")


def universal_get_staging_preview(
    batch_id: int,
    import_type_code: str,
    limit: int = 500,
    status_filter: Optional[str] = None,
) -> dict:
    if import_type_code == "departments":
        return get_departments_staging_preview(batch_id, limit=limit, status_filter=status_filter)
    elif import_type_code == "brands":
        return get_brands_staging_preview(batch_id, limit=limit, status_filter=status_filter)
    elif import_type_code == "articles":
        return get_articles_staging_preview(batch_id, limit=limit, status_filter=status_filter)
    else:
        return get_staging_preview(batch_id, limit=limit, status_filter=status_filter)


def universal_commit(
    batch_id: int,
    import_type_code: str,
    source_id: Optional[int] = None,
    period_from=None,
    period_to=None,
) -> dict:
    if import_type_code == "sales_fact":
        committed, deleted = commit_sales_fact(batch_id, source_id, period_from, period_to)
        return {"committed": committed, "deleted_from_target": deleted}
    elif import_type_code == "departments":
        return commit_departments(batch_id)
    elif import_type_code == "brands":
        return commit_brands(batch_id)
    elif import_type_code == "articles":
        return commit_articles(batch_id)
    else:
        raise ValueError(f"Import type '{import_type_code}' commit not implemented")


def rollback_batch(batch_id: int) -> dict:
    """Delete staging rows for a batch (soft-rollback; dimension records are kept)."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")

    import_type_code = batch["import_type_code"]
    staging_table = _TYPE_STAGING.get(import_type_code)

    conn = get_connection()
    cur = conn.cursor()
    try:
        deleted_staging = 0
        if staging_table:
            cur.execute(f"DELETE FROM {staging_table} WHERE batch_id = %s", (batch_id,))
            deleted_staging = cur.rowcount
        update_batch(batch_id, status="rolled_back")
        conn.commit()
        return {"deleted_staging": deleted_staging, "import_type_code": import_type_code}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
