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
    {"code": "pnl_plan",               "name": "Plan PnL",                 "target_table": "plan_pnl"},
    {"code": "pnl_fact",               "name": "Fakt PnL",                 "target_table": "fact_pnl"},
    {"code": "sales_fact",             "name": "Fakt tovarooborotu",        "target_table": "fact_turnover"},
    {"code": "sales_plan",             "name": "Plan tovarooborotu",        "target_table": "plan_turnover"},
    {"code": "expense_budget",         "name": "Byudzhety vytrat",          "target_table": "budgets"},
    {"code": "articles",               "name": "Statti PnL",                "target_table": "dim_article"},
    {"code": "article_mapping",        "name": "Vidpovidnist statey",       "target_table": "article_mapping"},
    {"code": "commercial_conditions",  "name": "Komertsiini umovy",         "target_table": "commercial_conditions"},
]

# Map import_type_code -> target_table
_TYPE_TARGET = {t["code"]: t["target_table"] for t in IMPORT_TYPES}

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

            try:
                raw_json = json.dumps(row, default=str, ensure_ascii=False)
            except Exception:
                raw_json = None

            try:
                cur.execute(
                    """INSERT INTO staging_sales_fact
                       (batch_id, period_month, department_uid, department_name,
                        product_group_id, product_group_uid, product_group_name,
                        sales_vat, sales_retail, excise, sales_dal, sales_kg,
                        raw_row, validation_status, validation_error)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                    (batch_id, period, dept_uid, dept_name, pg_id, pg_uid, pg_name,
                     sales_vat, sales_retail, excise, sales_dal, sales_kg,
                     raw_json, v_status, v_error),
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
