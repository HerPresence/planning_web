import re
"""
Planning Engine — Rule-engine generation with enriched dimensions.

Enrichment join path:
  fact_turnover.department_uid
    → department_source_mapping (by source_id + source_department_id)
    → dim_department → holding_name / organization_name / region_name / branch_name

  fact_turnover.product_group_uid
    → brand_source_mapping (by source_id + source_brand_id)
    → dim_brand → brand_name / brand_uid / brand_group

Rule matching per fact row:
  All scope conditions on a rule must match (AND logic).
  Conflict resolution per rule_type: priority DESC → scope_cnt DESC → created_at DESC.
  Different rule_types combine independently in the final formula.

Calculation model:
  plan_kg  = fact_kg  * (1 + vol_pct/100)
  plan_dal = fact_dal * (1 + vol_pct/100)

  When fact_kg > 0:
    plan_vat = fact_vat * (1+vol/100) * (1+price/100) * (1+revenue/100)
  When fact_kg = 0:
    plan_vat = fact_vat * (1+price/100) * (1+revenue/100)
"""

import json
import logging
from datetime import date
from typing import Optional, List

from db import get_connection

log = logging.getLogger("planning")

VALID_RULE_TYPES  = {"revenue_effect_pct", "volume_effect_pct", "price_effect_pct"}
VALID_SCOPE_TYPES = {
    "all", "holding", "organization", "region", "branch",
    "parent_department", "department", "department_uid",
    "product_group", "product_group_uid",
    "brand", "brand_uid", "source_id",
}

# Maps scope dimension_type → fact_plan_sales column (for coverage diagnostics)
_FPS_DIM_COL = {
    "holding":           "holding_name",
    "organization":      "organization_name",
    "region":            "region_name",
    "branch":            "branch_name",
    "department":        "department_name",
    "department_uid":    "department_uid",
    "product_group":     "product_group_name",
    "product_group_uid": "product_group_uid",
    "brand":             "brand_name",
    "brand_uid":         "brand_uid",
}

_DIM_LABELS_UK = {
    "holding": "Холдинг", "organization": "Організація",
    "region": "Регіон", "branch": "Філія",
    "parent_department": "Батьк. підр.", "department": "Підрозділ",
    "department_uid": "Підр. UID", "product_group": "Товарна група",
    "product_group_uid": "ТГ UID", "brand": "Бренд", "brand_uid": "Бренд UID",
}


# ══════════════════════════════════════════════════════════════════════════════
# PURE CALC FUNCTIONS — no DB; mirrors the SQL formulas; used in tests
# ══════════════════════════════════════════════════════════════════════════════

def calc_plan_kg(fact_kg: float, vol_pct: float) -> float:
    return fact_kg * (1 + vol_pct / 100)

def calc_plan_dal(fact_dal: float, vol_pct: float) -> float:
    return fact_dal * (1 + vol_pct / 100)

def calc_fact_price_per_kg(fact_vat: float, fact_kg: float):
    return fact_vat / fact_kg if fact_kg > 0 else None

def calc_plan_price_per_kg(fact_price_per_kg, price_pct: float):
    return fact_price_per_kg * (1 + price_pct / 100) if fact_price_per_kg is not None else None

def calc_plan_sales_vat(fact_vat: float, fact_kg: float, vol_pct: float, price_pct: float, rev_pct: float) -> float:
    """
    When kg > 0:  plan_vat = fact_vat * vol_mult * price_mult
                  (revenue_effect does NOT apply — price model is sufficient)
    When kg = 0:  plan_vat = fact_vat * rev_mult  (fallback — revenue-only)
    """
    if fact_kg > 0:
        return fact_vat * (1 + vol_pct / 100) * (1 + price_pct / 100)
    return fact_vat * (1 + rev_pct / 100)

def select_winning_rule(rules_for_type: list) -> dict:
    """Highest priority wins; ties broken by scope_cnt DESC (more specific wins)."""
    if not rules_for_type:
        return {}
    return max(rules_for_type, key=lambda r: (r.get("priority", 0), r.get("scope_cnt", 0)))

def scope_matches_row(row: dict, scope: dict) -> bool:
    """Returns True if a single scope condition matches the row dict."""
    dim_type = scope.get("dimension_type", "all")
    if dim_type == "all":
        return True
    field_map = {
        "holding": "holding_name", "organization": "organization_name",
        "region": "region_name", "branch": "branch_name",
        "parent_department": "parent_dept_name", "department": "department_name",
        "department_uid": "department_uid", "product_group": "product_group_name",
        "product_group_uid": "product_group_uid", "brand": "brand_name",
        "brand_uid": "brand_uid",
    }
    field = field_map.get(dim_type)
    if not field:
        return str(row.get("source_id", "")) == scope.get("dimension_value", "")
    return (row.get(field) or "") == scope.get("dimension_value", "")

def rule_matches_row(row: dict, scopes: list) -> bool:
    """OR within same dimension, AND between different dimensions."""
    from collections import defaultdict
    by_dim: dict = defaultdict(list)
    for s in scopes:
        dt = s.get("dimension_type", "all")
        if dt == "all":
            continue
        by_dim[dt].append(s)
    if not by_dim:
        return True
    return all(
        any(scope_matches_row(row, s) for s in dim_scopes)
        for dim_scopes in by_dim.values()
    )


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate_rule(
    rule_type=None, effect_percent=None, priority=None,
    period_from=None, period_to=None, scopes=None,
):
    errors = []
    if rule_type is not None and rule_type not in VALID_RULE_TYPES:
        errors.append(f"rule_type must be one of: {sorted(VALID_RULE_TYPES)}")
    if effect_percent is not None:
        ep = float(effect_percent)
        if ep < -100 or ep > 1000:
            errors.append(f"effect_percent must be between -100 and 1000 (got {ep})")
    if priority is not None and int(priority) < 0:
        errors.append(f"priority must be >= 0 (got {priority})")
    if period_from is not None and period_to is not None and period_from > period_to:
        errors.append(f"period_from ({period_from}) must be <= period_to ({period_to})")
    if scopes:
        for i, sc in enumerate(scopes):
            dt = sc.get("dimension_type", "all")
            dv = sc.get("dimension_value", "")
            if dt not in VALID_SCOPE_TYPES:
                errors.append(f"scope[{i}]: unknown dimension_type '{dt}'")
            elif dt != "all" and not str(dv).strip():
                errors.append(f"scope[{i}]: dimension_value cannot be empty for dimension_type '{dt}'")
    if errors:
        raise ValueError("; ".join(errors))


# Scope match CASE used inside NOT EXISTS — maps dimension_type to a boolean expression
_SCOPE_MATCH_CASE = """
    CASE prs.dimension_type
        WHEN 'holding'           THEN COALESCE(e.holding_name,       '') = prs.dimension_value
        WHEN 'organization'      THEN COALESCE(e.organization_name,  '') = prs.dimension_value
        WHEN 'region'            THEN COALESCE(e.region_name,        '') = prs.dimension_value
        WHEN 'branch'            THEN COALESCE(e.branch_name,        '') = prs.dimension_value
        WHEN 'parent_department' THEN COALESCE(e.parent_dept_name,   '') = prs.dimension_value
        WHEN 'department'        THEN COALESCE(e.department_name,    '') = prs.dimension_value
        WHEN 'department_uid'    THEN e.department_uid                    = prs.dimension_value
        WHEN 'product_group'     THEN COALESCE(e.product_group_name, '') = prs.dimension_value
        WHEN 'product_group_uid' THEN e.product_group_uid                 = prs.dimension_value
        WHEN 'brand'             THEN COALESCE(e.brand_name,         '') = prs.dimension_value
        WHEN 'brand_uid'         THEN COALESCE(e.brand_uid, e.product_group_uid) = prs.dimension_value
        WHEN 'source_id'         THEN e.source_id::TEXT                   = prs.dimension_value
        WHEN 'all'               THEN TRUE
        ELSE FALSE
    END
"""


# ══════════════════════════════════════════════════════════════════════════════
# TABLE SETUP
# ══════════════════════════════════════════════════════════════════════════════

def ensure_planning_tables():
    """Idempotent — safe to call on every startup."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        # ── dim_scenario ────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_scenario (
                scenario_id   SERIAL PRIMARY KEY,
                scenario_code TEXT    NOT NULL UNIQUE,
                scenario_name TEXT    NOT NULL,
                scenario_type TEXT    NOT NULL DEFAULT 'draft',
                description   TEXT,
                is_active     BOOLEAN DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT NOW(),
                created_by    INTEGER
            )
        """)
        for _col, _defn in [
            ("scenario_type", "TEXT NOT NULL DEFAULT 'draft'"),
            ("description",   "TEXT"),
            ("is_active",     "BOOLEAN DEFAULT TRUE"),
            ("created_at",    "TIMESTAMP DEFAULT NOW()"),
            ("created_by",    "INTEGER"),
        ]:
            try: cur.execute(f"ALTER TABLE dim_scenario ADD COLUMN IF NOT EXISTS {_col} {_defn}"); conn.commit()
            except Exception: conn.rollback()

        # ── scenario_version ─────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scenario_version (
                version_id     SERIAL PRIMARY KEY,
                scenario_id    INTEGER NOT NULL,
                version_number INTEGER NOT NULL DEFAULT 1,
                version_name   TEXT    NOT NULL DEFAULT 'v1',
                description    TEXT,
                is_locked      BOOLEAN   DEFAULT FALSE,
                locked_at      TIMESTAMP,
                locked_by      INTEGER,
                created_at     TIMESTAMP DEFAULT NOW(),
                created_by     INTEGER,
                UNIQUE (scenario_id, version_number)
            )
        """)
        for _col, _defn in [
            ("version_name",   "TEXT NOT NULL DEFAULT 'v1'"),
            ("description",    "TEXT"),
            ("is_locked",      "BOOLEAN DEFAULT FALSE"),
            ("locked_at",      "TIMESTAMP"),
            ("locked_by",      "INTEGER"),
            ("created_at",     "TIMESTAMP DEFAULT NOW()"),
            ("created_by",     "INTEGER"),
        ]:
            try: cur.execute(f"ALTER TABLE scenario_version ADD COLUMN IF NOT EXISTS {_col} {_defn}"); conn.commit()
            except Exception: conn.rollback()

        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='scenario_version'::regclass AND conname='fk_sv_scenario')
                THEN
                    ALTER TABLE scenario_version ADD CONSTRAINT fk_sv_scenario
                    FOREIGN KEY (scenario_id) REFERENCES dim_scenario(scenario_id);
                END IF;
            END $$
        """)

        # ── fact_plan_sales ──────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_plan_sales (
                id                 SERIAL PRIMARY KEY,
                scenario_id        INTEGER NOT NULL,
                version_id         INTEGER NOT NULL,
                period_month       DATE    NOT NULL,
                department_uid     TEXT    NOT NULL DEFAULT '',
                department_id      TEXT,
                department_name    TEXT,
                product_group_uid  TEXT    NOT NULL DEFAULT '',
                product_group_id   TEXT,
                product_group_name TEXT,
                sales_vat_plan     NUMERIC(18,4) DEFAULT 0,
                sales_retail_plan  NUMERIC(18,4) DEFAULT 0,
                excise_plan        NUMERIC(18,4) DEFAULT 0,
                sales_dal_plan     NUMERIC(18,4) DEFAULT 0,
                sales_kg_plan      NUMERIC(18,4) DEFAULT 0,
                fact_sales_vat     NUMERIC(18,4) DEFAULT 0,
                fact_sales_retail  NUMERIC(18,4) DEFAULT 0,
                fact_excise        NUMERIC(18,4) DEFAULT 0,
                fact_sales_dal     NUMERIC(18,4) DEFAULT 0,
                fact_sales_kg      NUMERIC(18,4) DEFAULT 0,
                base_year          INTEGER,
                generation_method  TEXT NOT NULL DEFAULT 'rule_engine',
                generation_id      INTEGER,
                created_at         TIMESTAMP DEFAULT NOW(),
                updated_at         TIMESTAMP DEFAULT NOW(),
                UNIQUE (scenario_id, version_id, period_month, department_uid, product_group_uid)
            )
        """)
        # New dimension snapshot + baseline + applied effects columns
        for col, defn in [
            ("holding_name",               "TEXT"),
            ("organization_name",          "TEXT"),
            ("region_name",                "TEXT"),
            ("branch_name",                "TEXT"),
            ("parent_department_name",     "TEXT"),
            ("brand_name",                 "TEXT"),
            ("brand_uid",                  "TEXT"),
            ("unmapped_dept",              "BOOLEAN DEFAULT FALSE"),
            ("unmapped_brand",             "BOOLEAN DEFAULT FALSE"),
            ("baseline_sales_vat",         "NUMERIC(18,4)"),
            ("baseline_sales_retail",      "NUMERIC(18,4)"),
            ("baseline_kg",                "NUMERIC(18,4)"),
            ("baseline_dal",               "NUMERIC(18,4)"),
            ("applied_revenue_effect_pct", "NUMERIC(8,4) DEFAULT 0"),
            ("applied_volume_effect_pct",  "NUMERIC(8,4) DEFAULT 0"),
            ("applied_price_effect_pct",   "NUMERIC(8,4) DEFAULT 0"),
            ("applied_rule_ids_json",      "JSONB DEFAULT '[]'::JSONB"),
            ("fact_price_per_kg",          "NUMERIC(18,6)"),
            ("plan_price_per_kg",          "NUMERIC(18,6)"),
            ("fact_price_per_dal",         "NUMERIC(18,6)"),
            ("plan_price_per_dal",         "NUMERIC(18,6)"),
        ]:
            try:
                cur.execute(f"ALTER TABLE fact_plan_sales ADD COLUMN IF NOT EXISTS {col} {defn}")
                conn.commit()
            except Exception:
                conn.rollback()

        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_fps_scen_ver   ON fact_plan_sales (scenario_id, version_id)",
            "CREATE INDEX IF NOT EXISTS idx_fps_period     ON fact_plan_sales (period_month)",
            "CREATE INDEX IF NOT EXISTS idx_fps_dept_uid   ON fact_plan_sales (department_uid)",
            "CREATE INDEX IF NOT EXISTS idx_fps_pg_uid     ON fact_plan_sales (product_group_uid)",
            "CREATE INDEX IF NOT EXISTS idx_fps_region     ON fact_plan_sales (region_name)",
            "CREATE INDEX IF NOT EXISTS idx_fps_branch     ON fact_plan_sales (branch_name)",
            "CREATE INDEX IF NOT EXISTS idx_fps_brand_name ON fact_plan_sales (brand_name)",
            "CREATE INDEX IF NOT EXISTS idx_fps_gen_id     ON fact_plan_sales (generation_id)",
            "CREATE INDEX IF NOT EXISTS idx_fps_org_name   ON fact_plan_sales (organization_name)",
            "CREATE INDEX IF NOT EXISTS idx_fps_dept_name  ON fact_plan_sales (department_name)",
        ]:
            try: cur.execute(idx); conn.commit()
            except Exception: conn.rollback()

        # ── plan_generation_log ──────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_generation_log (
                generation_id       SERIAL PRIMARY KEY,
                scenario_id         INTEGER NOT NULL,
                version_id          INTEGER NOT NULL,
                base_period_from    DATE,
                base_period_to      DATE,
                target_period_from  DATE,
                target_period_to    DATE,
                global_revenue_pct  NUMERIC(8,4) DEFAULT 0,
                global_volume_pct   NUMERIC(8,4) DEFAULT 0,
                global_price_pct    NUMERIC(8,4) DEFAULT 0,
                filters_json        JSONB,
                applied_rules_json  JSONB,
                applied_rules_count INTEGER DEFAULT 0,
                generated_rows      INTEGER DEFAULT 0,
                deleted_rows        INTEGER DEFAULT 0,
                months_processed    INTEGER DEFAULT 0,
                started_at          TIMESTAMP DEFAULT NOW(),
                finished_at         TIMESTAMP,
                status              TEXT NOT NULL DEFAULT 'running',
                error_message       TEXT,
                created_by          INTEGER,
                created_by_name     TEXT
            )
        """)
        for col, defn in [
            ("global_revenue_pct",  "NUMERIC(8,4) DEFAULT 0"),
            ("global_volume_pct",   "NUMERIC(8,4) DEFAULT 0"),
            ("global_price_pct",    "NUMERIC(8,4) DEFAULT 0"),
            ("applied_rules_count", "INTEGER DEFAULT 0"),
            ("applied_rules_json",  "JSONB"),
            ("created_by_name",     "TEXT"),
            ("replace_existing",    "BOOLEAN DEFAULT TRUE"),
            ("generation_method",   "TEXT DEFAULT 'rule_engine'"),
            ("rows_without_rules",  "INTEGER DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE plan_generation_log ADD COLUMN IF NOT EXISTS {col} {defn}")
                conn.commit()
            except Exception:
                conn.rollback()

        # ── plan_rule ────────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_rule (
                rule_id        SERIAL PRIMARY KEY,
                scenario_id    INTEGER NOT NULL,
                version_id     INTEGER NOT NULL,
                rule_name      TEXT    NOT NULL,
                rule_type      TEXT    NOT NULL,
                period_from    DATE,
                period_to      DATE,
                effect_percent NUMERIC(8,4) NOT NULL DEFAULT 0,
                priority       INTEGER NOT NULL DEFAULT 100,
                is_active      BOOLEAN DEFAULT TRUE,
                created_at     TIMESTAMP DEFAULT NOW(),
                created_by     INTEGER,
                updated_at     TIMESTAMP DEFAULT NOW()
            )
        """)
        for col, defn in [
            ("period_from", "DATE"),
            ("period_to",   "DATE"),
            ("updated_at",  "TIMESTAMP DEFAULT NOW()"),
        ]:
            try:
                cur.execute(f"ALTER TABLE plan_rule ADD COLUMN IF NOT EXISTS {col} {defn}")
                conn.commit()
            except Exception:
                conn.rollback()

        # Drop NOT NULL constraints on deprecated columns (now moved to plan_rule_effects)
        for col in ["rule_type", "effect_percent", "priority"]:
            try:
                cur.execute(f"ALTER TABLE plan_rule ALTER COLUMN {col} DROP NOT NULL")
                conn.commit()
            except Exception:
                conn.rollback()

        # Migrate old rule_type names
        try:
            cur.execute("UPDATE plan_rule SET rule_type='revenue_effect_pct' WHERE rule_type='growth_pct'")
            conn.commit()
        except Exception:
            conn.rollback()

        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_pr_scen_ver ON plan_rule (scenario_id, version_id, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_pr_period   ON plan_rule (period_from, period_to)",
        ]:
            try: cur.execute(idx); conn.commit()
            except Exception: conn.rollback()

        # ── plan_rule_scope ──────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_rule_scope (
                scope_id        SERIAL PRIMARY KEY,
                rule_id         INTEGER NOT NULL,
                dimension_type  TEXT    NOT NULL,
                dimension_value TEXT    NOT NULL DEFAULT '',
                dimension_label TEXT
            )
        """)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='plan_rule_scope'::regclass AND conname='fk_prs_rule')
                THEN
                    ALTER TABLE plan_rule_scope ADD CONSTRAINT fk_prs_rule
                    FOREIGN KEY (rule_id) REFERENCES plan_rule(rule_id) ON DELETE CASCADE;
                END IF;
            END $$
        """)
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_prs_rule_id  ON plan_rule_scope (rule_id)",
            "CREATE INDEX IF NOT EXISTS idx_prs_dim_type ON plan_rule_scope (dimension_type, dimension_value)",
        ]:
            try: cur.execute(idx); conn.commit()
            except Exception: conn.rollback()

        # Migrate old scope_type/scope_value → plan_rule_scope (idempotent)
        try:
            cur.execute("""
                INSERT INTO plan_rule_scope (rule_id, dimension_type, dimension_value, dimension_label)
                SELECT rule_id, scope_type, COALESCE(scope_value,''), COALESCE(scope_value,'')
                FROM plan_rule
                WHERE scope_type IS NOT NULL
                  AND scope_type NOT IN ('all','')
                  AND scope_value IS NOT NULL AND scope_value != ''
                  AND NOT EXISTS (SELECT 1 FROM plan_rule_scope prs WHERE prs.rule_id = plan_rule.rule_id)
            """)
            conn.commit()
        except Exception:
            conn.rollback()

        # ── plan_rule_effects ──────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_rule_effects (
                effect_id       SERIAL PRIMARY KEY,
                rule_id         INTEGER NOT NULL,
                period_from     DATE,
                period_to       DATE,
                rule_type       TEXT    NOT NULL,
                effect_percent  NUMERIC(8,4) NOT NULL DEFAULT 0,
                priority        INTEGER NOT NULL DEFAULT 100,
                is_active       BOOLEAN DEFAULT TRUE,
                created_at      TIMESTAMP DEFAULT NOW(),
                updated_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='plan_rule_effects'::regclass AND conname='fk_pre_rule')
                THEN
                    ALTER TABLE plan_rule_effects ADD CONSTRAINT fk_pre_rule
                    FOREIGN KEY (rule_id) REFERENCES plan_rule(rule_id) ON DELETE CASCADE;
                END IF;
            END $$
        """)
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_pre_rule_id ON plan_rule_effects (rule_id)",
            "CREATE INDEX IF NOT EXISTS idx_pre_period  ON plan_rule_effects (period_from, period_to)",
            "CREATE INDEX IF NOT EXISTS idx_pre_type    ON plan_rule_effects (rule_type, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_pre_active  ON plan_rule_effects (rule_id, is_active)",
        ]:
            try: cur.execute(idx); conn.commit()
            except Exception: conn.rollback()

        # ── Migration: plan_rule → plan_rule_effects (idempotent via effects_migrated flag)
        try:
            cur.execute("ALTER TABLE plan_rule ADD COLUMN IF NOT EXISTS effects_migrated BOOLEAN DEFAULT FALSE")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("""
                INSERT INTO plan_rule_effects
                    (rule_id, period_from, period_to, rule_type, effect_percent, priority, is_active, created_at)
                SELECT rule_id, period_from, period_to, rule_type,
                       COALESCE(effect_percent, 0),
                       COALESCE(priority, 100),
                       COALESCE(is_active, TRUE),
                       created_at
                FROM plan_rule
                WHERE COALESCE(effects_migrated, FALSE) = FALSE
                  AND rule_type IS NOT NULL AND rule_type != ''
            """)
            cur.execute("UPDATE plan_rule SET effects_migrated=TRUE WHERE COALESCE(effects_migrated,FALSE)=FALSE AND rule_type IS NOT NULL AND rule_type!=''")
            conn.commit()
        except Exception:
            conn.rollback()

        conn.commit()
        print("[startup] ensure_planning_tables: done")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_planning_tables failed: {exc}") from exc
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ROW MAPPERS
# ══════════════════════════════════════════════════════════════════════════════

def _s(v): return str(v) if v else None

def _scenario_dict(r) -> dict:
    return {"scenario_id": r[0], "scenario_code": r[1], "scenario_name": r[2],
            "scenario_type": r[3], "description": r[4], "is_active": r[5],
            "created_at": _s(r[6]), "created_by": r[7]}

def _version_dict(r) -> dict:
    return {"version_id": r[0], "scenario_id": r[1], "version_number": r[2],
            "version_name": r[3], "description": r[4], "is_locked": r[5],
            "locked_at": _s(r[6]), "created_at": _s(r[7]), "created_by": r[8]}

def _rule_dict(r, scopes=None, effects=None) -> dict:
    return {"rule_id": r[0], "scenario_id": r[1], "version_id": r[2],
            "rule_name": r[3], "is_active": r[4],
            "created_at": _s(r[5]), "created_by": r[6],
            "scopes": scopes or [], "effects": effects or []}

def _scope_dict(r) -> dict:
    return {"scope_id": r[0], "rule_id": r[1],
            "dimension_type": r[2], "dimension_value": r[3], "dimension_label": r[4]}

def _effect_dict(r) -> dict:
    return {"effect_id": r[0], "rule_id": r[1],
            "period_from": _s(r[2]), "period_to": _s(r[3]),
            "rule_type": r[4],
            "effect_percent": float(r[5]) if r[5] is not None else 0.0,
            "priority": r[6], "is_active": r[7], "created_at": _s(r[8])}

def _genlog_dict(r) -> dict:
    return {
        "generation_id": r[0], "scenario_id": r[1], "version_id": r[2],
        "base_period_from": _s(r[3]), "base_period_to": _s(r[4]),
        "target_period_from": _s(r[5]), "target_period_to": _s(r[6]),
        "global_revenue_pct":  float(r[7])  if r[7]  is not None else 0,
        "global_volume_pct":   float(r[8])  if r[8]  is not None else 0,
        "global_price_pct":    float(r[9])  if r[9]  is not None else 0,
        "filters_json": r[10], "applied_rules_json": r[11],
        "applied_rules_count": r[12] or 0, "generated_rows": r[13] or 0,
        "deleted_rows": r[14] or 0, "months_processed": r[15] or 0,
        "started_at": _s(r[16]), "finished_at": _s(r[17]),
        "status": r[18], "error_message": r[19],
        "created_by": r[20], "created_by_name": r[21],
        "replace_existing":   r[22] if r[22] is not None else True,
        "generation_method":  r[23] if r[23] is not None else "rule_engine",
        "rows_without_rules": r[24] if r[24] is not None else 0,
    }

_SC_COLS     = "scenario_id, scenario_code, scenario_name, scenario_type, description, is_active, created_at, created_by"
_VER_COLS    = "version_id, scenario_id, version_number, version_name, description, is_locked, locked_at, created_at, created_by"
_RULE_COLS   = "rule_id, scenario_id, version_id, rule_name, is_active, created_at, created_by"
_EFFECT_COLS = "effect_id, rule_id, period_from, period_to, rule_type, effect_percent, priority, is_active, created_at"
_GENLOG_COLS = """
    generation_id, scenario_id, version_id,
    base_period_from, base_period_to, target_period_from, target_period_to,
    global_revenue_pct, global_volume_pct, global_price_pct,
    filters_json, applied_rules_json, applied_rules_count,
    generated_rows, deleted_rows, months_processed,
    started_at, finished_at, status, error_message, created_by, created_by_name,
    replace_existing, generation_method, rows_without_rules
"""


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO CRUD
# ══════════════════════════════════════════════════════════════════════════════

def get_scenarios(page=1, page_size=50, is_active=None, scenario_type=None) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds, params = [], []
        if is_active is not None: conds.append("is_active=%s"); params.append(is_active)
        if scenario_type: conds.append("scenario_type=%s"); params.append(scenario_type)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        cur.execute(f"SELECT COUNT(*) FROM dim_scenario {where}", params)
        total = int(cur.fetchone()[0])
        eff = min(max(page_size,1),200); offset=(page-1)*eff
        cur.execute(f"SELECT {_SC_COLS} FROM dim_scenario {where} ORDER BY scenario_id DESC LIMIT %s OFFSET %s", params+[eff,offset])
        return {"total":total,"page":page,"page_size":eff,"total_pages":max(1,(total+eff-1)//eff),"rows":[_scenario_dict(r) for r in cur.fetchall()]}
    finally: cur.close(); conn.close()

def create_scenario(scenario_code, scenario_name, scenario_type="draft", description=None, created_by=None) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"INSERT INTO dim_scenario (scenario_code,scenario_name,scenario_type,description,created_by) VALUES (%s,%s,%s,%s,%s) RETURNING {_SC_COLS}", (scenario_code,scenario_name,scenario_type,description,created_by))
        r=cur.fetchone(); conn.commit(); return _scenario_dict(r)
    finally: cur.close(); conn.close()

def get_scenario(scenario_id) -> Optional[dict]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_SC_COLS} FROM dim_scenario WHERE scenario_id=%s", (scenario_id,))
        r=cur.fetchone(); return _scenario_dict(r) if r else None
    finally: cur.close(); conn.close()

def update_scenario(scenario_id, scenario_name=None, description=None, is_active=None) -> Optional[dict]:
    conn = get_connection(); cur = conn.cursor()
    try:
        sets,params=[],[]
        if scenario_name is not None: sets.append("scenario_name=%s"); params.append(scenario_name)
        if description is not None:   sets.append("description=%s");   params.append(description)
        if is_active is not None:     sets.append("is_active=%s");     params.append(is_active)
        if not sets: return get_scenario(scenario_id)
        params.append(scenario_id)
        cur.execute(f"UPDATE dim_scenario SET {','.join(sets)} WHERE scenario_id=%s RETURNING {_SC_COLS}",params)
        r=cur.fetchone(); conn.commit(); return _scenario_dict(r) if r else None
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# VERSION CRUD
# ══════════════════════════════════════════════════════════════════════════════

def get_versions(scenario_id, page=1, page_size=50) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM scenario_version WHERE scenario_id=%s",(scenario_id,))
        total=int(cur.fetchone()[0]); eff=min(max(page_size,1),200); offset=(page-1)*eff
        cur.execute(f"SELECT {_VER_COLS} FROM scenario_version WHERE scenario_id=%s ORDER BY version_number DESC LIMIT %s OFFSET %s",(scenario_id,eff,offset))
        return {"total":total,"page":page,"page_size":eff,"total_pages":max(1,(total+eff-1)//eff),"rows":[_version_dict(r) for r in cur.fetchall()]}
    finally: cur.close(); conn.close()

def create_version(scenario_id, version_name, description=None, created_by=None) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM scenario_version WHERE scenario_id=%s",(scenario_id,))
        n=cur.fetchone()[0]
        cur.execute(f"INSERT INTO scenario_version (scenario_id,version_number,version_name,description,created_by) VALUES (%s,%s,%s,%s,%s) RETURNING {_VER_COLS}",(scenario_id,n,version_name,description,created_by))
        r=cur.fetchone(); conn.commit(); return _version_dict(r)
    finally: cur.close(); conn.close()

def get_version(version_id) -> Optional[dict]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_VER_COLS} FROM scenario_version WHERE version_id=%s",(version_id,))
        r=cur.fetchone(); return _version_dict(r) if r else None
    finally: cur.close(); conn.close()

def lock_version(version_id, locked_by=None) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"UPDATE scenario_version SET is_locked=TRUE,locked_at=NOW(),locked_by=%s WHERE version_id=%s RETURNING {_VER_COLS}",(locked_by,version_id))
        r=cur.fetchone(); conn.commit()
        if not r: raise ValueError(f"Version {version_id} not found")
        return _version_dict(r)
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PLAN RULE CRUD  (with scopes)
# ══════════════════════════════════════════════════════════════════════════════

def _load_scopes(cur, rule_ids: list) -> dict:
    if not rule_ids: return {}
    cur.execute("SELECT scope_id,rule_id,dimension_type,dimension_value,dimension_label FROM plan_rule_scope WHERE rule_id=ANY(%s) ORDER BY scope_id",(rule_ids,))
    result: dict = {rid: [] for rid in rule_ids}
    for r in cur.fetchall(): result[r[1]].append(_scope_dict(r))
    return result

def _load_effects(cur, rule_ids: list) -> dict:
    if not rule_ids: return {}
    cur.execute(f"SELECT {_EFFECT_COLS} FROM plan_rule_effects WHERE rule_id=ANY(%s) ORDER BY priority ASC, effect_id ASC", (rule_ids,))
    result: dict = {rid: [] for rid in rule_ids}
    for r in cur.fetchall(): result[r[1]].append(_effect_dict(r))
    return result


def get_rules(scenario_id: int, version_id: int, is_active: Optional[bool] = None) -> list:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds=["scenario_id=%s","version_id=%s"]; params: list=[scenario_id,version_id]
        if is_active is not None: conds.append("is_active=%s"); params.append(is_active)
        cur.execute(f"SELECT {_RULE_COLS} FROM plan_rule WHERE {' AND '.join(conds)} ORDER BY rule_id ASC",params)
        rows=cur.fetchall(); rule_ids=[r[0] for r in rows]
        scopes_map=_load_scopes(cur,rule_ids)
        effects_map=_load_effects(cur,rule_ids)
        return [_rule_dict(r,scopes_map.get(r[0],[]),effects_map.get(r[0],[])) for r in rows]
    finally: cur.close(); conn.close()


def _validate_effect(rule_type=None, effect_percent=None, priority=None, period_from=None, period_to=None):
    errors = []
    if rule_type is not None and rule_type not in VALID_RULE_TYPES:
        errors.append(f"rule_type must be one of: {sorted(VALID_RULE_TYPES)}")
    if effect_percent is not None:
        ep = float(effect_percent)
        if ep < -100 or ep > 1000:
            errors.append(f"effect_percent must be between -100 and 1000 (got {ep})")
    if priority is not None and int(priority) < 0:
        errors.append(f"priority must be >= 0 (got {priority})")
    if period_from is not None and period_to is not None and period_from > period_to:
        errors.append(f"period_from ({period_from}) must be <= period_to ({period_to})")
    if errors:
        raise ValueError("; ".join(errors))


def create_rule(
    scenario_id: int, version_id: int, rule_name: str,
    scopes: Optional[list]=None, effects: Optional[list]=None,
    created_by: Optional[int]=None,
) -> dict:
    if not rule_name or not rule_name.strip():
        raise ValueError("rule_name cannot be empty")
    if scopes:
        _validate_rule(scopes=scopes)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM dim_scenario WHERE scenario_id=%s AND is_active=TRUE", (scenario_id,))
        if not cur.fetchone():
            raise ValueError(f"Scenario {scenario_id} not found or inactive")
        cur.execute("SELECT is_locked FROM scenario_version WHERE version_id=%s AND scenario_id=%s", (version_id, scenario_id))
        v = cur.fetchone()
        if not v:   raise ValueError(f"Version {version_id} not found for scenario {scenario_id}")
        if v[0]:    raise ValueError(f"Version {version_id} is locked — cannot add rules")
        cur.execute(
            f"INSERT INTO plan_rule (scenario_id,version_id,rule_name,created_by) VALUES (%s,%s,%s,%s) RETURNING {_RULE_COLS}",
            (scenario_id, version_id, rule_name.strip(), created_by)
        )
        rule_row=cur.fetchone(); rule_id=rule_row[0]
        saved_scopes=[]
        for sc in (scopes or []):
            dt=sc.get("dimension_type","all"); dv=sc.get("dimension_value",""); dl=sc.get("dimension_label") or dv
            if dt not in VALID_SCOPE_TYPES: continue
            cur.execute("INSERT INTO plan_rule_scope (rule_id,dimension_type,dimension_value,dimension_label) VALUES (%s,%s,%s,%s) RETURNING scope_id,rule_id,dimension_type,dimension_value,dimension_label",(rule_id,dt,dv,dl))
            saved_scopes.append(_scope_dict(cur.fetchone()))
        saved_effects=[]
        for i, ef in enumerate(effects or []):
            rt = ef.get("rule_type")
            pf = ef.get("period_from")
            pt = ef.get("period_to")
            if not rt:  raise ValueError(f"effect[{i}]: rule_type є обов'язковим")
            if not pf:  raise ValueError(f"effect[{i}]: period_from є обов'язковим")
            if not pt:  raise ValueError(f"effect[{i}]: period_to є обов'язковим")
            _validate_effect(rule_type=rt, effect_percent=ef.get("effect_percent"),
                             priority=ef.get("priority"), period_from=pf, period_to=pt)
            cur.execute(
                f"INSERT INTO plan_rule_effects (rule_id,period_from,period_to,rule_type,effect_percent,priority,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_EFFECT_COLS}",
                (rule_id, pf, pt, rt, ef.get("effect_percent", 0), ef.get("priority", 100), ef.get("is_active", True))
            )
            saved_effects.append(_effect_dict(cur.fetchone()))
        conn.commit()
        return _rule_dict(rule_row, saved_scopes, saved_effects)
    finally: cur.close(); conn.close()


def update_rule(
    rule_id: int, rule_name=None, is_active=None, scopes=None,
) -> Optional[dict]:
    if rule_name is not None and not str(rule_name).strip():
        raise ValueError("rule_name cannot be empty")
    if scopes is not None:
        _validate_rule(scopes=scopes)
    conn = get_connection(); cur = conn.cursor()
    try:
        sets,params=[],[]
        if rule_name is not None: sets.append("rule_name=%s"); params.append(rule_name.strip())
        if is_active is not None: sets.append("is_active=%s"); params.append(is_active)
        if sets:
            sets.append("updated_at=NOW()"); params.append(rule_id)
            cur.execute(f"UPDATE plan_rule SET {','.join(sets)} WHERE rule_id=%s RETURNING {_RULE_COLS}", params)
            rule_row=cur.fetchone()
        else:
            cur.execute(f"SELECT {_RULE_COLS} FROM plan_rule WHERE rule_id=%s",(rule_id,))
            rule_row=cur.fetchone()
        if not rule_row: return None
        if scopes is not None:
            cur.execute("DELETE FROM plan_rule_scope WHERE rule_id=%s",(rule_id,))
            saved_scopes=[]
            for sc in scopes:
                dt=sc.get("dimension_type","all"); dv=sc.get("dimension_value",""); dl=sc.get("dimension_label") or dv
                if dt not in VALID_SCOPE_TYPES: continue
                cur.execute("INSERT INTO plan_rule_scope (rule_id,dimension_type,dimension_value,dimension_label) VALUES (%s,%s,%s,%s) RETURNING scope_id,rule_id,dimension_type,dimension_value,dimension_label",(rule_id,dt,dv,dl))
                saved_scopes.append(_scope_dict(cur.fetchone()))
        else:
            saved_scopes=_load_scopes(cur,[rule_id]).get(rule_id,[])
        effects=_load_effects(cur,[rule_id]).get(rule_id,[])
        conn.commit()
        return _rule_dict(rule_row, saved_scopes, effects)
    finally: cur.close(); conn.close()


def delete_rule(rule_id: int) -> bool:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM plan_rule WHERE rule_id=%s",(rule_id,))
        n=cur.rowcount; conn.commit(); return n>0
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# EFFECT CRUD
# ══════════════════════════════════════════════════════════════════════════════

def create_effect(
    rule_id: int, rule_type: str, effect_percent: float=0.0,
    period_from=None, period_to=None, priority: int=100,
    is_active: bool=True,
) -> dict:
    if not rule_type:   raise ValueError("rule_type є обов'язковим")
    if not period_from: raise ValueError("period_from є обов'язковим")
    if not period_to:   raise ValueError("period_to є обов'язковим")
    _validate_effect(rule_type=rule_type, effect_percent=effect_percent,
                     priority=priority, period_from=period_from, period_to=period_to)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT 1 FROM plan_rule WHERE rule_id=%s",(rule_id,))
        if not cur.fetchone(): raise ValueError(f"Rule {rule_id} not found")
        cur.execute(
            f"INSERT INTO plan_rule_effects (rule_id,period_from,period_to,rule_type,effect_percent,priority,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_EFFECT_COLS}",
            (rule_id, period_from, period_to, rule_type, effect_percent, priority, is_active)
        )
        r=cur.fetchone(); conn.commit(); return _effect_dict(r)
    finally: cur.close(); conn.close()


def update_effect(
    effect_id: int, rule_type=None, effect_percent=None,
    period_from=None, period_to=None, clear_period=False,
    priority=None, is_active=None,
) -> Optional[dict]:
    _validate_effect(rule_type=rule_type, effect_percent=effect_percent,
                     priority=priority, period_from=period_from, period_to=period_to)
    conn = get_connection(); cur = conn.cursor()
    try:
        sets,params=[],[]
        if rule_type is not None:      sets.append("rule_type=%s");      params.append(rule_type)
        if effect_percent is not None: sets.append("effect_percent=%s"); params.append(effect_percent)
        if period_from is not None:    sets.append("period_from=%s");    params.append(period_from)
        if period_to is not None:      sets.append("period_to=%s");      params.append(period_to)
        if clear_period:               sets.extend(["period_from=NULL","period_to=NULL"])
        if priority is not None:       sets.append("priority=%s");       params.append(priority)
        if is_active is not None:      sets.append("is_active=%s");      params.append(is_active)
        if not sets:
            cur.execute(f"SELECT {_EFFECT_COLS} FROM plan_rule_effects WHERE effect_id=%s",(effect_id,))
        else:
            sets.append("updated_at=NOW()"); params.append(effect_id)
            cur.execute(f"UPDATE plan_rule_effects SET {','.join(sets)} WHERE effect_id=%s RETURNING {_EFFECT_COLS}", params)
        r=cur.fetchone()
        if not r: return None
        conn.commit(); return _effect_dict(r)
    finally: cur.close(); conn.close()


def delete_effect(effect_id: int) -> bool:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM plan_rule_effects WHERE effect_id=%s",(effect_id,))
        n=cur.rowcount; conn.commit(); return n>0
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# COPY RULE
# ══════════════════════════════════════════════════════════════════════════════

def copy_rule(rule_id: int, created_by: Optional[int]=None) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_RULE_COLS} FROM plan_rule WHERE rule_id=%s",(rule_id,))
        orig=cur.fetchone()
        if not orig: raise ValueError(f"Rule {rule_id} not found")
        new_name=orig[3] + " copy"
        cur.execute(
            f"INSERT INTO plan_rule (scenario_id,version_id,rule_name,is_active,created_by) VALUES (%s,%s,%s,FALSE,%s) RETURNING {_RULE_COLS}",
            (orig[1], orig[2], new_name, created_by)
        )
        new_row=cur.fetchone(); new_id=new_row[0]
        cur.execute("SELECT dimension_type,dimension_value,dimension_label FROM plan_rule_scope WHERE rule_id=%s",(rule_id,))
        saved_scopes=[]
        for sc in cur.fetchall():
            cur.execute("INSERT INTO plan_rule_scope (rule_id,dimension_type,dimension_value,dimension_label) VALUES (%s,%s,%s,%s) RETURNING scope_id,rule_id,dimension_type,dimension_value,dimension_label",(new_id,)+sc)
            saved_scopes.append(_scope_dict(cur.fetchone()))
        cur.execute(f"SELECT {_EFFECT_COLS} FROM plan_rule_effects WHERE rule_id=%s",(rule_id,))
        saved_effects=[]
        for ef in cur.fetchall():
            cur.execute(
                f"INSERT INTO plan_rule_effects (rule_id,period_from,period_to,rule_type,effect_percent,priority,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_EFFECT_COLS}",
                (new_id, ef[2], ef[3], ef[4], ef[5], ef[6], ef[7])
            )
            saved_effects.append(_effect_dict(cur.fetchone()))
        conn.commit()
        return _rule_dict(new_row, saved_scopes, saved_effects)
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# DIMENSION OPTIONS  (scope builder + plan filters)
# ══════════════════════════════════════════════════════════════════════════════

_DIM_SQL = {
    "holding":           "SELECT DISTINCT holding_name          AS v FROM dim_department WHERE holding_name IS NOT NULL AND holding_name != '' {s} ORDER BY v LIMIT %s",
    "organization":      "SELECT DISTINCT organization_name     AS v FROM dim_department WHERE organization_name IS NOT NULL AND organization_name != '' {s} ORDER BY v LIMIT %s",
    "region":            "SELECT DISTINCT region_name           AS v FROM dim_department WHERE region_name IS NOT NULL AND region_name != '' {s} ORDER BY v LIMIT %s",
    "branch":            "SELECT DISTINCT branch_name           AS v FROM dim_department WHERE branch_name IS NOT NULL AND branch_name != '' {s} ORDER BY v LIMIT %s",
    "parent_department": "SELECT DISTINCT parent_department_name AS v FROM dim_department WHERE parent_department_name IS NOT NULL AND parent_department_name != '' {s} ORDER BY v LIMIT %s",
    "department":        "SELECT DISTINCT department_name       AS v FROM dim_department WHERE department_name IS NOT NULL AND COALESCE(is_deleted,FALSE)=FALSE {s} ORDER BY v LIMIT %s",
    "product_group":     "SELECT DISTINCT product_group_name    AS v FROM fact_turnover WHERE product_group_name IS NOT NULL AND product_group_name != '' {s} ORDER BY v LIMIT %s",
    "brand":             "SELECT DISTINCT brand_name            AS v FROM dim_brand WHERE brand_name IS NOT NULL AND brand_name != '' AND COALESCE(is_active,TRUE)=TRUE {s} ORDER BY v LIMIT %s",
}
_DIM_COL = {
    "holding": "holding_name", "organization": "organization_name", "region": "region_name",
    "branch": "branch_name", "parent_department": "parent_department_name",
    "department": "department_name", "product_group": "product_group_name", "brand": "brand_name",
}


def get_dim_options(dim_type: str, search: str = "", limit: int = 50) -> list:
    if dim_type not in _DIM_SQL: return []
    conn = get_connection(); cur = conn.cursor()
    try:
        col = _DIM_COL[dim_type]
        sql_tmpl = _DIM_SQL[dim_type]
        if search and search.strip():
            sql = sql_tmpl.format(s=f"AND {col} ILIKE %s")
            params: list = [f"%{search.strip()}%", min(limit,200)]
        else:
            sql = sql_tmpl.format(s="")
            params = [min(limit,200)]
        cur.execute(sql, params)
        return [{"value": r[0], "label": r[0]} for r in cur.fetchall()]
    finally: cur.close(); conn.close()


def get_plan_filter_options(field: str, scenario_id: int, version_id: int, search: str = "", limit: int = 50) -> list:
    """Distinct values for a field from fact_plan_sales for the Fact vs Plan filter bar."""
    allowed = {
        "region_name","branch_name","holding_name","organization_name",
        "parent_department_name","department_name","department_uid",
        "product_group_name","product_group_uid","brand_name","brand_uid",
    }
    if field not in allowed: return []
    conn = get_connection(); cur = conn.cursor()
    try:
        conds=["scenario_id=%s","version_id=%s",f"{field} IS NOT NULL",f"{field} != ''"]
        params: list=[scenario_id,version_id]
        if search and search.strip():
            conds.append(f"{field} ILIKE %s"); params.append(f"%{search.strip()}%")
        where=" AND ".join(conds)
        cur.execute(f"SELECT DISTINCT {field} FROM fact_plan_sales WHERE {where} ORDER BY {field} LIMIT %s", params+[min(limit,200)])
        return [{"value": r[0], "label": r[0]} for r in cur.fetchall()]
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# GENERATION LOG
# ══════════════════════════════════════════════════════════════════════════════

def get_generation_status(generation_id: int) -> Optional[dict]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_GENLOG_COLS} FROM plan_generation_log WHERE generation_id=%s",(generation_id,))
        r=cur.fetchone(); return _genlog_dict(r) if r else None
    finally: cur.close(); conn.close()


def get_generation_log(scenario_id=None, version_id=None, page=1, page_size=20) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds,params=[],[]
        if scenario_id: conds.append("scenario_id=%s"); params.append(scenario_id)
        if version_id:  conds.append("version_id=%s");  params.append(version_id)
        where=("WHERE "+" AND ".join(conds)) if conds else ""
        cur.execute(f"SELECT COUNT(*) FROM plan_generation_log {where}",params)
        total=int(cur.fetchone()[0]); eff=min(max(page_size,1),100); offset=(page-1)*eff
        cur.execute(f"SELECT {_GENLOG_COLS} FROM plan_generation_log {where} ORDER BY generation_id DESC LIMIT %s OFFSET %s",params+[eff,offset])
        return {"total":total,"page":page,"page_size":eff,"total_pages":max(1,(total+eff-1)//eff),"rows":[_genlog_dict(r) for r in cur.fetchall()]}
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# GENERATE FIRST DRAFT — full rule engine
# ══════════════════════════════════════════════════════════════════════════════

def generate_first_draft(
    scenario_id: int, version_id: int,
    base_period_from: date, base_period_to: date,
    target_period_from: date, target_period_to: date,
    global_revenue_pct: float = 0.0,
    global_volume_pct:  float = 0.0,
    global_price_pct:   float = 0.0,
    department_uids:    Optional[List[str]] = None,
    product_group_uids: Optional[List[str]] = None,
    source_ids:         Optional[List[int]] = None,
    replace_existing:   bool = True,
    created_by:         Optional[int] = None,
    created_by_name:    Optional[str] = None,
) -> dict:
    """
    Pure-SQL batch generation per month:

    1. Enrich: JOIN fact_turnover with dim_department + dim_brand via mapping tables.
    2. Match:  CROSS JOIN active rules, NOT EXISTS scope check (all scopes must match).
    3. Pivot:  Best rule per rule_type per row (priority DESC wins).
    4. Calculate: plan = fact × effective_multipliers.
    5. INSERT: batch INSERT with ON CONFLICT UPDATE.
    """
    conn = get_connection()
    cur  = conn.cursor()

    # Validation
    cur.execute("SELECT scenario_id FROM dim_scenario WHERE scenario_id=%s AND is_active=TRUE",(scenario_id,))
    if not cur.fetchone(): cur.close(); conn.close(); raise ValueError(f"Scenario {scenario_id} not found or inactive")
    cur.execute("SELECT is_locked FROM scenario_version WHERE version_id=%s AND scenario_id=%s",(version_id,scenario_id))
    v_row=cur.fetchone()
    if not v_row: cur.close(); conn.close(); raise ValueError(f"Version {version_id} not found")
    if v_row[0]:  cur.close(); conn.close(); raise ValueError(f"Version {version_id} is locked")
    if base_period_from > base_period_to:   cur.close(); conn.close(); raise ValueError("base_period_from > base_period_to")
    if target_period_from > target_period_to: cur.close(); conn.close(); raise ValueError("target_period_from > target_period_to")

    # Snapshot active rules with their effects
    cur.execute(f"SELECT {_RULE_COLS} FROM plan_rule WHERE scenario_id=%s AND version_id=%s AND is_active=TRUE ORDER BY rule_id ASC",(scenario_id,version_id))
    rule_rows=cur.fetchall(); rule_ids=[r[0] for r in rule_rows]
    scopes_map=_load_scopes(cur,rule_ids)
    effects_map=_load_effects(cur,rule_ids)
    rules_snapshot=[_rule_dict(r,scopes_map.get(r[0],[]),effects_map.get(r[0],[])) for r in rule_rows]

    year_offset=target_period_from.year - base_period_from.year
    filters_json=json.dumps({"department_uids":department_uids,"product_group_uids":product_group_uids,"source_ids":source_ids},default=str)

    cur.execute(
        f"""INSERT INTO plan_generation_log
               (scenario_id,version_id,base_period_from,base_period_to,
                target_period_from,target_period_to,
                global_revenue_pct,global_volume_pct,global_price_pct,
                filters_json,applied_rules_json,applied_rules_count,
                replace_existing,generation_method,
                status,created_by,created_by_name)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,'rule_engine','running',%s,%s)
           RETURNING generation_id""",
        (scenario_id,version_id,base_period_from,base_period_to,
         target_period_from,target_period_to,
         global_revenue_pct,global_volume_pct,global_price_pct,
         filters_json,json.dumps(rules_snapshot,default=str),sum(len(r.get("effects",[])) for r in rules_snapshot),
         replace_existing,
         created_by,created_by_name)
    )
    generation_id=cur.fetchone()[0]; conn.commit()

    total_generated=total_deleted=months_done=0
    try:
        dept_cond  = " AND ft.department_uid   = ANY(%s)" if department_uids    else ""
        pg_cond    = " AND ft.product_group_uid = ANY(%s)" if product_group_uids else ""
        src_cond   = " AND ft.source_id         = ANY(%s)" if source_ids         else ""
        extra = (([department_uids] if department_uids else []) +
                 ([product_group_uids] if product_group_uids else []) +
                 ([source_ids] if source_ids else []))

        if replace_existing:
            cur.execute("DELETE FROM fact_plan_sales WHERE scenario_id=%s AND version_id=%s AND period_month BETWEEN %s AND %s",(scenario_id,version_id,target_period_from,target_period_to))
            total_deleted=cur.rowcount; conn.commit()

        cur.execute(f"SELECT DISTINCT period_month FROM fact_turnover ft WHERE ft.period_month BETWEEN %s AND %s {dept_cond}{pg_cond}{src_cond} ORDER BY period_month",[base_period_from,base_period_to]+extra)
        base_months=[r[0] for r in cur.fetchall()]

        if not base_months:
            cur.execute("UPDATE plan_generation_log SET status='completed',finished_at=NOW(),generated_rows=0,deleted_rows=%s,months_processed=0 WHERE generation_id=%s",(total_deleted,generation_id))
            conn.commit()
            return {"generation_id":generation_id,"status":"completed","generated_rows":0,"deleted_rows":total_deleted,"months_processed":0,"rules_applied":0,"warning":"No fact data in base period"}

        for base_month in base_months:
            target_month=base_month.replace(year=base_month.year+year_offset)
            if not (target_period_from <= target_month <= target_period_to):
                continue

            # Full SQL: enrich → rule matching → pivot → calculate → batch INSERT
            cur.execute(f"""
                WITH
                enriched AS (
                    SELECT
                        ft.department_uid,
                        COALESCE(ft.department_uid,'') AS dept_uid_nn,
                        ft.product_group_uid,
                        COALESCE(ft.product_group_uid,'') AS pg_uid_nn,
                        ft.product_group_id,
                        ft.product_group_name,
                        ft.source_id,
                        COALESCE(dd.department_name, ft.department_name)  AS department_name,
                        COALESCE(dd.holding_name,         '')              AS holding_name,
                        COALESCE(dd.organization_name,    '')              AS organization_name,
                        COALESCE(dd.region_name,          '')              AS region_name,
                        COALESCE(dd.branch_name,          '')              AS branch_name,
                        COALESCE(dd.parent_department_name,'')             AS parent_dept_name,
                        dd.department_id                                    AS master_dept_id,
                        COALESCE(db.brand_name, ft.product_group_name)    AS brand_name,
                        COALESCE(db.brand_uid,  ft.product_group_uid)     AS brand_uid,
                        (dsm.master_department_id IS NULL)                  AS unmapped_dept,
                        (bsm.master_brand_id IS NULL)                       AS unmapped_brand,
                        COALESCE(ft.sales_vat,    0) AS sales_vat,
                        COALESCE(ft.sales_retail, 0) AS sales_retail,
                        COALESCE(ft.excise,       0) AS excise,
                        COALESCE(ft.sales_dal,    0) AS sales_dal,
                        COALESCE(ft.sales_kg,     0) AS sales_kg
                    FROM fact_turnover ft
                    LEFT JOIN department_source_mapping dsm
                           ON dsm.source_id           = ft.source_id
                          AND dsm.source_department_id = ft.department_uid
                    LEFT JOIN dim_department dd ON dd.department_id = dsm.master_department_id
                    LEFT JOIN brand_source_mapping bsm
                           ON bsm.source_id       = ft.source_id
                          AND bsm.source_brand_id  = ft.product_group_uid
                    LEFT JOIN dim_brand db ON db.id = bsm.master_brand_id
                    WHERE ft.period_month = %s
                      {dept_cond}{pg_cond}{src_cond}
                ),
                active_rules AS (
                    SELECT pr.rule_id, pre.effect_id, pre.rule_type, pre.effect_percent,
                           pre.priority, pre.created_at AS effect_created_at,
                           COUNT(prs.scope_id) AS scope_cnt
                    FROM plan_rule pr
                    JOIN plan_rule_effects pre
                         ON pre.rule_id = pr.rule_id AND pre.is_active = TRUE
                    LEFT JOIN plan_rule_scope prs ON prs.rule_id = pr.rule_id
                    WHERE pr.scenario_id=%s AND pr.version_id=%s AND pr.is_active=TRUE
                      AND (pre.period_from IS NULL OR %s >= pre.period_from)
                      AND (pre.period_to   IS NULL OR %s <= pre.period_to)
                    GROUP BY pr.rule_id, pre.effect_id, pre.rule_type,
                             pre.effect_percent, pre.priority, pre.created_at
                ),
                rule_matches AS (
                    SELECT
                        e.department_uid, e.product_group_uid, e.source_id,
                        ar.rule_id, ar.effect_id, ar.rule_type, ar.effect_percent,
                        ROW_NUMBER() OVER (
                            PARTITION BY e.department_uid, e.product_group_uid, e.source_id, ar.rule_type
                            ORDER BY ar.priority DESC, ar.scope_cnt DESC, ar.effect_created_at DESC
                        ) AS rn
                    FROM enriched e
                    CROSS JOIN active_rules ar
                    WHERE NOT EXISTS (
                        -- For each distinct dimension_type the row must match at least one scope (OR within dim)
                        -- AND all dimension_types must be satisfied (AND between dims)
                        SELECT 1
                        FROM (
                            SELECT DISTINCT dimension_type
                            FROM plan_rule_scope
                            WHERE rule_id = ar.rule_id AND dimension_type != 'all'
                        ) dim_types
                        WHERE NOT EXISTS (
                            SELECT 1 FROM plan_rule_scope prs
                            WHERE prs.rule_id = ar.rule_id
                              AND prs.dimension_type = dim_types.dimension_type
                              AND ({_SCOPE_MATCH_CASE})
                        )
                    )
                ),
                best_rules AS (SELECT * FROM rule_matches WHERE rn=1),
                effective AS (
                    SELECT
                        e.dept_uid_nn, e.pg_uid_nn, e.department_uid, e.product_group_uid,
                        e.product_group_id, e.product_group_name,
                        e.department_name, e.holding_name, e.organization_name,
                        e.region_name, e.branch_name, e.parent_dept_name,
                        e.master_dept_id, e.brand_name, e.brand_uid,
                        e.unmapped_dept, e.unmapped_brand,
                        e.sales_vat, e.sales_retail, e.excise, e.sales_dal, e.sales_kg,
                        COALESCE(MAX(CASE WHEN br.rule_type='revenue_effect_pct' THEN br.effect_percent END),%s) AS eff_rev,
                        COALESCE(MAX(CASE WHEN br.rule_type='volume_effect_pct'  THEN br.effect_percent END),%s) AS eff_vol,
                        COALESCE(MAX(CASE WHEN br.rule_type='price_effect_pct'   THEN br.effect_percent END),%s) AS eff_price,
                        COALESCE(
                            TO_JSONB(ARRAY_REMOVE(ARRAY_AGG(DISTINCT br.effect_id),NULL)),
                            '[]'::JSONB
                        ) AS applied_rule_ids
                    FROM enriched e
                    LEFT JOIN best_rules br USING (department_uid, product_group_uid, source_id)
                    GROUP BY
                        e.dept_uid_nn, e.pg_uid_nn, e.department_uid, e.product_group_uid,
                        e.product_group_id, e.product_group_name,
                        e.department_name, e.holding_name, e.organization_name,
                        e.region_name, e.branch_name, e.parent_dept_name,
                        e.master_dept_id, e.brand_name, e.brand_uid,
                        e.unmapped_dept, e.unmapped_brand,
                        e.sales_vat, e.sales_retail, e.excise, e.sales_dal, e.sales_kg
                )
                INSERT INTO fact_plan_sales (
                    scenario_id, version_id, period_month,
                    department_uid, department_id, department_name,
                    product_group_uid, product_group_id, product_group_name,
                    holding_name, organization_name, region_name, branch_name,
                    parent_department_name, brand_name, brand_uid,
                    unmapped_dept, unmapped_brand,
                    fact_sales_vat, fact_sales_retail, fact_excise, fact_sales_dal, fact_sales_kg,
                    baseline_sales_vat, baseline_sales_retail, baseline_kg, baseline_dal,
                    applied_revenue_effect_pct, applied_volume_effect_pct, applied_price_effect_pct,
                    applied_rule_ids_json,
                    fact_price_per_kg, fact_price_per_dal,
                    sales_kg_plan, sales_dal_plan, excise_plan,
                    sales_vat_plan, sales_retail_plan,
                    plan_price_per_kg, plan_price_per_dal,
                    base_year, generation_method, generation_id
                )
                SELECT
                    %s, %s, %s,
                    e.dept_uid_nn, e.master_dept_id, e.department_name,
                    e.pg_uid_nn, e.product_group_id, e.product_group_name,
                    e.holding_name, e.organization_name, e.region_name, e.branch_name,
                    e.parent_dept_name, e.brand_name, e.brand_uid,
                    e.unmapped_dept, e.unmapped_brand,
                    e.sales_vat, e.sales_retail, e.excise, e.sales_dal, e.sales_kg,
                    e.sales_vat, e.sales_retail, e.sales_kg, e.sales_dal,
                    e.eff_rev, e.eff_vol, e.eff_price, e.applied_rule_ids,
                    CASE WHEN e.sales_kg  > 0 THEN e.sales_vat / e.sales_kg  ELSE NULL END,
                    CASE WHEN e.sales_dal > 0 THEN e.sales_vat / e.sales_dal ELSE NULL END,
                    e.sales_kg  * (1 + e.eff_vol/100),
                    e.sales_dal * (1 + e.eff_vol/100),
                    e.excise    * (1 + e.eff_vol/100),
                    -- plan_sales_vat:
                    --   kg>0 → vol × price only (revenue_effect does not duplicate)
                    --   kg=0 → revenue_effect fallback
                    CASE WHEN e.sales_kg > 0
                        THEN e.sales_vat * (1+e.eff_vol/100) * (1+e.eff_price/100)
                        ELSE e.sales_vat * (1+e.eff_rev/100)
                    END,
                    CASE WHEN e.sales_kg > 0
                        THEN e.sales_retail * (1+e.eff_vol/100) * (1+e.eff_price/100)
                        ELSE e.sales_retail * (1+e.eff_rev/100)
                    END,
                    -- plan_price_per_kg = fact_price_per_kg × price_multiplier
                    CASE WHEN e.sales_kg  > 0
                        THEN (e.sales_vat/e.sales_kg)  * (1+e.eff_price/100)
                        ELSE NULL END,
                    CASE WHEN e.sales_dal > 0
                        THEN (e.sales_vat/e.sales_dal) * (1+e.eff_price/100)
                        ELSE NULL END,
                    %s, 'rule_engine', %s
                FROM effective e
                ON CONFLICT (scenario_id, version_id, period_month, department_uid, product_group_uid)
                DO UPDATE SET
                    department_id              = EXCLUDED.department_id,
                    department_name            = EXCLUDED.department_name,
                    product_group_id           = EXCLUDED.product_group_id,
                    product_group_name         = EXCLUDED.product_group_name,
                    holding_name               = EXCLUDED.holding_name,
                    organization_name          = EXCLUDED.organization_name,
                    region_name                = EXCLUDED.region_name,
                    branch_name                = EXCLUDED.branch_name,
                    parent_department_name     = EXCLUDED.parent_department_name,
                    brand_name                 = EXCLUDED.brand_name,
                    brand_uid                  = EXCLUDED.brand_uid,
                    unmapped_dept              = EXCLUDED.unmapped_dept,
                    unmapped_brand             = EXCLUDED.unmapped_brand,
                    fact_sales_vat             = EXCLUDED.fact_sales_vat,
                    fact_sales_retail          = EXCLUDED.fact_sales_retail,
                    fact_excise                = EXCLUDED.fact_excise,
                    fact_sales_dal             = EXCLUDED.fact_sales_dal,
                    fact_sales_kg              = EXCLUDED.fact_sales_kg,
                    baseline_sales_vat         = EXCLUDED.baseline_sales_vat,
                    baseline_sales_retail      = EXCLUDED.baseline_sales_retail,
                    baseline_kg                = EXCLUDED.baseline_kg,
                    baseline_dal               = EXCLUDED.baseline_dal,
                    applied_revenue_effect_pct = EXCLUDED.applied_revenue_effect_pct,
                    applied_volume_effect_pct  = EXCLUDED.applied_volume_effect_pct,
                    applied_price_effect_pct   = EXCLUDED.applied_price_effect_pct,
                    applied_rule_ids_json      = EXCLUDED.applied_rule_ids_json,
                    fact_price_per_kg          = EXCLUDED.fact_price_per_kg,
                    fact_price_per_dal         = EXCLUDED.fact_price_per_dal,
                    sales_kg_plan              = EXCLUDED.sales_kg_plan,
                    sales_dal_plan             = EXCLUDED.sales_dal_plan,
                    excise_plan                = EXCLUDED.excise_plan,
                    sales_vat_plan             = EXCLUDED.sales_vat_plan,
                    sales_retail_plan          = EXCLUDED.sales_retail_plan,
                    plan_price_per_kg          = EXCLUDED.plan_price_per_kg,
                    plan_price_per_dal         = EXCLUDED.plan_price_per_dal,
                    generation_method          = 'rule_engine',
                    generation_id              = EXCLUDED.generation_id,
                    updated_at                 = NOW()
            """,
            [base_month] + extra
            + [scenario_id, version_id, target_month, target_month]  # active_rules WHERE
            + [global_revenue_pct, global_volume_pct, global_price_pct]  # COALESCE defaults
            + [scenario_id, version_id, target_month]  # INSERT header
            + [base_month.year, generation_id]
            )
            total_generated += cur.rowcount; months_done += 1; conn.commit()
            cur.execute("UPDATE plan_generation_log SET generated_rows=%s,months_processed=%s WHERE generation_id=%s",(total_generated,months_done,generation_id))
            conn.commit()
            log.info("[gen %s] %s→%s: %s rows", generation_id, base_month, target_month, cur.rowcount)

        # ── Coverage stats (per effect_id) ───────────────────────────────────
        rows_without = 0
        per_rule_coverage: list = []
        # Dims populated via dim_department mapping — empty here = missing mapping
        _MAPPING_DEP_DIMS = {
            "region": "region_name", "branch": "branch_name",
            "organization": "organization_name", "holding": "holding_name",
        }
        try:
            cur.execute(
                "SELECT COUNT(*) FROM fact_plan_sales WHERE generation_id=%s AND applied_rule_ids_json='[]'::jsonb",
                (generation_id,)
            )
            rows_without = int(cur.fetchone()[0])

            for rule in rules_snapshot:
                scopes = rule.get("scopes", [])
                non_all_scopes = [s for s in scopes if s.get("dimension_type", "all") != "all"]
                matched_scope_count = len(non_all_scopes)

                for eff in rule.get("effects", []):
                    eid = eff["effect_id"]
                    eff_period_from = eff.get("period_from")
                    eff_period_to   = eff.get("period_to")

                    # Rows where this effect was actually applied
                    cur.execute(
                        "SELECT COUNT(*) FROM fact_plan_sales WHERE generation_id=%s AND applied_rule_ids_json @> %s::jsonb",
                        (generation_id, json.dumps([eid]))
                    )
                    affected_rows = int(cur.fetchone()[0])

                    # Rows in this generation within the effect's period window
                    if eff_period_from or eff_period_to:
                        t_conds: list = ["generation_id=%s"]
                        t_params: list = [generation_id]
                        if eff_period_from:
                            t_conds.append("period_month >= %s"); t_params.append(eff_period_from)
                        if eff_period_to:
                            t_conds.append("period_month <= %s"); t_params.append(eff_period_to)
                        cur.execute(f"SELECT COUNT(*) FROM fact_plan_sales WHERE {' AND '.join(t_conds)}", t_params)
                        target_period_rows = int(cur.fetchone()[0])
                    else:
                        target_period_rows = total_generated

                    # Rows matching scope dimensions (no period filter)
                    # Group by dimension_type: OR within same dim, AND between dims
                    if not non_all_scopes:
                        matched_dimension_rows = total_generated
                    else:
                        from collections import defaultdict as _dd
                        _dim_groups: dict = _dd(list)
                        for sc in non_all_scopes:
                            col = _FPS_DIM_COL.get(sc.get("dimension_type", ""))
                            if col:
                                _dim_groups[col].append(sc.get("dimension_value", ""))
                        d_conds: list = ["generation_id=%s"]
                        d_params: list = [generation_id]
                        for col, vals in _dim_groups.items():
                            placeholders = ",".join(["%s"] * len(vals))
                            d_conds.append(f"COALESCE({col},'') IN ({placeholders})")
                            d_params.extend(vals)
                        cur.execute(f"SELECT COUNT(*) FROM fact_plan_sales WHERE {' AND '.join(d_conds)}", d_params)
                        matched_dimension_rows = int(cur.fetchone()[0])

                    # Derived exclusion counts
                    rows_excluded_by_period = max(0, matched_dimension_rows - affected_rows)
                    rows_excluded_by_scope  = max(0, target_period_rows - affected_rows)

                    # Rows excluded because mapping didn't populate the scope field
                    rows_excluded_by_missing_mapping = 0
                    missing_detail: list = []
                    for sc in non_all_scopes:
                        dt = sc.get("dimension_type", "all")
                        if dt in _MAPPING_DEP_DIMS:
                            col = _MAPPING_DEP_DIMS[dt]
                            cur.execute(
                                f"SELECT COUNT(*) FROM fact_plan_sales "
                                f"WHERE generation_id=%s AND (COALESCE({col},'')='')",
                                (generation_id,)
                            )
                            nc = int(cur.fetchone()[0])
                            if nc > 0:
                                rows_excluded_by_missing_mapping = max(rows_excluded_by_missing_mapping, nc)
                                missing_detail.append(
                                    f"{_DIM_LABELS_UK.get(dt, dt)} пустий у {nc} рядках"
                                )

                    # Build human-readable scope description grouped by dimension (OR within, AND between)
                    def _scope_desc(scopes_list):
                        from collections import defaultdict as _dd2
                        _grp: dict = _dd2(list)
                        for s in scopes_list:
                            label = _DIM_LABELS_UK.get(s['dimension_type'], s['dimension_type'])
                            _grp[label].append(s['dimension_value'])
                        parts = []
                        for label, vals in _grp.items():
                            if len(vals) == 1:
                                parts.append(f"{label}={vals[0]}")
                            else:
                                parts.append(f"{label} IN: {', '.join(vals)}")
                        return ", ".join(parts)

                    # Human-readable reason
                    if affected_rows > 0:
                        unmatched_reason = None
                    elif target_period_rows == 0:
                        unmatched_reason = "Немає рядків у вибраному periodі"
                    elif matched_dimension_rows == 0:
                        unmatched_reason = f"Не знайдено рядків для scope: {_scope_desc(non_all_scopes)}"
                    elif matched_scope_count > 0:
                        unmatched_reason = "Scope знайдений, але після AND-умов рядків немає"
                    else:
                        unmatched_reason = "Правило не покрило жодного рядка"

                    # Detailed explanation
                    if affected_rows > 0:
                        explanation = None
                    elif target_period_rows == 0:
                        explanation = "Немає рядків у цільовому periodі"
                    elif matched_dimension_rows == 0 and missing_detail:
                        explanation = "Фільтр не спрацює через відсутній маппінг: " + "; ".join(missing_detail)
                    elif matched_dimension_rows == 0:
                        explanation = f"Не знайдено жодного рядку для scope: {_scope_desc(non_all_scopes)}"
                    elif matched_dimension_rows > 0 and affected_rows == 0 and (eff_period_from or eff_period_to):
                        explanation = f"Scope знайшов {matched_dimension_rows} рядків, але period видалив їх всі"
                    elif matched_scope_count > 0:
                        explanation = "Scope знайдений, але після AND-умов рядків немає"
                    else:
                        explanation = "Правило не покрило жодного рядка"

                    per_rule_coverage.append({
                        "effect_id":                       eid,
                        "rule_id":                         rule["rule_id"],
                        "rule_name":                       rule["rule_name"],
                        "scopes":                          non_all_scopes,
                        "rule_type":                       eff["rule_type"],
                        "effect_percent":                  eff["effect_percent"],
                        "period_from":                     eff["period_from"],
                        "period_to":                       eff["period_to"],
                        "affected_rows":                   affected_rows,
                        "rows_excluded_by_period":         rows_excluded_by_period,
                        "rows_excluded_by_scope":          rows_excluded_by_scope,
                        "rows_excluded_by_missing_mapping": rows_excluded_by_missing_mapping,
                        "unmatched_reason":                unmatched_reason,
                        "explanation":                     explanation,
                        "matched_scope_count":             matched_scope_count,
                        "target_period_rows":              target_period_rows,
                        "matched_dimension_rows":          matched_dimension_rows,
                    })
        except Exception as cov_exc:
            log.warning("Coverage stats failed: %s", cov_exc)

        cur.execute(
            "UPDATE plan_generation_log SET status='completed',finished_at=NOW(),"
            "generated_rows=%s,deleted_rows=%s,months_processed=%s,rows_without_rules=%s "
            "WHERE generation_id=%s",
            (total_generated, total_deleted, months_done, rows_without, generation_id)
        )
        conn.commit()
        return {
            "generation_id":     generation_id,
            "status":            "completed",
            "generated_rows":    total_generated,
            "deleted_rows":      total_deleted,
            "months_processed":  months_done,
            "rows_without_rules": rows_without,
            "rules_applied":     len(rules_snapshot),
            "rules_coverage":    per_rule_coverage,
        }

    except Exception as exc:
        try:
            cur.execute("UPDATE plan_generation_log SET status='failed',finished_at=NOW(),error_message=%s WHERE generation_id=%s",(str(exc)[:500],generation_id)); conn.commit()
        except Exception: pass
        raise
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# FACT VS PLAN QUERY
# ══════════════════════════════════════════════════════════════════════════════

def get_fact_plan(
    scenario_id=None, version_id=None,
    period_from=None, period_to=None,
    region=None, branch=None, holding=None, organization=None,
    department_uid=None, department_name=None,
    product_group_uid=None, product_group_name=None,
    brand_name=None,
    page=1, page_size=100,
) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds,params=[],[]
        def _add(cond, val): conds.append(cond); params.append(val)
        if scenario_id   is not None: _add("fps.scenario_id=%s",            scenario_id)
        if version_id    is not None: _add("fps.version_id=%s",             version_id)
        if period_from:               _add("fps.period_month>=%s",          period_from)
        if period_to:                 _add("fps.period_month<=%s",          period_to)
        if region:                    _add("fps.region_name ILIKE %s",      f"%{region}%")
        if branch:                    _add("fps.branch_name ILIKE %s",      f"%{branch}%")
        if holding:                   _add("fps.holding_name ILIKE %s",     f"%{holding}%")
        if organization:              _add("fps.organization_name ILIKE %s",f"%{organization}%")
        if department_uid and department_uid.strip():
            _add("fps.department_uid ILIKE %s", f"%{department_uid.strip()}%")
        if department_name and department_name.strip():
            _add("fps.department_name ILIKE %s", f"%{department_name.strip()}%")
        if product_group_uid and product_group_uid.strip():
            _add("fps.product_group_uid ILIKE %s", f"%{product_group_uid.strip()}%")
        if product_group_name and product_group_name.strip():
            _add("fps.product_group_name ILIKE %s", f"%{product_group_name.strip()}%")
        if brand_name and brand_name.strip():
            _add("fps.brand_name ILIKE %s", f"%{brand_name.strip()}%")
        where=("WHERE "+" AND ".join(conds)) if conds else ""

        cur.execute(f"""
            SELECT COUNT(*),
                   COALESCE(SUM(fps.sales_vat_plan),0),    COALESCE(SUM(fps.fact_sales_vat),0),
                   COALESCE(SUM(fps.sales_retail_plan),0), COALESCE(SUM(fps.fact_sales_retail),0),
                   COALESCE(SUM(fps.sales_dal_plan),0),    COALESCE(SUM(fps.fact_sales_dal),0),
                   COALESCE(SUM(fps.sales_kg_plan),0),     COALESCE(SUM(fps.fact_sales_kg),0),
                   MIN(fps.period_month), MAX(fps.period_month)
            FROM fact_plan_sales fps {where}""", params)
        agg=cur.fetchone(); total=int(agg[0])
        eff=min(max(page_size,1),500); offset=(page-1)*eff; total_pages=max(1,(total+eff-1)//eff)

        cur.execute(f"""
            SELECT fps.id, fps.scenario_id, fps.version_id, fps.period_month,
                   fps.department_uid, fps.department_id, fps.department_name,
                   fps.product_group_uid, fps.product_group_id, fps.product_group_name,
                   fps.holding_name, fps.organization_name, fps.region_name, fps.branch_name,
                   fps.parent_department_name, fps.brand_name, fps.brand_uid,
                   fps.unmapped_dept, fps.unmapped_brand,
                   fps.sales_vat_plan,    fps.sales_retail_plan, fps.excise_plan,
                   fps.sales_dal_plan,    fps.sales_kg_plan,
                   fps.fact_sales_vat,    fps.fact_sales_retail, fps.fact_excise,
                   fps.fact_sales_dal,    fps.fact_sales_kg,
                   fps.fact_price_per_kg, fps.plan_price_per_kg,
                   fps.fact_price_per_dal,fps.plan_price_per_dal,
                   fps.applied_revenue_effect_pct, fps.applied_volume_effect_pct,
                   fps.applied_price_effect_pct,   fps.applied_rule_ids_json,
                   fps.generation_id, fps.generation_method
            FROM fact_plan_sales fps {where}
            ORDER BY fps.period_month, fps.region_name, fps.branch_name,
                     fps.department_uid, fps.product_group_uid
            LIMIT %s OFFSET %s""", params+[eff,offset])

        def _f(v): return float(v) if v is not None else None
        def _f0(v): return float(v) if v is not None else 0.0
        def _dp(p,f): return round((p-f)/f*100,2) if f else None

        rows=[]
        for r in cur.fetchall():
            pv=_f0(r[19]); fv=_f0(r[24]); pr=_f0(r[20]); fr=_f0(r[25])
            pk=_f0(r[23]); fk=_f0(r[28]); pd=_f0(r[22]); fd=_f0(r[27])
            rows.append({
                "id":r[0],"scenario_id":r[1],"version_id":r[2],
                "period_month": _s(r[3]),
                "department_uid":r[4],"department_id":r[5],"department_name":r[6],
                "product_group_uid":r[7],"product_group_id":r[8],"product_group_name":r[9],
                "holding_name":r[10],"organization_name":r[11],"region_name":r[12],
                "branch_name":r[13],"parent_department_name":r[14],
                "brand_name":r[15],"brand_uid":r[16],
                "unmapped_dept":r[17],"unmapped_brand":r[18],
                "mapping_status": (
                    "NO_DEPARTMENT_MAPPING" if r[17] and not r[18] else
                    "NO_BRAND_MAPPING"      if r[18] and not r[17] else
                    "PARTIAL_MAPPING"       if r[17] and r[18]     else "OK"
                ),
                "plan_sales_vat":pv,"plan_sales_retail":pr,
                "plan_excise":_f0(r[21]),"plan_sales_dal":pd,"plan_sales_kg":pk,
                "fact_sales_vat":fv,"fact_sales_retail":fr,
                "fact_excise":_f0(r[26]),"fact_sales_dal":fd,"fact_sales_kg":fk,
                "diff_sales_vat":round(pv-fv,4),"diff_sales_vat_pct":_dp(pv,fv),
                "diff_sales_retail":round(pr-fr,4),"diff_sales_retail_pct":_dp(pr,fr),
                "diff_kg":round(pk-fk,4),"diff_kg_pct":_dp(pk,fk),
                "diff_dal":round(pd-fd,4),"diff_dal_pct":_dp(pd,fd),
                "fact_price_per_kg":_f(r[29]),"plan_price_per_kg":_f(r[30]),
                "fact_price_per_dal":_f(r[31]),"plan_price_per_dal":_f(r[32]),
                "applied_revenue_effect_pct":_f0(r[33]),
                "applied_volume_effect_pct":_f0(r[34]),
                "applied_price_effect_pct":_f0(r[35]),
                "applied_rule_ids_json":r[36],
                "generation_id":r[37],"generation_method":r[38],
            })

        pv_t=float(agg[1]); fv_t=float(agg[2])
        pk_t=float(agg[7]); fk_t=float(agg[8])
        pd_t=float(agg[5]); fd_t=float(agg[6])
        return {
            "rows":rows,"total_count":total,"page":page,"page_size":eff,"total_pages":total_pages,
            "total_plan_sales_vat":pv_t,       "total_fact_sales_vat":fv_t,
            "total_plan_sales_retail":float(agg[3]),"total_fact_sales_retail":float(agg[4]),
            "total_plan_sales_dal":pd_t,        "total_fact_sales_dal":fd_t,
            "total_plan_sales_kg":pk_t,         "total_fact_sales_kg":fk_t,
            "total_diff_vat":round(pv_t-fv_t,4), "total_diff_vat_pct":_dp(pv_t,fv_t),
            "total_diff_kg":round(pk_t-fk_t,4),  "total_diff_kg_pct":_dp(pk_t,fk_t),
            "total_diff_dal":round(pd_t-fd_t,4),  "total_diff_dal_pct":_dp(pd_t,fd_t),
            "period_from":_s(agg[9]),"period_to":_s(agg[10]),
        }
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PLAN FILTER OPTION WRAPPERS  (names expected by planning.py router)
# ══════════════════════════════════════════════════════════════════════════════

def get_plan_dept_options(scenario_id: int, version_id: int, search: str = "", limit: int = 50) -> list:
    return get_plan_filter_options("department_name", scenario_id, version_id, search, limit)

def get_plan_pg_options(scenario_id: int, version_id: int, search: str = "", limit: int = 50) -> list:
    return get_plan_filter_options("product_group_name", scenario_id, version_id, search, limit)


# ══════════════════════════════════════════════════════════════════════════════
# TURNOVER DISTINCT OPTIONS  (for scope-builder dropdowns when plan is empty)
# ══════════════════════════════════════════════════════════════════════════════

def get_turnover_dept_options(search: str = "", period_from=None, period_to=None, limit: int = 50) -> list:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = ["ft.department_uid IS NOT NULL", "ft.department_uid != ''"]
        params: list = []
        if period_from: conds.append("ft.period_month >= %s"); params.append(period_from)
        if period_to:   conds.append("ft.period_month <= %s"); params.append(period_to)
        if search and search.strip():
            conds.append("(COALESCE(dd.department_name, ft.department_name, '') ILIKE %s OR ft.department_uid ILIKE %s)")
            params.extend([f"%{search.strip()}%", f"%{search.strip()}%"])
        where = " AND ".join(conds)
        cur.execute(f"""
            SELECT DISTINCT ft.department_uid,
                   COALESCE(dd.department_name, ft.department_name, ft.department_uid) AS dept_name
            FROM fact_turnover ft
            LEFT JOIN department_source_mapping dsm
                   ON dsm.source_id = ft.source_id AND dsm.source_department_id = ft.department_uid
            LEFT JOIN dim_department dd ON dd.department_id = dsm.master_department_id
            WHERE {where}
            ORDER BY dept_name LIMIT %s
        """, params + [min(limit, 200)])
        return [{"department_uid": r[0], "department_name": r[1]} for r in cur.fetchall()]
    finally: cur.close(); conn.close()


def get_turnover_pg_options(search: str = "", period_from=None, period_to=None, limit: int = 50) -> list:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = ["ft.product_group_uid IS NOT NULL", "ft.product_group_uid != ''"]
        params: list = []
        if period_from: conds.append("ft.period_month >= %s"); params.append(period_from)
        if period_to:   conds.append("ft.period_month <= %s"); params.append(period_to)
        if search and search.strip():
            conds.append("(ft.product_group_name ILIKE %s OR ft.product_group_uid ILIKE %s)")
            params.extend([f"%{search.strip()}%", f"%{search.strip()}%"])
        where = " AND ".join(conds)
        cur.execute(f"""
            SELECT DISTINCT ft.product_group_uid, ft.product_group_id, ft.product_group_name
            FROM fact_turnover ft
            WHERE {where}
            ORDER BY ft.product_group_name LIMIT %s
        """, params + [min(limit, 200)])
        return [{"product_group_uid": r[0], "product_group_id": r[1], "product_group_name": r[2]} for r in cur.fetchall()]
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# FACT PLAN AGGREGATED
# ══════════════════════════════════════════════════════════════════════════════

def get_fact_plan_aggregated(
    scenario_id: int, version_id: int,
    group_by: str = "month",
    period_from=None, period_to=None,
) -> dict:
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = ["scenario_id=%s", "version_id=%s"]
        params: list = [scenario_id, version_id]
        if period_from: conds.append("period_month>=%s"); params.append(period_from)
        if period_to:   conds.append("period_month<=%s"); params.append(period_to)
        where = " AND ".join(conds)
        gf = {"month": "period_month::TEXT", "department": "department_name",
              "product_group": "product_group_name"}.get(group_by, "period_month::TEXT")
        cur.execute(f"""
            SELECT {gf} AS grp,
                   COALESCE(SUM(sales_vat_plan),  0), COALESCE(SUM(fact_sales_vat),  0),
                   COALESCE(SUM(sales_kg_plan),   0), COALESCE(SUM(fact_sales_kg),   0),
                   COALESCE(SUM(sales_dal_plan),  0), COALESCE(SUM(fact_sales_dal),  0)
            FROM fact_plan_sales WHERE {where}
            GROUP BY {gf} ORDER BY {gf}
        """, params)
        def _f(v): return float(v) if v is not None else 0.0
        def _dp(p, f): return round((p - f) / f * 100, 2) if f else None
        rows = [{"group": r[0], "plan_vat": _f(r[1]), "fact_vat": _f(r[2]),
                 "plan_kg": _f(r[3]), "fact_kg": _f(r[4]),
                 "plan_dal": _f(r[5]), "fact_dal": _f(r[6])} for r in cur.fetchall()]
        tp_vat = sum(r["plan_vat"] for r in rows)
        tf_vat = sum(r["fact_vat"] for r in rows)
        tp_kg  = sum(r["plan_kg"]  for r in rows)
        tf_kg  = sum(r["fact_kg"]  for r in rows)
        tp_dal = sum(r["plan_dal"] for r in rows)
        tf_dal = sum(r["fact_dal"] for r in rows)
        return {
            "rows": rows,
            "total_plan_vat":         tp_vat, "total_fact_vat":         tf_vat,
            "total_diff_sales_vat":   round(tp_vat - tf_vat, 4),
            "total_diff_sales_vat_pct": _dp(tp_vat, tf_vat),
            "total_plan_kg":          tp_kg,  "total_fact_kg":          tf_kg,
            "total_diff_kg":          round(tp_kg  - tf_kg,  4),
            "total_diff_kg_pct":      _dp(tp_kg, tf_kg),
            "total_plan_dal":         tp_dal, "total_fact_dal":         tf_dal,
            "total_diff_dal":         round(tp_dal - tf_dal, 4),
            "total_diff_dal_pct":     _dp(tp_dal, tf_dal),
        }
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# FACT PLAN GROUPED  (server-side hierarchical aggregation)
# ══════════════════════════════════════════════════════════════════════════════

_GROUPED_DIM_COL = {
    "region":       "region_name",
    "branch":       "branch_name",
    "organization": "organization_name",
    "department":   "department_name",
    "brand":        "brand_name",
}

_GROUPED_DIM_FALLBACK = {
    "region":       "Без регіону",
    "branch":       "Без філії",
    "organization": "Без організації",
    "department":   "Без підрозділу",
    "brand":        "Без бренду/НГ",
}

_grouped_logger = logging.getLogger(__name__ + ".grouped")


def get_fact_plan_grouped(
    scenario_id=None, version_id=None,
    group_by: str = "region",
    period_from=None, period_to=None,
    region=None, branch=None, holding=None, organization=None,
    department_uid=None, department_name=None,
    product_group_uid=None, product_group_name=None,
    brand_name=None,
) -> dict:
    import time as _time
    t0 = _time.time()

    # Preserve user-supplied order; validate and deduplicate
    seen: set = set()
    ordered_dims: list = []
    for g in group_by.split(","):
        g = g.strip()
        if g in _GROUPED_DIM_COL and g not in seen:
            ordered_dims.append(g)
            seen.add(g)

    if not ordered_dims:
        return {"group_by": [], "rows": [], "total": 0, "summary": {}}

    conn = get_connection(); cur = conn.cursor()
    try:
        conds, params = [], []
        def _add(cond, val): conds.append(cond); params.append(val)
        if scenario_id   is not None: _add("fps.scenario_id=%s",            scenario_id)
        if version_id    is not None: _add("fps.version_id=%s",             version_id)
        if period_from:               _add("fps.period_month>=%s",          period_from)
        if period_to:                 _add("fps.period_month<=%s",          period_to)
        if region:                    _add("fps.region_name ILIKE %s",       f"%{region}%")
        if branch:                    _add("fps.branch_name ILIKE %s",       f"%{branch}%")
        if holding:                   _add("fps.holding_name ILIKE %s",      f"%{holding}%")
        if organization:              _add("fps.organization_name ILIKE %s", f"%{organization}%")
        if department_uid and department_uid.strip():
            _add("fps.department_uid ILIKE %s", f"%{department_uid.strip()}%")
        if department_name and department_name.strip():
            _add("fps.department_name ILIKE %s", f"%{department_name.strip()}%")
        if product_group_uid and product_group_uid.strip():
            _add("fps.product_group_uid ILIKE %s", f"%{product_group_uid.strip()}%")
        if product_group_name and product_group_name.strip():
            _add("fps.product_group_name ILIKE %s", f"%{product_group_name.strip()}%")
        if brand_name and brand_name.strip():
            _add("fps.brand_name ILIKE %s", f"%{brand_name.strip()}%")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        def _f(v): return float(v) if v is not None else 0.0
        # Percentages are computed AFTER aggregation, not summed
        def _dp(p, f): return round((p - f) / f * 100, 2) if f else None

        all_rows: list = []

        # One query per level — no N+1
        for level_idx in range(len(ordered_dims)):
            dims_at_level = ordered_dims[:level_idx + 1]
            select_parts, group_parts = [], []
            for dim in dims_at_level:
                col = _GROUPED_DIM_COL[dim]
                fb  = _GROUPED_DIM_FALLBACK[dim]
                expr = f"COALESCE(fps.{col}, '{fb}')"
                select_parts.append(f"{expr} AS grp_{dim}")
                group_parts.append(expr)
            for dim in ordered_dims[level_idx + 1:]:
                select_parts.append(f"NULL AS grp_{dim}")

            select_clause = ", ".join(select_parts)
            group_clause  = ", ".join(group_parts)

            cur.execute(f"""
                SELECT {select_clause},
                       COALESCE(SUM(fps.sales_vat_plan),  0),
                       COALESCE(SUM(fps.fact_sales_vat),  0),
                       COALESCE(SUM(fps.sales_kg_plan),   0),
                       COALESCE(SUM(fps.fact_sales_kg),   0),
                       COALESCE(SUM(fps.sales_dal_plan),  0),
                       COALESCE(SUM(fps.fact_sales_dal),  0),
                       COUNT(*) AS rows_count
                FROM fact_plan_sales fps
                {where}
                GROUP BY {group_clause}
                ORDER BY {group_clause}
            """, params)

            n = len(ordered_dims)
            raw = cur.fetchall()
            for r in raw:
                dim_values = {ordered_dims[i]: r[i] for i in range(n)}
                pv = _f(r[n]);     fv = _f(r[n + 1])
                pk = _f(r[n + 2]); fk = _f(r[n + 3])
                pd_ = _f(r[n + 4]); fd = _f(r[n + 5])
                rows_count = int(r[n + 6])

                # Stable, collision-safe keys: "dim=value|dim2=value2"
                path_parts  = [f"{d}={dim_values[d] or ''}" for d in dims_at_level]
                group_key   = "|".join(path_parts)
                group_label = dim_values[dims_at_level[-1]] or ""
                parent_key  = "|".join(path_parts[:-1]) if level_idx > 0 else None

                all_rows.append({
                    "level":       level_idx,
                    "dim":         dims_at_level[-1],
                    "group_key":   group_key,
                    "group_label": group_label,
                    "parent_key":  parent_key,
                    "rows_count":  rows_count,
                    **{f"grp_{d}": dim_values[d] for d in ordered_dims},
                    "plan_revenue":      pv,   "fact_revenue":      fv,
                    "delta_revenue":     round(pv - fv, 4),
                    "delta_revenue_pct": _dp(pv, fv),
                    "plan_kg":           pk,   "fact_kg":           fk,
                    "delta_kg":          round(pk - fk, 4),
                    "delta_kg_pct":      _dp(pk, fk),
                    "plan_dal":          pd_,  "fact_dal":          fd,
                    "delta_dal":         round(pd_ - fd, 4),
                    "delta_dal_pct":     _dp(pd_, fd),
                })

        # Sort hierarchically: parent followed immediately by its children
        by_parent: dict = {}
        for row in all_rows:
            if row["parent_key"] is not None:
                by_parent.setdefault(row["parent_key"], []).append(row)

        sorted_rows: list = []
        def _append(row):
            sorted_rows.append(row)
            for child in sorted(by_parent.get(row["group_key"], []), key=lambda x: x["group_label"] or ""):
                _append(child)

        for root in sorted(
            (r for r in all_rows if r["level"] == 0),
            key=lambda x: x["group_label"] or ""
        ):
            _append(root)

        # Summary from level-0 aggregates (avoids double-counting)
        level_0 = [r for r in all_rows if r["level"] == 0]
        tp_vat = sum(r["plan_revenue"] for r in level_0)
        tf_vat = sum(r["fact_revenue"] for r in level_0)
        tp_kg  = sum(r["plan_kg"]      for r in level_0)
        tf_kg  = sum(r["fact_kg"]      for r in level_0)
        tp_dal = sum(r["plan_dal"]     for r in level_0)
        tf_dal = sum(r["fact_dal"]     for r in level_0)

        elapsed_ms = round((_time.time() - t0) * 1000)
        _grouped_logger.info(
            "grouped: scenario=%s version=%s group_by=%s levels=%d rows=%d elapsed_ms=%d "
            "filters={region=%s branch=%s org=%s dept=%s brand=%s period=%s/%s}",
            scenario_id, version_id, ordered_dims,
            len(ordered_dims), len(sorted_rows), elapsed_ms,
            region, branch, organization, department_name, brand_name,
            period_from, period_to,
        )

        return {
            "group_by":   ordered_dims,
            "rows":       sorted_rows,
            "total":      len(sorted_rows),
            "elapsed_ms": elapsed_ms,
            "summary": {
                "plan_revenue":      tp_vat, "fact_revenue":      tf_vat,
                "delta_revenue":     round(tp_vat - tf_vat, 4),
                "delta_revenue_pct": _dp(tp_vat, tf_vat),
                "plan_kg":           tp_kg,  "fact_kg":           tf_kg,
                "delta_kg":          round(tp_kg - tf_kg, 4),
                "delta_kg_pct":      _dp(tp_kg, tf_kg),
                "plan_dal":          tp_dal, "fact_dal":          tf_dal,
                "delta_dal":         round(tp_dal - tf_dal, 4),
                "delta_dal_pct":     _dp(tp_dal, tf_dal),
            },
        }
    finally: cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# DEPARTMENT MAPPING COVERAGE  (fact_turnover perspective)
# ══════════════════════════════════════════════════════════════════════════════

def get_dept_mapping_coverage(
    period_from=None, period_to=None, source_id: Optional[int] = None,
) -> dict:
    """
    Coverage of department_source_mapping vs fact_turnover.
    Returns aggregate KPIs (with planning-focused field names),
    impact-ranked list of unmapped departments, and an explanation string.
    """
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = []
        params: list = []
        if period_from: conds.append("ft.period_month >= %s"); params.append(period_from)
        if period_to:   conds.append("ft.period_month <= %s"); params.append(period_to)
        if source_id:   conds.append("ft.source_id = %s");     params.append(source_id)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        _IS_MAPPED = "m.master_department_id IS NOT NULL AND m.mapping_status IN ('mapped','auto')"
        _IS_UNMAPPED = ("(m.master_department_id IS NULL"
                        " OR m.mapping_status NOT IN ('mapped','auto')"
                        " OR m.mapping_status IS NULL)")

        cur.execute(f"""
            SELECT
              COUNT(*)                                                              AS total_fact_rows,
              COUNT(*) FILTER (WHERE {_IS_MAPPED})                                 AS mapped_rows,
              COUNT(*) FILTER (WHERE {_IS_UNMAPPED})                               AS unmapped_rows,
              COALESCE(SUM(ft.sales_vat), 0)                                       AS total_sales_vat,
              COALESCE(SUM(ft.sales_vat) FILTER (WHERE {_IS_MAPPED}), 0)           AS mapped_sales_vat,
              COALESCE(SUM(ft.sales_vat) FILTER (WHERE {_IS_UNMAPPED}), 0)         AS unmapped_sales_vat,
              COUNT(DISTINCT ft.department_uid)                                     AS unique_fact_departments,
              COUNT(DISTINCT ft.department_uid) FILTER (WHERE {_IS_MAPPED})        AS mapped_departments,
              COUNT(DISTINCT ft.department_uid) FILTER (WHERE {_IS_UNMAPPED})      AS unmapped_departments
            FROM fact_turnover ft
            LEFT JOIN department_source_mapping m
                   ON m.source_department_id = ft.department_uid
                  AND m.source_id            = ft.source_id
            {where}
        """, params)
        agg = cur.fetchone()

        total_fact_rows        = int(agg[0])
        mapped_rows            = int(agg[1])
        unmapped_rows          = int(agg[2])
        unique_fact_departments = int(agg[6])
        mapped_departments     = int(agg[7])
        unmapped_departments   = int(agg[8])

        coverage_pct = round(mapped_departments / unique_fact_departments * 100, 1) if unique_fact_departments > 0 else 0.0

        if unique_fact_departments == 0:
            explanation = "fact_turnover не містить підрозділів. Перевірте наявність даних у fact_turnover."
        elif coverage_pct == 100.0:
            explanation = "Усі підрозділи, які використовуються у продажах, вже замаплені. Planning rules можуть використовувати region/branch/org фільтри."
        elif coverage_pct >= 80:
            explanation = (f"Більшість підрозділів замаплено ({coverage_pct}%). "
                           f"{unmapped_departments} dept без маппінгу обмежать coverage правил Planning.")
        elif coverage_pct >= 50:
            explanation = (f"Половина підрозділів без маппінгу. Planning rules з region/branch/org "
                           f"фільтрами охоплять менше даних ({coverage_pct}% coverage).")
        else:
            explanation = (f"Критично мало маппінгу ({coverage_pct}%). "
                           "Більшість правил Planning не зможуть використовувати dimension-фільтри.")

        # Unmapped dept details — with planning_impact and impact_level
        VAT_HIGH   = 1_000_000
        VAT_MEDIUM = 100_000
        unmapped_conds = list(conds) + [
            f"({_IS_UNMAPPED})"
        ]
        unmapped_where = "WHERE " + " AND ".join(unmapped_conds)
        cur.execute(f"""
            SELECT
              ft.department_uid,
              ft.source_id,
              MAX(ft.department_name)         AS department_name,
              COUNT(*)                        AS rows_count,
              COALESCE(SUM(ft.sales_vat), 0)  AS sales_vat_sum,
              EXISTS(
                SELECT 1 FROM fact_plan_sales fps
                WHERE fps.department_uid = ft.department_uid
              )                               AS used_in_planning
            FROM fact_turnover ft
            LEFT JOIN department_source_mapping m
                   ON m.source_department_id = ft.department_uid
                  AND m.source_id            = ft.source_id
            {unmapped_where}
            GROUP BY ft.department_uid, ft.source_id
            ORDER BY SUM(ft.sales_vat) DESC NULLS LAST
            LIMIT 200
        """, params)

        unmapped_depts = []
        for r in cur.fetchall():
            vat = float(r[4]) if r[4] is not None else 0.0
            unmapped_depts.append({
                "department_uid":          r[0],
                "source_id":               r[1],
                "department_name_from_fact": r[2],
                "rows_count":              int(r[3]),
                "sales_vat_sum":           vat,
                "planning_impact":         "Used in Planning" if bool(r[5]) else "Not used",
                "impact_level":            "HIGH" if vat > VAT_HIGH else "MEDIUM" if vat > VAT_MEDIUM else "LOW",
            })

        def _f(v): return float(v) if v is not None else 0.0
        return {
            # New spec fields
            "total_fact_rows":          total_fact_rows,
            "unique_fact_departments":  unique_fact_departments,
            "mapped_departments":       mapped_departments,
            "unmapped_departments":     unmapped_departments,
            "mapped_sales_vat":         _f(agg[4]),
            "unmapped_sales_vat":       _f(agg[5]),
            "coverage_pct":             coverage_pct,
            "explanation":              explanation,
            # Backward-compat aliases
            "total_rows":               total_fact_rows,
            "mapped_rows":              mapped_rows,
            "unmapped_rows":            unmapped_rows,
            "total_sales_vat":          _f(agg[3]),
            "total_unique_depts":       unique_fact_departments,
            "mapped_unique_depts":      mapped_departments,
            "unmapped_unique_depts":    unmapped_departments,
            "mapped_pct":               coverage_pct,
            "unmapped_depts":           unmapped_depts,
        }
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PLANNING READINESS
# ══════════════════════════════════════════════════════════════════════════════

def get_planning_readiness(period_from=None, period_to=None, source_id: Optional[int] = None) -> dict:
    """
    Single-pass aggregate over fact_turnover to assess what % of fact data
    is ready for planning (has department mapping + dimension coverage).
    """
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = []
        params: list = []
        if period_from: conds.append("ft.period_month >= %s"); params.append(period_from)
        if period_to:   conds.append("ft.period_month <= %s"); params.append(period_to)
        if source_id:   conds.append("ft.source_id = %s");     params.append(source_id)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        cur.execute(f"""
            SELECT
              COUNT(*)                                                                         AS fact_rows,
              COUNT(*) FILTER (WHERE m.master_department_id IS NOT NULL
                               AND m.mapping_status IN ('mapped','auto'))                       AS dept_mapped_rows,
              COUNT(DISTINCT ft.department_uid)                                                 AS total_dept_uids,
              COUNT(DISTINCT ft.department_uid) FILTER (
                  WHERE m.master_department_id IS NOT NULL
                    AND m.mapping_status IN ('mapped','auto'))                                   AS mapped_dept_uids,
              COUNT(*) FILTER (WHERE bm.master_brand_id IS NOT NULL)                           AS brand_mapped_rows,
              COUNT(DISTINCT ft.product_group_uid)                                              AS total_pg_uids,
              COUNT(DISTINCT ft.product_group_uid) FILTER (
                  WHERE bm.master_brand_id IS NOT NULL)                                         AS mapped_pg_uids,
              COUNT(*) FILTER (WHERE dd.region_name IS NOT NULL AND dd.region_name != '')       AS rows_with_region,
              COUNT(*) FILTER (WHERE dd.branch_name IS NOT NULL AND dd.branch_name != '')       AS rows_with_branch,
              COUNT(*) FILTER (WHERE dd.organization_name IS NOT NULL AND dd.organization_name != '') AS rows_with_org,
              COUNT(*) FILTER (WHERE dd.holding_name IS NOT NULL AND dd.holding_name != '')     AS rows_with_holding
            FROM fact_turnover ft
            LEFT JOIN department_source_mapping m
                   ON m.source_department_id = ft.department_uid
                  AND m.source_id            = ft.source_id
            LEFT JOIN dim_department dd ON dd.department_id = m.master_department_id
            LEFT JOIN brand_source_mapping bm
                   ON bm.source_brand_id = ft.product_group_uid
                  AND bm.source_id       = ft.source_id
            {where}
        """, params)
        r = cur.fetchone()

        fact_rows        = int(r[0])
        dept_mapped_rows = int(r[1])
        total_dept_uids  = int(r[2])
        mapped_dept_uids = int(r[3])
        brand_mapped_rows = int(r[4])
        total_pg_uids    = int(r[5])
        mapped_pg_uids   = int(r[6])
        rows_with_region = int(r[7])
        rows_with_branch = int(r[8])
        rows_with_org    = int(r[9])
        rows_with_holding = int(r[10])

        def pct(n, d): return round(n / d * 100, 1) if d > 0 else 0.0

        mapped_dept_pct  = pct(dept_mapped_rows, fact_rows)
        mapped_brand_pct = pct(brand_mapped_rows, fact_rows)
        region_pct       = pct(rows_with_region, fact_rows)
        branch_pct       = pct(rows_with_branch, fact_rows)
        org_pct          = pct(rows_with_org, fact_rows)
        holding_pct      = pct(rows_with_holding, fact_rows)

        warnings: list = []
        if fact_rows > 0:
            unmapped_dept_uids = total_dept_uids - mapped_dept_uids
            unmapped_pg_uids   = total_pg_uids   - mapped_pg_uids
            if unmapped_dept_uids > 0:
                warnings.append(f"{round(100 - mapped_dept_pct, 1)}% рядків не мають department mapping ({unmapped_dept_uids} UID)")
            if unmapped_pg_uids > 0:
                warnings.append(f"{round(100 - mapped_brand_pct, 1)}% рядків не мають brand mapping ({unmapped_pg_uids} product group)")
            if region_pct < 90:
                warnings.append(f"{round(100 - region_pct, 1)}% рядків не мають region mapping")
            if branch_pct < 90:
                warnings.append(f"{round(100 - branch_pct, 1)}% рядків не мають branch mapping")
            if org_pct < 90:
                warnings.append(f"{round(100 - org_pct, 1)}% рядків не мають organization mapping")

        _dims = [mapped_dept_pct, mapped_brand_pct, region_pct, branch_pct, org_pct, holding_pct]
        _dim_labels = ["Dept mapping", "Brand mapping", "Регіон", "Філія", "Організація", "Холдинг"]
        planning_ready_pct = round(sum(_dims) / len(_dims), 1)
        ready_count   = sum(1 for d in _dims if d >= 100)
        blockers      = [{"label": lbl, "pct": pct} for lbl, pct in zip(_dim_labels, _dims) if pct < 100]

        return {
            "fact_rows":                      fact_rows,
            "mapped_department_pct":          mapped_dept_pct,
            "mapped_brand_pct":               mapped_brand_pct,
            "rows_with_region":               rows_with_region,
            "rows_with_branch":               rows_with_branch,
            "rows_with_organization":         rows_with_org,
            "rows_with_holding":              rows_with_holding,
            "rows_without_department_mapping": fact_rows - dept_mapped_rows,
            "rows_without_brand_mapping":      fact_rows - brand_mapped_rows,
            "planning_ready_pct":             planning_ready_pct,
            "ready_count":                    ready_count,
            "total_count":                    len(_dims),
            "blockers":                       blockers,
            "region_pct":                     region_pct,
            "branch_pct":                     branch_pct,
            "org_pct":                        org_pct,
            "holding_pct":                    holding_pct,
            "unmapped_dept_uids":             total_dept_uids - mapped_dept_uids,
            "unmapped_pg_uids":               total_pg_uids   - mapped_pg_uids,
            "warnings":                       warnings,
        }
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# READINESS PROBLEMS  (Part 3)
# ══════════════════════════════════════════════════════════════════════════════

def get_readiness_problems(
    problem_type: str,
    period_from=None, period_to=None, source_id: Optional[int] = None,
    page: int = 1, page_size: int = 50, search: Optional[str] = None,
) -> dict:
    """
    Paginated list of fact_turnover rows grouped by source item that have a
    specific readiness problem.

    problem_type:
      dept_mapping       - no mapping or mapping_status not in (mapped, auto)
      brand_mapping      - no brand mapping
      missing_region     - mapped dept has empty region_name
      missing_branch     - mapped dept has empty branch_name
      missing_organization - mapped dept has empty organization_name
      missing_holding    - mapped dept has empty holding_name
    """
    conn = get_connection(); cur = conn.cursor()
    try:
        base_conds: list = []
        base_params: list = []
        if period_from: base_conds.append("ft.period_month >= %s"); base_params.append(period_from)
        if period_to:   base_conds.append("ft.period_month <= %s"); base_params.append(period_to)
        if source_id:   base_conds.append("ft.source_id = %s");    base_params.append(source_id)

        offset = (max(page, 1) - 1) * page_size

        if problem_type == "dept_mapping":
            unmapped = ("(m.master_department_id IS NULL"
                        " OR m.mapping_status NOT IN ('mapped','auto')"
                        " OR m.mapping_status IS NULL)")
            all_conds = base_conds + [unmapped]
            where = ("WHERE " + " AND ".join(all_conds)) if all_conds else ""
            inner = f"""
                SELECT
                    ft.department_uid                  AS source_item_id,
                    MAX(ft.department_name)            AS source_item_name,
                    NULL::text                         AS master_id,
                    NULL::text                         AS master_name,
                    NULL::text                         AS holding_name,
                    NULL::text                         AS organization_name,
                    NULL::text                         AS branch_name,
                    NULL::text                         AS region_name,
                    COUNT(*)                           AS fact_rows,
                    COALESCE(SUM(ft.sales_vat), 0)    AS sales_amount,
                    ft.source_id                       AS source_id
                FROM fact_turnover ft
                LEFT JOIN department_source_mapping m
                       ON m.source_department_id = ft.department_uid
                      AND m.source_id            = ft.source_id
                {where}
                GROUP BY ft.department_uid, ft.source_id"""

        elif problem_type == "brand_mapping":
            all_conds = base_conds + ["bm.master_brand_id IS NULL"]
            where = ("WHERE " + " AND ".join(all_conds)) if all_conds else ""
            inner = f"""
                SELECT
                    ft.product_group_uid               AS source_item_id,
                    MAX(ft.product_group_name)         AS source_item_name,
                    NULL::text                         AS master_id,
                    NULL::text                         AS master_name,
                    NULL::text                         AS holding_name,
                    NULL::text                         AS organization_name,
                    NULL::text                         AS branch_name,
                    NULL::text                         AS region_name,
                    COUNT(*)                           AS fact_rows,
                    COALESCE(SUM(ft.sales_vat), 0)    AS sales_amount,
                    ft.source_id                       AS source_id
                FROM fact_turnover ft
                LEFT JOIN brand_source_mapping bm
                       ON bm.source_brand_id = ft.product_group_uid
                      AND bm.source_id       = ft.source_id
                {where}
                GROUP BY ft.product_group_uid, ft.source_id"""

        else:
            attr_cond = {
                "missing_region":       "COALESCE(dd.region_name, '') = ''",
                "missing_branch":       "COALESCE(dd.branch_name, '') = ''",
                "missing_organization": "COALESCE(dd.organization_name, '') = ''",
                "missing_holding":      "COALESCE(dd.holding_name, '') = ''",
            }.get(problem_type)
            if not attr_cond:
                raise ValueError(f"Unknown problem_type: {problem_type!r}")
            mapped = "m.master_department_id IS NOT NULL AND m.mapping_status IN ('mapped','auto')"
            all_conds = base_conds + [mapped, attr_cond]
            where = "WHERE " + " AND ".join(all_conds)
            inner = f"""
                SELECT
                    ft.department_uid                      AS source_item_id,
                    MAX(ft.department_name)                AS source_item_name,
                    m.master_department_id                 AS master_id,
                    MAX(dd.department_name)                AS master_name,
                    MAX(dd.holding_name)                   AS holding_name,
                    MAX(dd.organization_name)              AS organization_name,
                    MAX(dd.branch_name)                    AS branch_name,
                    MAX(dd.region_name)                    AS region_name,
                    COUNT(*)                               AS fact_rows,
                    COALESCE(SUM(ft.sales_vat), 0)        AS sales_amount,
                    ft.source_id                           AS source_id,
                    MAX(dd.parent_department_id)           AS parent_department_id,
                    MAX(dd.parent_department_name)         AS parent_department_name,
                    BOOL_AND(COALESCE(dd.is_active, true)) AS is_active
                FROM fact_turnover ft
                JOIN department_source_mapping m
                    ON m.source_department_id = ft.department_uid
                   AND m.source_id            = ft.source_id
                JOIN dim_department dd ON dd.department_id = m.master_department_id
                {where}
                GROUP BY ft.department_uid, m.master_department_id, ft.source_id"""

        # Optional search on the aggregated result
        search_sql, search_params = "", []
        if search:
            p = f"%{search}%"
            search_sql = ("WHERE COALESCE(source_item_id,'') ILIKE %s"
                          "   OR COALESCE(source_item_name,'') ILIKE %s"
                          "   OR COALESCE(master_name,'') ILIKE %s")
            search_params = [p, p, p]

        all_p = base_params + search_params

        # Total count
        cur.execute(f"SELECT COUNT(*) FROM ({inner}) sub {search_sql}", all_p)
        total = int(cur.fetchone()[0])

        # KPI over all matching rows
        cur.execute(
            f"SELECT SUM(fact_rows), SUM(sales_amount), COUNT(*)"
            f" FROM ({inner}) sub {search_sql}",
            all_p,
        )
        krow = cur.fetchone()
        kpi = {
            "affected_rows":  int(krow[0] or 0),
            "affected_sales": float(krow[1] or 0),
            "unique_items":   int(krow[2] or 0),
        }

        # Page of results
        cur.execute(
            f"SELECT * FROM ({inner}) sub {search_sql}"
            f" ORDER BY sales_amount DESC NULLS LAST LIMIT %s OFFSET %s",
            all_p + [page_size, offset],
        )
        rows = [
            {
                "source_item_id":       r[0],
                "source_item_name":     r[1],
                "master_id":            r[2],
                "master_name":          r[3],
                "holding_name":         r[4],
                "organization_name":    r[5],
                "branch_name":          r[6],
                "region_name":          r[7],
                "fact_rows":            int(r[8]),
                "sales_amount":         float(r[9] or 0),
                "source_id":            r[10],
                "parent_department_id":   r[11] if len(r) > 11 else None,
                "parent_department_name": r[12] if len(r) > 12 else None,
                "is_active":              bool(r[13]) if len(r) > 13 and r[13] is not None else True,
            }
            for r in cur.fetchall()
        ]

        return {"rows": rows, "total": total, "page": page, "page_size": page_size, "kpi": kpi}
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# BRAND MAPPING COVERAGE  (Part 4)
# ══════════════════════════════════════════════════════════════════════════════

def get_brand_mapping_coverage(
    period_from=None, period_to=None, source_id: Optional[int] = None,
) -> dict:
    """Coverage of brand_source_mapping vs fact_turnover (product_group_uid)."""
    import traceback as _tb
    conn = get_connection(); cur = conn.cursor()
    try:
        conds: list = []
        params: list = []
        if period_from: conds.append("ft.period_month >= %s"); params.append(period_from)
        if period_to:   conds.append("ft.period_month <= %s"); params.append(period_to)
        if source_id:   conds.append("ft.source_id = %s");    params.append(source_id)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        _MAPPED   = "bm.master_brand_id IS NOT NULL"
        _UNMAPPED = "bm.master_brand_id IS NULL"

        cur.execute(f"""
            SELECT
              COUNT(*)                                                        AS total_fact_rows,
              COUNT(*) FILTER (WHERE {_MAPPED})                              AS mapped_rows,
              COUNT(*) FILTER (WHERE {_UNMAPPED})                            AS unmapped_rows,
              COALESCE(SUM(ft.sales_vat), 0)                                 AS total_sales_vat,
              COALESCE(SUM(ft.sales_vat) FILTER (WHERE {_MAPPED}),   0)     AS mapped_sales_vat,
              COALESCE(SUM(ft.sales_vat) FILTER (WHERE {_UNMAPPED}), 0)     AS unmapped_sales_vat,
              COUNT(DISTINCT ft.product_group_uid)                           AS unique_fact_brands,
              COUNT(DISTINCT ft.product_group_uid) FILTER (WHERE {_MAPPED}) AS mapped_brands,
              COUNT(DISTINCT ft.product_group_uid) FILTER (WHERE {_UNMAPPED}) AS unmapped_brands
            FROM fact_turnover ft
            LEFT JOIN brand_source_mapping bm
                   ON bm.source_brand_id = ft.product_group_uid
                  AND bm.source_id       = ft.source_id
            {where}
        """, params)
        agg = cur.fetchone()
        total_fact_rows   = int(agg[0])
        unique_fact_brands = int(agg[6])
        mapped_brands     = int(agg[7])
        unmapped_brands   = int(agg[8])
        coverage_pct = round(mapped_brands / unique_fact_brands * 100, 1) if unique_fact_brands > 0 else 0.0

        if coverage_pct == 100:
            explanation = "Усі товарні групи/бренди мають mapping. Planning rules з brand фільтрами охоплюють усі рядки."
        elif coverage_pct >= 80:
            explanation = (f"Більшість брендів замаплено ({coverage_pct}%). "
                           f"{unmapped_brands} без mapping — рядки fact не отримають brand-специфічних правил.")
        else:
            explanation = (f"Частина товарних груп/брендів не має mapping ({coverage_pct}%). "
                           "Planning rules з brand/product group фільтрами не зможуть охопити ці рядки.")

        # Unmapped brand details
        unmapped_conds = conds + [_UNMAPPED]
        unmapped_where = "WHERE " + " AND ".join(unmapped_conds) if unmapped_conds else ""
        cur.execute(f"""
            SELECT
              ft.product_group_uid,
              ft.source_id,
              MAX(ft.product_group_name)     AS brand_name,
              COUNT(*)                       AS rows_count,
              COALESCE(SUM(ft.sales_vat), 0) AS sales_vat_sum
            FROM fact_turnover ft
            LEFT JOIN brand_source_mapping bm
                   ON bm.source_brand_id = ft.product_group_uid
                  AND bm.source_id       = ft.source_id
            {unmapped_where}
            GROUP BY ft.product_group_uid, ft.source_id
            ORDER BY SUM(ft.sales_vat) DESC NULLS LAST
            LIMIT 200
        """, params)

        VAT_HIGH, VAT_MEDIUM = 1_000_000, 100_000
        unmapped_list = []
        for r in cur.fetchall():
            vat = float(r[4] or 0)
            raw_uid = r[0] or ""
            normalized_uid = re.sub(r"[^a-zA-Z0-9]", "", raw_uid).lower()
            unmapped_list.append({
                "brand_uid":      raw_uid,
                "source_id":      r[1],
                "brand_name":     r[2],
                "rows_count":     int(r[3]),
                "sales_vat_sum":  vat,
                "impact_level":   "HIGH" if vat > VAT_HIGH else "MEDIUM" if vat > VAT_MEDIUM else "LOW",
                "normalized_uid": normalized_uid,
            })

        def _f(v): return float(v) if v is not None else 0.0
        return {
            "total_fact_rows":    total_fact_rows,
            "unique_fact_brands": unique_fact_brands,
            "mapped_brands":      mapped_brands,
            "unmapped_brands":    unmapped_brands,
            "mapped_sales_vat":   _f(agg[4]),
            "unmapped_sales_vat": _f(agg[5]),
            "coverage_pct":       coverage_pct,
            "explanation":        explanation,
            "unmapped_brands_list": unmapped_list,
        }
    except Exception as _exc:
        log.error("get_brand_mapping_coverage FAILED: %s\n%s", _exc, _tb.format_exc())
        raise
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PLANS OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

def get_plans_overview() -> list:
    """
    One-shot query: all scenario+version combos with their plan row counts,
    total VAT, and latest generation metadata.
    """
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
              s.scenario_id, s.scenario_name, s.scenario_code, s.scenario_type,
              v.version_id, v.version_name, v.version_number, v.is_locked,
              v.created_at                                    AS version_created_at,
              COALESCE(fp.plan_rows_count, 0)                AS plan_rows_count,
              COALESCE(fp.total_plan_sales_vat, 0)           AS total_plan_sales_vat,
              g.generation_id                                 AS last_generation_id,
              g.finished_at                                   AS last_generated_at,
              COALESCE(g.generated_rows, 0)                  AS generated_rows,
              g.target_period_from,
              g.target_period_to,
              g.status                                        AS generation_status
            FROM dim_scenario s
            JOIN scenario_version v ON v.scenario_id = s.scenario_id
            LEFT JOIN LATERAL (
              SELECT generation_id, finished_at, generated_rows,
                     target_period_from, target_period_to, status
              FROM plan_generation_log
              WHERE scenario_id = s.scenario_id AND version_id = v.version_id
              ORDER BY generation_id DESC
              LIMIT 1
            ) g ON TRUE
            LEFT JOIN LATERAL (
              SELECT COUNT(*)                  AS plan_rows_count,
                     SUM(sales_vat_plan)       AS total_plan_sales_vat
              FROM fact_plan_sales
              WHERE scenario_id = s.scenario_id AND version_id = v.version_id
            ) fp ON TRUE
            WHERE s.is_active = TRUE
            ORDER BY v.version_id DESC
        """)
        def _f(v): return float(v) if v is not None else 0.0
        def _s(v): return str(v) if v else None
        return [
            {
                "scenario_id":          r[0],
                "scenario_name":        r[1],
                "scenario_code":        r[2],
                "scenario_type":        r[3],
                "version_id":           r[4],
                "version_name":         r[5],
                "version_number":       r[6],
                "is_locked":            bool(r[7]),
                "version_created_at":   _s(r[8]),
                "plan_rows_count":      int(r[9]),
                "total_plan_sales_vat": _f(r[10]),
                "last_generation_id":   r[11],
                "last_generated_at":    _s(r[12]),
                "generated_rows":       int(r[13]),
                "target_period_from":   _s(r[14]),
                "target_period_to":     _s(r[15]),
                "generation_status":    r[16],
            }
            for r in cur.fetchall()
        ]
    finally:
        cur.close(); conn.close()


def delete_version(version_id: int) -> bool:
    """Hard-delete a plan version and all its data (rows, generation log, rules)."""
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT scenario_id FROM scenario_version WHERE version_id=%s", (version_id,))
        row = cur.fetchone()
        if not row: return False
        scenario_id = row[0]
        cur.execute("DELETE FROM fact_plan_sales WHERE scenario_id=%s AND version_id=%s", (scenario_id, version_id))
        cur.execute("DELETE FROM plan_generation_log WHERE scenario_id=%s AND version_id=%s", (scenario_id, version_id))
        cur.execute("DELETE FROM plan_rule WHERE scenario_id=%s AND version_id=%s", (scenario_id, version_id))
        cur.execute("DELETE FROM scenario_version WHERE version_id=%s", (version_id,))
        # Deactivate scenario if no versions remain
        cur.execute("SELECT COUNT(*) FROM scenario_version WHERE scenario_id=%s", (scenario_id,))
        if int(cur.fetchone()[0]) == 0:
            cur.execute("UPDATE dim_scenario SET is_active=FALSE WHERE scenario_id=%s", (scenario_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()
