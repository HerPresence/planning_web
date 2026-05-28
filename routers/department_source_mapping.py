"""
Department source mapping — HTTP endpoints.
Manages dim_department_source ↔ department_source_mapping ↔ dim_department correspondence.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from db import get_connection

router = APIRouter(prefix="/api/department-source-mapping")


# ── Request models ────────────────────────────────────────────────────────────

class BindRequest(BaseModel):
    source_id:            int
    source_department_id: str
    master_department_id: str  # dim_department.department_id (TEXT)


class RejectRequest(BaseModel):
    source_id:            int
    source_department_id: str


class ResetRequest(BaseModel):
    source_id:            int
    source_department_id: str


class AutoBindRequest(BaseModel):
    source_id: Optional[int] = None


class BulkFillFilters(BaseModel):
    source_id:              Optional[int] = None
    organization_name:      Optional[str] = None
    branch_name:            Optional[str] = None
    region_name:            Optional[str] = None
    master_department_id:   Optional[str] = None
    mapping_status:         Optional[str] = None
    search:                 Optional[str] = None
    has_parent:             Optional[str] = None
    parent_status:          Optional[str] = None
    parent_department_id:   Optional[str] = None
    parent_department_name: Optional[str] = None
    source_level:           Optional[int] = None
    source_node_type:       Optional[str] = None


class BulkFillRequest(BaseModel):
    filters:  BulkFillFilters
    field:    str
    value:    str
    value_id: Optional[str] = None   # dict entry ID or dept ID (as string)
    confirm:  bool = False


class BulkCreateRequest(BaseModel):
    filters: BulkFillFilters
    confirm: bool = False


class CreateStandaloneRequest(BaseModel):
    department_id:          str
    department_name:        str
    organization_name:      str
    parent_department_id:   Optional[str] = None
    parent_department_name: Optional[str] = None
    branch_name:            Optional[str] = None
    region_name:            Optional[str] = None
    holding_name:           Optional[str] = None
    holding_id:             Optional[int] = None
    organization_id:        Optional[int] = None
    region_id:              Optional[int] = None
    branch_id:              Optional[int] = None
    # If set, auto-bind the parent's source row after creation
    auto_bind_source_id:            Optional[int] = None
    auto_bind_source_department_id: Optional[str] = None


class CreateMasterRequest(BaseModel):
    source_id:              int
    source_department_id:   str
    department_id:          str
    department_name:        str
    organization_name:      str
    parent_department_id:   Optional[str] = None
    parent_department_name: Optional[str] = None
    branch_name:            Optional[str] = None
    region_name:            Optional[str] = None
    holding_name:           Optional[str] = None
    # Resolved master-dict IDs (from /resolve-context)
    holding_id:             Optional[int] = None
    organization_id:        Optional[int] = None
    region_id:              Optional[int] = None
    branch_id:              Optional[int] = None


class ResolveContextRequest(BaseModel):
    holding_name:      Optional[str] = None
    organization_name: Optional[str] = None
    region_name:       Optional[str] = None
    branch_name:       Optional[str] = None


class SuggestMatchRequest(BaseModel):
    source_id:            int
    source_department_id: str


class CreateDictEntryRequest(BaseModel):
    entry_type: str   # "holding" | "organization" | "region" | "branch"
    name:       str


# ── Bulk-fill field config ────────────────────────────────────────────────────

# Simple name-only fields: field_key → staging default column
_ALLOWED_FIELDS = {
    "department_name":    "default_department_name",
    "organization_name":  "default_organization_name",
    "branch_name":        "default_branch_name",
    "region_name":        "default_region_name",
    "holding_name":       "default_holding_name",
    # parent_department is handled separately (dual-column update)
}

# field_key → dim_department column name
_DIM_FIELDS = {
    "department_name":    "department_name",
    "organization_name":  "organization_name",
    "branch_name":        "branch_name",
    "region_name":        "region_name",
    "holding_name":       "holding_name",
}

_FIELD_LABELS = {
    "department_name":    "Назва підрозділу",
    "organization_name":  "Організація",
    "branch_name":        "Філія",
    "region_name":        "Регіон",
    "holding_name":       "Холдинг",
    "parent_department":  "Parent підрозділ",
}

# Dict-lookup fields that also carry an integer ID column
_PAIRED_ID_COLS = {
    # field_key: (staging_id_col, dim_id_col)
    "organization_name": ("default_organization_id", "organization_id"),
    "holding_name":      ("default_holding_id",      "holding_id"),
    "region_name":       ("default_region_id",       "region_id"),
    "branch_name":       ("default_branch_id",       "branch_id"),
}

_ALL_FILL_FIELDS = set(_ALLOWED_FIELDS.keys()) | {"parent_department"}

_IS_MAPPED  = "dsm.master_department_id IS NOT NULL AND dsm.mapping_status IN ('mapped', 'auto')"
_IS_PENDING = "(dsm.mapping_status = 'pending' OR dsm.mapping_status IS NULL)"

_BULK_JOIN = """
    FROM dim_department_source dds
    LEFT JOIN department_source_mapping dsm
           ON dsm.source_id = dds.source_id
          AND dsm.source_department_id = dds.source_department_id
    LEFT JOIN dim_department d ON d.department_id = dsm.master_department_id
"""


# ── Column migration ──────────────────────────────────────────────────────────

_columns_initialized = False


def _ensure_dept_source_columns():
    global _columns_initialized
    if _columns_initialized:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        for col, typ in [
            ("default_department_name",        "TEXT"),
            ("default_parent_department_id",   "TEXT"),
            ("default_parent_department_name", "TEXT"),
            ("default_organization_name",      "TEXT"),
            ("default_organization_id",        "INTEGER"),
            ("default_branch_name",            "TEXT"),
            ("default_branch_id",              "INTEGER"),
            ("default_region_name",            "TEXT"),
            ("default_region_id",              "INTEGER"),
            ("default_holding_name",           "TEXT"),
            ("default_holding_id",             "INTEGER"),
        ]:
            cur.execute(f"ALTER TABLE dim_department_source ADD COLUMN IF NOT EXISTS {col} {typ}")

        for col, typ in [
            ("parent_department_id",   "TEXT"),
            ("parent_department_name", "TEXT"),
            ("is_deleted",             "BOOLEAN DEFAULT FALSE"),
        ]:
            cur.execute(f"ALTER TABLE dim_department ADD COLUMN IF NOT EXISTS {col} {typ}")

        conn.commit()
        _columns_initialized = True
    finally:
        cur.close()
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_staged(r) -> dict:
    # r[0]-r[21]: base fields (same as before)
    # r[22]-r[26]: default_parent_id, default_parent_name, default_branch, default_region, default_holding
    # r[27]-r[32]: master context (parent_id, parent_name, branch, region, holding, org)
    # r[33]: parent_missing (bool), r[34]: has_duplicate_id (bool)
    mapping_status = r[16] or "pending"
    master_dept_id = r[17]

    eff_dept_name   = (r[20] or "").strip() or (r[4] or "").strip()
    eff_org_name    = (r[21] or "").strip() or (r[9] or "").strip()
    eff_dept_id     = (r[3]  or "").strip()
    eff_parent_id   = (r[22] or "").strip() or (r[5]  or "").strip()
    eff_parent_name = (r[23] or "").strip() or (r[6]  or "").strip()
    eff_branch      = (r[24] or "").strip() or (r[10] or "").strip()
    eff_region      = (r[25] or "").strip() or (r[11] or "").strip()
    eff_holding     = (r[26] or "").strip() or (r[12] or "").strip()

    parent_missing    = bool(r[33])
    has_duplicate_id  = bool(r[34])
    source_has_children = bool(r[36]) if r[36] is not None else False
    master_has_children = r[37]  # None when no master row, True/False otherwise
    source_changed_val  = bool(r[38]) if r[38] is not None else False
    changed_fields_val  = r[39] or []
    last_batch_id_val   = r[40]
    seen_count_val      = int(r[41]) if r[41] is not None else 1
    last_seen_at_val    = str(r[42]) if r[42] else None

    source_level = 0 if not eff_parent_id else 1
    if not eff_parent_id and not source_has_children:
        source_node_type = "root"
    elif not eff_parent_id and source_has_children:
        source_node_type = "root_parent"
    elif eff_parent_id and not source_has_children:
        source_node_type = "leaf"
    else:
        source_node_type = "parent_child"

    master_parent_id_val = r[27]
    master_level      = None
    master_node_type  = None
    if master_dept_id is not None:
        has_mp = bool(master_parent_id_val)
        has_mc = bool(master_has_children)
        master_level = 0 if not has_mp else 1
        if not has_mp and not has_mc:
            master_node_type = "root"
        elif not has_mp and has_mc:
            master_node_type = "root_parent"
        elif has_mp and not has_mc:
            master_node_type = "leaf"
        else:
            master_node_type = "parent_child"

    exists_in_master = (master_dept_id is not None) and mapping_status in ("mapped", "auto")
    is_pending_state = mapping_status == "pending" or r[16] is None
    ready_to_create  = (
        is_pending_state
        and bool(eff_dept_id)
        and bool(eff_dept_name)
        and bool(eff_org_name)
        and not exists_in_master
        and not has_duplicate_id
        and not parent_missing
    )

    missing = []
    if is_pending_state and not exists_in_master:
        if not eff_dept_id:    missing.append("department_id")
        if not eff_dept_name:  missing.append("department_name")
        if not eff_org_name:   missing.append("organization_name")
        if parent_missing:     missing.append("parent_not_in_master")

    if mapping_status in ("mapped", "auto", "rejected"):
        computed_status = mapping_status
    elif has_duplicate_id:
        computed_status = "duplicate_warning"
    elif parent_missing:
        computed_status = "parent_missing"
    elif ready_to_create:
        computed_status = "ready_to_create"
    else:
        computed_status = "pending"

    # Human-readable status explanation
    blocking_reasons = []
    if is_pending_state and not exists_in_master:
        if not eff_dept_id:    blocking_reasons.append("Відсутній department_id")
        if not eff_dept_name:  blocking_reasons.append("Відсутня назва підрозділу")
        if not eff_org_name:   blocking_reasons.append("Відсутня організація")
        if parent_missing:     blocking_reasons.append(f"Parent «{eff_parent_id}» відсутній в довіднику")
        if has_duplicate_id:   blocking_reasons.append(f"ID «{eff_dept_id}» вже існує в dim_department")

    _status_reasons = {
        "mapped":            f"Прив'язано до {master_dept_id}",
        "auto":              f"Авто-прив'язано до {master_dept_id} (збіг за ID)",
        "rejected":          "Позначено як відхилено — скиньте статус для переприв'язки",
        "duplicate_warning": f"ID «{eff_dept_id}» вже існує в dim_department. Прив'яжіть до існуючого запису.",
        "parent_missing":    f"Parent «{eff_parent_id}» не знайдений в dim_department. Спочатку створіть parent.",
        "ready_to_create":   "Всі обов'язкові поля заповнені — можна створити master-підрозділ.",
        "pending":           ("Не вистачає полів: " + "; ".join(blocking_reasons)) if blocking_reasons
                             else "Очікує прив'язки або створення.",
    }
    _next_actions = {
        "mapped":            "done",
        "auto":              "done",
        "rejected":          "reset_if_needed",
        "duplicate_warning": "bind_existing",
        "parent_missing":    "create_parent_first",
        "ready_to_create":   "create_master",
        "pending":           "fill_fields" if blocking_reasons else "bind_or_create",
    }
    status_reason = _status_reasons.get(computed_status, "")
    next_action   = _next_actions.get(computed_status, "")

    return {
        "id":                               r[0],
        "source_id":                        r[1],
        "source_name":                      r[2],
        "source_department_id":             r[3],
        "source_department_name":           r[4],
        "source_parent_department_id":      r[5],
        "source_parent_department_name":    r[6],
        "source_separated_department_id":   r[7],
        "source_separated_department_name": r[8],
        "organization_name":                r[9],
        "branch_name":                      r[10],
        "region_name":                      r[11],
        "holding_name":                     r[12],
        "loaded_at":                        str(r[13]) if r[13] else None,
        "is_active":                        r[14],
        "mapping_id":                       r[15],
        "mapping_status":                   mapping_status,
        "master_department_id":             master_dept_id,
        "confidence":                       float(r[18]) if r[18] is not None else 0,
        "master_department_name":           r[19],
        "master_parent_id":                 r[27],
        "master_parent_name":               r[28],
        "master_branch":                    r[29],
        "master_region":                    r[30],
        "master_holding":                   r[31],
        "master_org":                       r[32],
        "exists_in_master":                 exists_in_master,
        "ready_to_create":                  ready_to_create,
        "parent_missing":                   parent_missing,
        "has_duplicate_id":                 has_duplicate_id,
        "computed_status":                  computed_status,
        "status_reason":                    status_reason,
        "next_action":                      next_action,
        "blocking_reasons":                 blocking_reasons,
        "missing_required_fields":          missing,
        "effective_department_id":          eff_dept_id,
        "effective_department_name":        eff_dept_name,
        "effective_organization_name":      eff_org_name,
        "effective_parent_id":              eff_parent_id,
        "effective_parent_name":            eff_parent_name,
        "effective_branch":                 eff_branch,
        "effective_region":                 eff_region,
        "effective_holding":                eff_holding,
        "extra_fields":                     r[35] or {},
        "source_has_children":              source_has_children,
        "source_level":                     source_level,
        "source_node_type":                 source_node_type,
        "master_has_children":              master_has_children,
        "master_level":                     master_level,
        "master_node_type":                 master_node_type,
        "source_changed":                   source_changed_val,
        "changed_fields":                   changed_fields_val,
        "last_batch_id":                    last_batch_id_val,
        "seen_count":                       seen_count_val,
        "last_seen_at":                     last_seen_at_val,
    }


def _has_any_filter(f: BulkFillFilters) -> bool:
    return bool(
        f.source_id or f.organization_name or f.branch_name or f.region_name
        or f.master_department_id
        or (f.mapping_status and f.mapping_status not in ("all",))
        or f.search
        or f.has_parent or f.parent_status or f.parent_department_id or f.parent_department_name
        or f.source_level is not None or f.source_node_type
    )


_BULK_EFF_PID = ("COALESCE(NULLIF(dds.default_parent_department_id,''),"
                 " NULLIF(dds.source_parent_department_id,''))")
_BULK_EFF_PNM = ("COALESCE(NULLIF(dds.default_parent_department_name,''),"
                 " NULLIF(dds.source_parent_department_name,''))")
_BULK_EFF_ORG = ("COALESCE(NULLIF(dds.default_organization_name,''),"
                 " NULLIF(dds.organization_name,''))")
_BULK_EFF_BRN = ("COALESCE(NULLIF(dds.default_branch_name,''),"
                 " NULLIF(dds.branch_name,''))")
_BULK_EFF_REG = ("COALESCE(NULLIF(dds.default_region_name,''),"
                 " NULLIF(dds.region_name,''))")

# Effective name / org for readiness checks (shared between all bulk endpoints)
_READY_EFF_NAME = ("COALESCE(NULLIF(dds.default_department_name,''),"
                   " NULLIF(dds.source_department_name,''))")
_READY_EFF_ORG  = _BULK_EFF_ORG   # same expression

# Reusable SQL boolean expressions for computed readiness fields
_PARENT_MISSING_SQL = (
    f"({_BULK_EFF_PID} IS NOT NULL AND {_BULK_EFF_PID} <> ''"
    f" AND NOT EXISTS ("
    f"     SELECT 1 FROM dim_department pd"
    f"     WHERE pd.department_id = {_BULK_EFF_PID} AND pd.is_active = TRUE"
    f" ))"
)

_HAS_DUP_ID_SQL = (
    "EXISTS ("
    "    SELECT 1 FROM dim_department ed"
    "    WHERE ed.department_id = dds.source_department_id"
    ")"
)

_IS_READY_SQL = (
    f"({_IS_PENDING}"
    f" AND NULLIF(dds.source_department_id, '') IS NOT NULL"
    f" AND NULLIF({_READY_EFF_NAME}, '') IS NOT NULL"
    f" AND NULLIF({_READY_EFF_ORG},  '') IS NOT NULL"
    f" AND NOT {_PARENT_MISSING_SQL}"
    f" AND NOT {_HAS_DUP_ID_SQL})"
)

# Source-hierarchy filters for bulk queries
_BULK_HAS_PARENT   = f"({_BULK_EFF_PID} IS NOT NULL AND {_BULK_EFF_PID} != '')"
_BULK_SRC_CHILDREN = (
    "EXISTS (SELECT 1 FROM dim_department_source s2"
    " WHERE s2.source_parent_department_id = dds.source_department_id"
    " AND s2.source_id = dds.source_id)"
)


def _build_bulk_where(f: BulkFillFilters):
    where  = []
    params = []

    if f.source_id:
        where.append("dds.source_id = %s")
        params.append(f.source_id)

    if f.organization_name:
        where.append(f"{_BULK_EFF_ORG} ILIKE %s")
        params.append(f"%{f.organization_name}%")

    if f.branch_name:
        where.append(f"{_BULK_EFF_BRN} ILIKE %s")
        params.append(f"%{f.branch_name}%")

    if f.region_name:
        where.append(f"{_BULK_EFF_REG} ILIKE %s")
        params.append(f"%{f.region_name}%")

    if f.master_department_id:
        where.append("dsm.master_department_id = %s")
        params.append(f.master_department_id)

    if f.search:
        where.append(
            "(dds.source_department_name ILIKE %s"
            " OR dds.source_department_id  ILIKE %s"
            " OR dds.organization_name     ILIKE %s)"
        )
        params += [f"%{f.search}%", f"%{f.search}%", f"%{f.search}%"]

    if f.mapping_status and f.mapping_status not in ("all",):
        if f.mapping_status == "pending":
            where.append("(dsm.mapping_status = 'pending' OR dsm.mapping_status IS NULL)")
        else:
            where.append("dsm.mapping_status = %s")
            params.append(f.mapping_status)

    if f.parent_department_id:
        where.append(f"{_BULK_EFF_PID} ILIKE %s")
        params.append(f"%{f.parent_department_id}%")

    if f.parent_department_name:
        where.append(f"{_BULK_EFF_PNM} ILIKE %s")
        params.append(f"%{f.parent_department_name}%")

    if f.has_parent == "with":
        where.append(f"{_BULK_EFF_PID} IS NOT NULL")
    elif f.has_parent == "without":
        where.append(f"{_BULK_EFF_PID} IS NULL")

    if f.parent_status == "found":
        where.append(
            f"{_BULK_EFF_PID} IS NOT NULL"
            f" AND EXISTS (SELECT 1 FROM dim_department pd"
            f" WHERE pd.department_id = {_BULK_EFF_PID} AND pd.is_active = TRUE)"
        )
    elif f.parent_status == "missing":
        where.append(
            f"{_BULK_EFF_PID} IS NOT NULL"
            f" AND NOT EXISTS (SELECT 1 FROM dim_department pd"
            f" WHERE pd.department_id = {_BULK_EFF_PID} AND pd.is_active = TRUE)"
        )

    if f.source_level is not None:
        if f.source_level == 0:
            where.append(f"NOT {_BULK_HAS_PARENT}")
        else:
            where.append(_BULK_HAS_PARENT)

    if f.source_node_type:
        if f.source_node_type == "root":
            where.append(f"NOT {_BULK_HAS_PARENT} AND NOT {_BULK_SRC_CHILDREN}")
        elif f.source_node_type == "root_parent":
            where.append(f"NOT {_BULK_HAS_PARENT} AND {_BULK_SRC_CHILDREN}")
        elif f.source_node_type == "leaf":
            where.append(f"{_BULK_HAS_PARENT} AND NOT {_BULK_SRC_CHILDREN}")
        elif f.source_node_type == "parent_child":
            where.append(f"{_BULK_HAS_PARENT} AND {_BULK_SRC_CHILDREN}")

    return (" AND ".join(where)) if where else "TRUE", params


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/staged")
def get_staged(
    source_id:              Optional[int] = None,
    mapping_status:         Optional[str] = None,
    computed_status:        Optional[str] = None,
    organization_name:      Optional[str] = None,
    branch_name:            Optional[str] = None,
    region_name:            Optional[str] = None,
    master_department_id:   Optional[str] = None,
    search:                 Optional[str] = None,
    parent_department_id:   Optional[str] = None,
    parent_department_name: Optional[str] = None,
    has_parent:             Optional[str] = None,
    parent_status:          Optional[str] = None,
    source_level:           Optional[int] = None,
    source_node_type:       Optional[str] = None,
    source_changed:         Optional[bool] = None,
    page:                   int = 1,
    page_size:              int = 100,
    _u=Depends(get_current_user),
):
    _ensure_dept_source_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        conds  = []
        params = []

        if source_id:
            conds.append("dds.source_id = %s")
            params.append(source_id)

        if mapping_status:
            if mapping_status == "pending":
                conds.append("(dsm.mapping_status = 'pending' OR dsm.mapping_status IS NULL)")
            else:
                conds.append("dsm.mapping_status = %s")
                params.append(mapping_status)

        if computed_status:
            if computed_status == "ready_to_create":
                conds.append(_IS_READY_SQL)
            elif computed_status == "parent_missing":
                conds.append(f"{_IS_PENDING} AND {_PARENT_MISSING_SQL}")
            elif computed_status == "duplicate_warning":
                conds.append(f"{_IS_PENDING} AND {_HAS_DUP_ID_SQL} AND NOT {_PARENT_MISSING_SQL}")

        _EFF_ORG_COND = ("COALESCE(NULLIF(dds.default_organization_name,''), dds.organization_name)")
        _EFF_BRN_COND = ("COALESCE(NULLIF(dds.default_branch_name,''),       dds.branch_name)")
        _EFF_REG_COND = ("COALESCE(NULLIF(dds.default_region_name,''),       dds.region_name)")

        if organization_name:
            conds.append(f"{_EFF_ORG_COND} ILIKE %s")
            params.append(f"%{organization_name}%")

        if branch_name:
            conds.append(f"{_EFF_BRN_COND} ILIKE %s")
            params.append(f"%{branch_name}%")

        if region_name:
            conds.append(f"{_EFF_REG_COND} ILIKE %s")
            params.append(f"%{region_name}%")

        if master_department_id:
            conds.append("dsm.master_department_id = %s")
            params.append(master_department_id)

        if search:
            conds.append(
                "(dds.source_department_name ILIKE %s"
                " OR dds.source_department_id  ILIKE %s"
                " OR dds.organization_name     ILIKE %s)"
            )
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]

        _EFF_PID = ("COALESCE(NULLIF(dds.default_parent_department_id,''),"
                    " NULLIF(dds.source_parent_department_id,''))")
        _EFF_PNM = ("COALESCE(NULLIF(dds.default_parent_department_name,''),"
                    " NULLIF(dds.source_parent_department_name,''))")

        if parent_department_id:
            conds.append(f"{_EFF_PID} ILIKE %s")
            params.append(f"%{parent_department_id}%")

        if parent_department_name:
            conds.append(f"{_EFF_PNM} ILIKE %s")
            params.append(f"%{parent_department_name}%")

        if has_parent == "with":
            conds.append(f"{_EFF_PID} IS NOT NULL")
        elif has_parent == "without":
            conds.append(f"{_EFF_PID} IS NULL")

        if parent_status == "found":
            conds.append(
                f"{_EFF_PID} IS NOT NULL"
                f" AND EXISTS (SELECT 1 FROM dim_department pd"
                f" WHERE pd.department_id = {_EFF_PID} AND pd.is_active = TRUE)"
            )
        elif parent_status == "missing":
            conds.append(
                f"{_EFF_PID} IS NOT NULL"
                f" AND NOT EXISTS (SELECT 1 FROM dim_department pd"
                f" WHERE pd.department_id = {_EFF_PID} AND pd.is_active = TRUE)"
            )

        _HAS_PARENT_COND = f"({_EFF_PID} IS NOT NULL AND {_EFF_PID} != '')"
        _SRC_CHILDREN    = (
            "EXISTS (SELECT 1 FROM dim_department_source s2"
            " WHERE s2.source_parent_department_id = dds.source_department_id"
            " AND s2.source_id = dds.source_id)"
        )

        if source_level is not None:
            if source_level == 0:
                conds.append(f"NOT {_HAS_PARENT_COND}")
            else:
                conds.append(_HAS_PARENT_COND)

        if source_node_type:
            if source_node_type == "root":
                conds.append(f"NOT {_HAS_PARENT_COND} AND NOT {_SRC_CHILDREN}")
            elif source_node_type == "root_parent":
                conds.append(f"NOT {_HAS_PARENT_COND} AND {_SRC_CHILDREN}")
            elif source_node_type == "leaf":
                conds.append(f"{_HAS_PARENT_COND} AND NOT {_SRC_CHILDREN}")
            elif source_node_type == "parent_child":
                conds.append(f"{_HAS_PARENT_COND} AND {_SRC_CHILDREN}")

        if source_changed is not None:
            conds.append("dds.source_changed = %s")
            params.append(source_changed)

        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        cur.execute(
            f"""SELECT
                    COUNT(*)                                                          AS total,
                    COUNT(*) FILTER (WHERE dsm.mapping_status = 'pending'
                                      OR   dsm.mapping_status IS NULL)               AS pending,
                    COUNT(*) FILTER (WHERE dsm.mapping_status = 'mapped')            AS mapped,
                    COUNT(*) FILTER (WHERE dsm.mapping_status = 'rejected')          AS rejected,
                    COUNT(*) FILTER (WHERE dsm.mapping_status = 'auto')              AS auto_bound,
                    COUNT(*) FILTER (WHERE {_IS_READY_SQL})                          AS ready_to_create,
                    COUNT(*) FILTER (WHERE {_IS_PENDING} AND {_PARENT_MISSING_SQL})  AS parent_missing,
                    COUNT(*) FILTER (WHERE {_IS_PENDING} AND {_HAS_DUP_ID_SQL}
                                      AND NOT {_PARENT_MISSING_SQL})                 AS duplicate_warning,
                    COUNT(*) FILTER (WHERE dds.source_changed = TRUE)                AS changed_source
               FROM dim_department_source dds
               LEFT JOIN department_source_mapping dsm
                      ON dsm.source_id = dds.source_id
                     AND dsm.source_department_id = dds.source_department_id
               LEFT JOIN dim_department d ON d.department_id = dsm.master_department_id
               {where}""",
            params,
        )
        kpi = cur.fetchone()

        offset = (page - 1) * page_size
        cur.execute(
            f"""SELECT
                    dds.id, dds.source_id, dds.source_name,
                    dds.source_department_id, dds.source_department_name,
                    dds.source_parent_department_id, dds.source_parent_department_name,
                    dds.source_separated_department_id, dds.source_separated_department_name,
                    dds.organization_name, dds.branch_name, dds.region_name, dds.holding_name,
                    dds.loaded_at, dds.is_active,
                    dsm.id            AS mapping_id,
                    dsm.mapping_status,
                    dsm.master_department_id,
                    dsm.confidence,
                    d.department_name    AS master_department_name,
                    dds.default_department_name,
                    dds.default_organization_name,
                    dds.default_parent_department_id,
                    dds.default_parent_department_name,
                    dds.default_branch_name,
                    dds.default_region_name,
                    dds.default_holding_name,
                    d.parent_department_id   AS master_parent_id,
                    d.parent_department_name AS master_parent_name,
                    d.branch_name            AS master_branch,
                    d.region_name            AS master_region,
                    d.holding_name           AS master_holding,
                    d.organization_name      AS master_org,
                    {_PARENT_MISSING_SQL} AS parent_missing,
                    {_HAS_DUP_ID_SQL}     AS has_duplicate_id,
                    dds.extra_fields,
                    EXISTS (
                        SELECT 1 FROM dim_department_source s2
                        WHERE s2.source_parent_department_id = dds.source_department_id
                          AND s2.source_id = dds.source_id
                    ) AS source_has_children,
                    CASE WHEN d.department_id IS NULL THEN NULL
                         WHEN EXISTS (
                             SELECT 1 FROM dim_department c
                             WHERE c.parent_department_id = d.department_id
                               AND COALESCE(c.is_deleted, FALSE) = FALSE
                         ) THEN TRUE ELSE FALSE END AS master_has_children,
                    dds.source_changed,
                    dds.changed_fields,
                    dds.last_batch_id,
                    dds.seen_count,
                    dds.last_seen_at
               FROM dim_department_source dds
               LEFT JOIN department_source_mapping dsm
                      ON dsm.source_id = dds.source_id
                     AND dsm.source_department_id = dds.source_department_id
               LEFT JOIN dim_department d ON d.department_id = dsm.master_department_id
               {where}
               ORDER BY
                   CASE WHEN dsm.mapping_status = 'pending' OR dsm.mapping_status IS NULL THEN 0
                        WHEN dsm.mapping_status = 'mapped'  THEN 1
                        WHEN dsm.mapping_status = 'auto'    THEN 2
                        ELSE 3 END,
                   dds.source_department_name
               LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = [_row_to_staged(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT DISTINCT source_id, source_name FROM dim_department_source ORDER BY source_name"
        )
        sources = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

        cur.execute(
            """SELECT DISTINCT COALESCE(NULLIF(default_organization_name,''), organization_name) AS v
               FROM dim_department_source
               WHERE COALESCE(NULLIF(default_organization_name,''), organization_name) IS NOT NULL
                 AND COALESCE(NULLIF(default_organization_name,''), organization_name) != ''
               ORDER BY v"""
        )
        organizations = [r[0] for r in cur.fetchall()]

        cur.execute(
            """SELECT DISTINCT COALESCE(NULLIF(default_branch_name,''), branch_name) AS v
               FROM dim_department_source
               WHERE COALESCE(NULLIF(default_branch_name,''), branch_name) IS NOT NULL
                 AND COALESCE(NULLIF(default_branch_name,''), branch_name) != ''
               ORDER BY v"""
        )
        branches = [r[0] for r in cur.fetchall()]

        cur.execute(
            """SELECT DISTINCT COALESCE(NULLIF(default_region_name,''), region_name) AS v
               FROM dim_department_source
               WHERE COALESCE(NULLIF(default_region_name,''), region_name) IS NOT NULL
                 AND COALESCE(NULLIF(default_region_name,''), region_name) != ''
               ORDER BY v"""
        )
        regions = [r[0] for r in cur.fetchall()]

        return {
            "total":             int(kpi[0]),
            "pending":           int(kpi[1]),
            "mapped":            int(kpi[2]),
            "rejected":          int(kpi[3]),
            "auto_bound":        int(kpi[4]),
            "ready_to_create":   int(kpi[5]),
            "parent_missing":    int(kpi[6]),
            "duplicate_warning": int(kpi[7]),
            "changed_source":    int(kpi[8]),
            "page":          page,
            "page_size":     page_size,
            "rows":          rows,
            "sources":       sources,
            "organizations": organizations,
            "branches":      branches,
            "regions":       regions,
        }
    finally:
        cur.close()
        conn.close()


@router.get("/masters")
def get_masters(_u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT department_id, department_name, organization_name,
                      branch_name, region_name, holding_name,
                      parent_department_id, parent_department_name
               FROM dim_department
               WHERE is_active = TRUE
               ORDER BY organization_name, department_name"""
        )
        return [
            {
                "department_id":        r[0],
                "department_name":      r[1],
                "organization_name":    r[2],
                "branch_name":          r[3],
                "region_name":          r[4],
                "holding_name":         r[5],
                "parent_department_id": r[6],
                "parent_department_name": r[7],
            }
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


@router.post("/bind")
def bind_department(body: BindRequest, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM dim_department WHERE department_id = %s AND is_active = TRUE",
            (body.master_department_id,),
        )
        if not cur.fetchone():
            raise HTTPException(
                404,
                f"Master department '{body.master_department_id}' not found or inactive",
            )

        cur.execute(
            """INSERT INTO department_source_mapping
                   (source_id, source_department_id, master_department_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
               ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                   master_department_id = EXCLUDED.master_department_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_department_id, body.master_department_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/suggest-match")
def suggest_match(body: SuggestMatchRequest, _u=Depends(get_current_user)):
    """Return scored master-department candidates for a given source row."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT dds.source_department_id, dds.source_department_name,
                      dds.organization_name, dds.branch_name, dds.region_name, dds.holding_name,
                      dds.source_parent_department_id, dds.source_parent_department_name,
                      dds.default_department_name, dds.default_organization_name,
                      dds.default_parent_department_id, dds.default_parent_department_name,
                      dds.default_branch_name, dds.default_region_name, dds.default_holding_name
               FROM dim_department_source dds
               WHERE dds.source_id = %s AND dds.source_department_id = %s""",
            (body.source_id, body.source_department_id),
        )
        src_row = cur.fetchone()
        if not src_row:
            raise HTTPException(404, "Source department not found")

        def eff(a, b):
            return ((a or "").strip() or (b or "").strip()).lower()

        src_dept_id    = (src_row[0] or "").strip().lower()
        src_dept_name  = eff(src_row[8],  src_row[1])
        src_org        = eff(src_row[9],  src_row[2])
        src_branch     = eff(src_row[12], src_row[3])
        src_region     = eff(src_row[13], src_row[4])
        src_holding    = eff(src_row[14], src_row[5])
        src_parent_id  = eff(src_row[10], src_row[6])
        src_parent_nm  = eff(src_row[11], src_row[7])

        cur.execute(
            """SELECT department_id, department_name, organization_name,
                      branch_name, region_name, holding_name,
                      parent_department_id, parent_department_name
               FROM dim_department
               WHERE COALESCE(is_deleted, FALSE) = FALSE AND is_active = TRUE"""
        )
        masters = cur.fetchall()

        _WEIGHTS = {
            "dept_id":     100,
            "dept_name":   30,
            "org":         20,
            "branch":      15,
            "parent_id":   15,
            "region":      10,
            "holding":     10,
            "parent_name": 10,
        }

        def score_master(m):
            m_id  = (m[0] or "").strip().lower()
            m_nm  = (m[1] or "").strip().lower()
            m_org = (m[2] or "").strip().lower()
            m_br  = (m[3] or "").strip().lower()
            m_re  = (m[4] or "").strip().lower()
            m_ho  = (m[5] or "").strip().lower()
            m_pid = (m[6] or "").strip().lower()
            m_pnm = (m[7] or "").strip().lower()

            matched   = []
            mismatched = []
            score = 0

            def chk(field, src_v, mst_v, weight):
                nonlocal score
                if src_v and mst_v:
                    if src_v == mst_v:
                        score += weight; matched.append(field)
                    else:
                        mismatched.append(field)
                elif src_v and not mst_v:
                    mismatched.append(field)

            chk("dept_id",     src_dept_id,   m_id,  _WEIGHTS["dept_id"])
            chk("dept_name",   src_dept_name,  m_nm,  _WEIGHTS["dept_name"])
            chk("org",         src_org,        m_org, _WEIGHTS["org"])
            chk("branch",      src_branch,     m_br,  _WEIGHTS["branch"])
            chk("parent_id",   src_parent_id,  m_pid, _WEIGHTS["parent_id"])
            chk("region",      src_region,     m_re,  _WEIGHTS["region"])
            chk("holding",     src_holding,    m_ho,  _WEIGHTS["holding"])
            chk("parent_name", src_parent_nm,  m_pnm, _WEIGHTS["parent_name"])

            return score, matched, mismatched

        scored = []
        for m in masters:
            sc, matched, mismatched = score_master(m)
            if sc > 0:
                scored.append({
                    "department_id":          m[0],
                    "department_name":        m[1],
                    "organization_name":      m[2],
                    "branch_name":            m[3],
                    "region_name":            m[4],
                    "holding_name":           m[5],
                    "parent_department_id":   m[6],
                    "parent_department_name": m[7],
                    "score":                  sc,
                    "matched_fields":         matched,
                    "mismatched_fields":      mismatched,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        candidates = scored[:5]
        best = candidates[0] if candidates else None

        score_level = "none"
        if best:
            if best["score"] >= 80:
                score_level = "high"
            elif best["score"] >= 50:
                score_level = "medium"
            else:
                score_level = "low"

        return {
            "best_match":   best,
            "candidates":   candidates,
            "score_level":  score_level,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/reject")
def reject_department(body: RejectRequest, _u=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO department_source_mapping
                   (source_id, source_department_id, master_department_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, NULL, 'rejected', 0, %s, NOW())
               ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                   master_department_id = NULL,
                   mapping_status  = 'rejected',
                   confidence      = 0,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_department_id, _u["id"]),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/bind/{source_id}/{source_department_id}")
def reset_mapping(source_id: int, source_department_id: str, _u=Depends(get_current_user)):
    """Reset mapping to pending (Скинути)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE department_source_mapping
               SET master_department_id = NULL,
                   mapping_status = 'pending',
                   confidence     = 0,
                   mapped_by      = %s,
                   updated_at     = NOW()
               WHERE source_id = %s AND source_department_id = %s""",
            (_u["id"], source_id, source_department_id),
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/auto-bind")
def auto_bind(body: AutoBindRequest, _u=Depends(get_current_user)):
    """Auto-bind pending source departments where source_department_id exactly matches dim_department.department_id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        source_cond  = "AND dds.source_id = %s" if body.source_id else ""
        source_param = [body.source_id] if body.source_id else []

        cur.execute(
            f"""WITH candidates AS (
                    SELECT dds.source_id, dds.source_department_id, d.department_id AS master_dept_id
                    FROM dim_department_source dds
                    JOIN dim_department d
                      ON d.department_id = dds.source_department_id
                     AND d.is_active = TRUE
                    LEFT JOIN department_source_mapping dsm
                           ON dsm.source_id = dds.source_id
                          AND dsm.source_department_id = dds.source_department_id
                    WHERE (dsm.mapping_status = 'pending' OR dsm.mapping_status IS NULL)
                    {source_cond}
                )
                INSERT INTO department_source_mapping
                    (source_id, source_department_id, master_department_id,
                     mapping_status, confidence, mapped_by, updated_at)
                SELECT source_id, source_department_id, master_dept_id, 'auto', 95, %s, NOW()
                FROM candidates
                ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                    master_department_id = EXCLUDED.master_department_id,
                    mapping_status  = 'auto',
                    confidence      = 95,
                    mapped_by       = EXCLUDED.mapped_by,
                    updated_at      = NOW()
                WHERE department_source_mapping.mapping_status IN ('pending')
                   OR department_source_mapping.mapping_status IS NULL""",
            source_param + [_u["id"]],
        )
        bound = cur.rowcount
        conn.commit()
        return {"ok": True, "auto_bound": bound}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.post("/create-master-from-source")
def create_master_from_source(body: CreateMasterRequest, _u=Depends(get_current_user)):
    """Create a dim_department entry using form values and immediately bind."""
    _ensure_dept_source_columns()
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Verify source exists and check current mapping state
        cur.execute(
            """SELECT dsm.mapping_status, dsm.master_department_id
               FROM dim_department_source dds
               LEFT JOIN department_source_mapping dsm
                      ON dsm.source_id = dds.source_id
                     AND dsm.source_department_id = dds.source_department_id
               WHERE dds.source_id = %s AND dds.source_department_id = %s""",
            (body.source_id, body.source_department_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Source department not found")
        mapping_status, master_dept_id = row
        if master_dept_id is not None and mapping_status in ("mapped", "auto"):
            raise HTTPException(409, "Підрозділ вже прив'язаний до майстра")

        dept_id_s   = (body.department_id    or "").strip()
        name_s      = (body.department_name  or "").strip()
        org_s       = (body.organization_name or "").strip()
        parent_id_s = (body.parent_department_id   or "").strip() or None
        parent_nm_s = (body.parent_department_name or "").strip() or None
        branch_s    = (body.branch_name  or "").strip() or None
        region_s    = (body.region_name  or "").strip() or None
        holding_s   = (body.holding_name or "").strip() or None

        if not dept_id_s:
            raise HTTPException(400, "department_id обов'язковий")
        if not name_s:
            raise HTTPException(400, "Назва підрозділу обов'язкова")
        if not org_s:
            raise HTTPException(400, "Організація обов'язкова")

        # Block on dept_id duplicate
        cur.execute("SELECT 1 FROM dim_department WHERE department_id = %s", (dept_id_s,))
        if cur.fetchone():
            raise HTTPException(409, f"department_id «{dept_id_s}» вже існує в dim_department")

        # If parent provided, verify it exists in master
        if parent_id_s:
            cur.execute(
                "SELECT 1 FROM dim_department WHERE department_id = %s AND is_active = TRUE",
                (parent_id_s,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    409,
                    f"Батьківський підрозділ «{parent_id_s}» не знайдено в dim_department. "
                    "Спочатку створіть батьківський підрозділ."
                )

        # Block on name+org+branch+parent_id combo duplicate
        cur.execute(
            """SELECT department_id FROM dim_department
               WHERE LOWER(TRIM(department_name))   = %s
                 AND LOWER(TRIM(organization_name))  = %s
                 AND COALESCE(branch_name, '')        = COALESCE(%s, '')
                 AND COALESCE(parent_department_id, '') = COALESCE(%s, '')""",
            (name_s.lower(), org_s.lower(), branch_s, parent_id_s),
        )
        dup = cur.fetchone()
        if dup:
            raise HTTPException(
                409,
                f"Підрозділ з такою назвою, організацією, філією та parent вже існує: [{dup[0]}]"
            )

        cur.execute(
            """INSERT INTO dim_department
                   (department_id, department_name, organization_name,
                    branch_name, region_name, holding_name,
                    parent_department_id, parent_department_name, is_active,
                    holding_id, organization_id, region_id, branch_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s)""",
            (dept_id_s, name_s, org_s, branch_s, region_s, holding_s, parent_id_s, parent_nm_s,
             body.holding_id, body.organization_id, body.region_id, body.branch_id),
        )

        cur.execute(
            """INSERT INTO department_source_mapping
                   (source_id, source_department_id, master_department_id,
                    mapping_status, confidence, mapped_by, updated_at)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
               ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                   master_department_id = EXCLUDED.master_department_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   updated_at      = NOW()""",
            (body.source_id, body.source_department_id, dept_id_s, _u["id"]),
        )
        conn.commit()
        return {"ok": True, "department_id": dept_id_s, "department_name": name_s}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


@router.get("/duplicate-check")
def duplicate_check(
    department_id:       Optional[str] = None,
    department_name:     Optional[str] = None,
    organization_name:   Optional[str] = None,
    branch_name:         Optional[str] = None,
    parent_department_id: Optional[str] = None,
    _u=Depends(get_current_user),
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        id_exists    = False
        combo_exists = False
        matches: list = []

        if department_id and department_id.strip():
            cur.execute(
                "SELECT department_id, department_name, organization_name FROM dim_department"
                " WHERE department_id = %s",
                (department_id.strip(),),
            )
            for r in cur.fetchall():
                id_exists = True
                matches.append({"department_id": r[0], "department_name": r[1],
                                 "organization_name": r[2]})

        if department_name and department_name.strip() and organization_name and organization_name.strip():
            branch_v = (branch_name or "").strip() or None
            parent_v = (parent_department_id or "").strip() or None
            cur.execute(
                """SELECT department_id, department_name, organization_name FROM dim_department
                   WHERE LOWER(TRIM(department_name))     = %s
                     AND LOWER(TRIM(organization_name))   = %s
                     AND COALESCE(branch_name, '')         = COALESCE(%s, '')
                     AND COALESCE(parent_department_id,'') = COALESCE(%s, '')""",
                (department_name.strip().lower(), organization_name.strip().lower(),
                 branch_v, parent_v),
            )
            for r in cur.fetchall():
                combo_exists = True
                entry = {"department_id": r[0], "department_name": r[1],
                         "organization_name": r[2]}
                if entry not in matches:
                    matches.append(entry)

        return {
            "id_exists":    id_exists,
            "combo_exists": combo_exists,
            "matches":      matches,
        }
    finally:
        cur.close()
        conn.close()


# ── Bulk fill ─────────────────────────────────────────────────────────────────

@router.post("/bulk-fill-preview")
def bulk_fill_preview(body: BulkFillRequest, _u=Depends(get_current_user)):
    _ensure_dept_source_columns()
    if body.field not in _ALL_FILL_FIELDS:
        return {"status": "error", "message": f"Поле '{body.field}' не дозволено"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Звузьте вибірку фільтром або пошуком"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)
        cur.execute(
            f"""SELECT
                    COUNT(DISTINCT dsm.master_department_id) FILTER (WHERE {_IS_MAPPED}),
                    COUNT(*)                                 FILTER (WHERE {_IS_PENDING})
                {_BULK_JOIN}
                WHERE {where_sql}""",
            params,
        )
        row = cur.fetchone()
        affected_master_count = int(row[0])
        affected_source_count = int(row[1])

        warnings = []
        if affected_master_count == 0 and affected_source_count == 0:
            warnings.append("Жодного рядка не знайдено за поточними фільтрами.")

        return {
            "status":                "ok",
            "affected_master_count": affected_master_count,
            "affected_source_count": affected_source_count,
            "total_affected_count":  affected_master_count + affected_source_count,
            "field_label":           _FIELD_LABELS[body.field],
            "value":                 body.value,
            "value_id":              body.value_id,
            "warnings":              warnings,
        }
    finally:
        cur.close()
        conn.close()


@router.post("/bulk-fill")
def bulk_fill(body: BulkFillRequest, _u=Depends(get_current_user)):
    _ensure_dept_source_columns()
    if not body.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if body.field not in _ALL_FILL_FIELDS:
        return {"status": "error", "message": f"Поле '{body.field}' не дозволено"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Звузьте вибірку фільтром або пошуком"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)

        cur.execute(
            f"SELECT DISTINCT dsm.master_department_id {_BULK_JOIN} WHERE {where_sql} AND {_IS_MAPPED}",
            params,
        )
        master_ids = [r[0] for r in cur.fetchall()]
        updated_masters = 0

        if body.field == "parent_department":
            # Dual-column update: parent ID + parent name
            if master_ids:
                cur.execute(
                    "UPDATE dim_department SET parent_department_id = %s, parent_department_name = %s"
                    " WHERE department_id = ANY(%s)",
                    (body.value_id, body.value, master_ids),
                )
                updated_masters = cur.rowcount

            cur.execute(
                f"""UPDATE dim_department_source
                       SET default_parent_department_id = %s, default_parent_department_name = %s
                   WHERE (source_id, source_department_id) IN (
                       SELECT dds.source_id, dds.source_department_id
                       {_BULK_JOIN}
                       WHERE {where_sql} AND {_IS_PENDING}
                   )""",
                [body.value_id, body.value] + params,
            )

        elif body.field in _PAIRED_ID_COLS and body.value_id is not None:
            # Name + integer ID update
            staging_name_col           = _ALLOWED_FIELDS[body.field]
            staging_id_col, dim_id_col = _PAIRED_ID_COLS[body.field]
            dim_name_col               = _DIM_FIELDS[body.field]
            int_id = int(body.value_id)

            if master_ids:
                cur.execute(
                    f"UPDATE dim_department SET {dim_name_col} = %s, {dim_id_col} = %s"
                    f" WHERE department_id = ANY(%s)",
                    (body.value, int_id, master_ids),
                )
                updated_masters = cur.rowcount

            cur.execute(
                f"""UPDATE dim_department_source
                        SET {staging_name_col} = %s, {staging_id_col} = %s
                    WHERE (source_id, source_department_id) IN (
                        SELECT dds.source_id, dds.source_department_id
                        {_BULK_JOIN}
                        WHERE {where_sql} AND {_IS_PENDING}
                    )""",
                [body.value, int_id] + params,
            )

        else:
            # Simple single-column text update
            dim_field   = _DIM_FIELDS[body.field]
            staging_col = _ALLOWED_FIELDS[body.field]

            if master_ids:
                cur.execute(
                    f"UPDATE dim_department SET {dim_field} = %s WHERE department_id = ANY(%s)",
                    (body.value, master_ids),
                )
                updated_masters = cur.rowcount

            cur.execute(
                f"""UPDATE dim_department_source SET {staging_col} = %s
                    WHERE (source_id, source_department_id) IN (
                        SELECT dds.source_id, dds.source_department_id
                        {_BULK_JOIN}
                        WHERE {where_sql} AND {_IS_PENDING}
                    )""",
                [body.value] + params,
            )

        updated_staging = cur.rowcount
        conn.commit()
        return {
            "status":          "ok",
            "updated_masters": updated_masters,
            "updated_staging": updated_staging,
            "field_label":     _FIELD_LABELS[body.field],
            "value":           body.value,
            "value_id":        body.value_id,
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── Bulk create ───────────────────────────────────────────────────────────────

@router.post("/bulk-create-preview")
def bulk_create_preview(body: BulkCreateRequest, _u=Depends(get_current_user)):
    _ensure_dept_source_columns()
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Звузьте вибірку фільтром або пошуком"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)

        # Single pass: count all readiness categories using shared SQL expressions
        cur.execute(
            f"""SELECT
                    COUNT(*) FILTER (WHERE {_IS_PENDING})                                                AS total_pending,
                    COUNT(*) FILTER (WHERE {_IS_READY_SQL})                                              AS will_create,
                    COUNT(*) FILTER (WHERE {_IS_PENDING} AND {_PARENT_MISSING_SQL})                      AS parent_missing,
                    COUNT(*) FILTER (WHERE {_IS_PENDING} AND {_HAS_DUP_ID_SQL}
                                      AND NOT {_PARENT_MISSING_SQL})                                     AS dup_id,
                    COUNT(*) FILTER (WHERE {_IS_PENDING}
                                      AND NULLIF(dds.source_department_id, '') IS NULL)                  AS missing_id,
                    COUNT(*) FILTER (WHERE {_IS_PENDING}
                                      AND NULLIF({_READY_EFF_NAME}, '') IS NULL)                         AS missing_name,
                    COUNT(*) FILTER (WHERE {_IS_PENDING}
                                      AND NULLIF({_READY_EFF_ORG},  '') IS NULL)                         AS missing_org
                {_BULK_JOIN}
                WHERE {where_sql}""",
            params,
        )
        r = cur.fetchone()
        total_pending  = int(r[0])
        will_create    = int(r[1])
        parent_missing = int(r[2])
        dup_id         = int(r[3])
        missing_id     = int(r[4])
        missing_name   = int(r[5])
        missing_org    = int(r[6])

        # Up to 10 examples of ready rows
        cur.execute(
            f"""SELECT dds.source_department_id, {_READY_EFF_NAME}, {_READY_EFF_ORG}
                {_BULK_JOIN}
                WHERE {where_sql} AND {_IS_READY_SQL}
                ORDER BY dds.source_department_name LIMIT 10""",
            params,
        )
        will_create_examples = [
            {"source_department_id": row[0], "source_department_name": row[1], "organization_name": row[2]}
            for row in cur.fetchall()
        ]

        return {
            "status":           "ok",
            "total_pending":    total_pending,
            "will_create":      will_create,
            "skipped_existing": dup_id,
            "parent_missing":   parent_missing,
            "missing_id":       missing_id,
            "missing_name":     missing_name,
            "missing_org":      missing_org,
            "examples": {
                "will_create": will_create_examples,
            },
            "can_apply": will_create > 0,
        }
    finally:
        cur.close()
        conn.close()


@router.post("/bulk-create")
def bulk_create(body: BulkCreateRequest, _u=Depends(get_current_user)):
    _ensure_dept_source_columns()
    if not body.confirm:
        return {"status": "error", "message": "Потрібне підтвердження (confirm=true)"}
    if not _has_any_filter(body.filters):
        return {"status": "error", "message": "Звузьте вибірку фільтром або пошуком"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_bulk_where(body.filters)

        # Use _IS_READY_SQL so readiness rules are identical to preview and /staged
        cur.execute(
            f"""SELECT
                    dds.source_id,
                    dds.source_department_id,
                    {_READY_EFF_NAME}  AS eff_name,
                    {_READY_EFF_ORG}   AS eff_org,
                    {_BULK_EFF_PID}    AS eff_parent_id,
                    {_BULK_EFF_PNM}    AS eff_parent_name,
                    {_BULK_EFF_BRN}    AS eff_branch,
                    {_BULK_EFF_REG}    AS eff_region,
                    COALESCE(NULLIF(dds.default_holding_name,''), NULLIF(dds.holding_name,'')) AS eff_holding
                {_BULK_JOIN}
                WHERE {where_sql} AND {_IS_READY_SQL}
                ORDER BY dds.source_department_name""",
            params,
        )
        eligible = cur.fetchall()

        if not eligible:
            conn.commit()
            return {"status": "ok", "created": 0, "bound": 0, "skipped": 0}

        # Within-batch deduplication (multiple sources may share the same dept_id)
        seen_ids:    set = set()
        created = bound = skipped = 0

        for (source_id, source_dept_id, eff_name, eff_org,
             eff_parent_id, eff_parent_name, eff_branch, eff_region, eff_holding) in eligible:
            dept_id = (source_dept_id or "").strip()

            if dept_id in seen_ids:
                skipped += 1
                continue

            name = (eff_name or "").strip()
            org  = (eff_org  or "").strip()
            cur.execute(
                """INSERT INTO dim_department
                       (department_id, department_name, organization_name,
                        branch_name, region_name, holding_name,
                        parent_department_id, parent_department_name, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (
                    dept_id, name, org,
                    (eff_branch      or "").strip() or None,
                    (eff_region      or "").strip() or None,
                    (eff_holding     or "").strip() or None,
                    (eff_parent_id   or "").strip() or None,
                    (eff_parent_name or "").strip() or None,
                ),
            )
            created += 1
            seen_ids.add(dept_id)

            cur.execute(
                """INSERT INTO department_source_mapping
                       (source_id, source_department_id, master_department_id,
                        mapping_status, confidence, mapped_by, updated_at)
                   VALUES (%s, %s, %s, 'mapped', 100, %s, NOW())
                   ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                       master_department_id = EXCLUDED.master_department_id,
                       mapping_status  = 'mapped',
                       confidence      = 100,
                       mapped_by       = EXCLUDED.mapped_by,
                       updated_at      = NOW()
                   WHERE department_source_mapping.mapping_status IN ('pending')
                      OR department_source_mapping.mapping_status IS NULL""",
                (source_id, source_dept_id, dept_id, _u["id"]),
            )
            bound += 1

        conn.commit()
        return {"status": "ok", "created": created, "bound": bound, "skipped": skipped}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


# ── Master-dict resolution ────────────────────────────────────────────────────

def _resolve_one(cur, table: str, id_col: str, name_col: str, name_val: Optional[str]) -> dict:
    """Look up a master-dict entry by name (case-insensitive, active only)."""
    if not name_val or not name_val.strip():
        return {"required": False, "found": False, "id": None, "name": None}
    name_s = name_val.strip()
    cur.execute(
        f"SELECT {id_col}, {name_col} FROM {table}"
        f" WHERE LOWER(TRIM({name_col})) = %s AND is_active = TRUE",
        (name_s.lower(),),
    )
    row = cur.fetchone()
    if row:
        return {"required": True, "found": True, "id": row[0], "name": row[1]}
    return {"required": True, "found": False, "id": None, "name": name_s}


@router.post("/resolve-context")
def resolve_context(body: ResolveContextRequest, _u=Depends(get_current_user)):
    """Resolve holding / org / region / branch names to master-dict IDs."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        holding      = _resolve_one(cur, "dim_holding",      "holding_id",      "holding_name",      body.holding_name)
        organization = _resolve_one(cur, "dim_organization",  "organization_id",  "organization_name",  body.organization_name)
        region       = _resolve_one(cur, "dim_region",        "region_id",        "region_name",        body.region_name)
        branch       = _resolve_one(cur, "dim_branch",        "branch_id",        "branch_name",        body.branch_name)
        all_resolved = (
            (not holding["required"]      or holding["found"]) and
            (not organization["required"] or organization["found"]) and
            (not region["required"]       or region["found"]) and
            (not branch["required"]       or branch["found"])
        )
        return {
            "holding":      holding,
            "organization": organization,
            "region":       region,
            "branch":       branch,
            "all_resolved": all_resolved,
        }
    finally:
        cur.close()
        conn.close()


@router.post("/create-standalone-dept")
def create_standalone_dept(body: CreateStandaloneRequest, _u=Depends(get_current_user)):
    """Create a dim_department entry directly without requiring a source staging row."""
    dept_id_s   = (body.department_id    or "").strip()
    name_s      = (body.department_name  or "").strip()
    org_s       = (body.organization_name or "").strip()
    parent_id_s = (body.parent_department_id   or "").strip() or None
    parent_nm_s = (body.parent_department_name or "").strip() or None
    branch_s    = (body.branch_name  or "").strip() or None
    region_s    = (body.region_name  or "").strip() or None
    holding_s   = (body.holding_name or "").strip() or None

    if not dept_id_s:
        raise HTTPException(400, "department_id обов'язковий")
    if not name_s:
        raise HTTPException(400, "Назва підрозділу обов'язкова")
    if not org_s:
        raise HTTPException(400, "Організація обов'язкова")

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM dim_department WHERE department_id = %s", (dept_id_s,))
        if cur.fetchone():
            raise HTTPException(409, f"department_id «{dept_id_s}» вже існує в dim_department")

        if parent_id_s:
            cur.execute(
                "SELECT 1 FROM dim_department WHERE department_id = %s AND is_active = TRUE",
                (parent_id_s,),
            )
            if not cur.fetchone():
                raise HTTPException(409, f"Батьківський підрозділ «{parent_id_s}» не знайдено")

        cur.execute(
            """INSERT INTO dim_department
                   (department_id, department_name, organization_name,
                    branch_name, region_name, holding_name,
                    parent_department_id, parent_department_name, is_active,
                    holding_id, organization_id, region_id, branch_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s)""",
            (dept_id_s, name_s, org_s, branch_s, region_s, holding_s, parent_id_s, parent_nm_s,
             body.holding_id, body.organization_id, body.region_id, body.branch_id),
        )

        parent_bound = False
        if body.auto_bind_source_id and body.auto_bind_source_department_id:
            ab_src_id   = body.auto_bind_source_id
            ab_src_dept = body.auto_bind_source_department_id.strip()
            cur.execute(
                "SELECT 1 FROM dim_department_source WHERE source_id = %s AND source_department_id = %s",
                (ab_src_id, ab_src_dept),
            )
            if cur.fetchone():
                cur.execute(
                    """INSERT INTO department_source_mapping
                           (source_id, source_department_id, master_department_id,
                            mapping_status, confidence, mapped_by, updated_at)
                       VALUES (%s, %s, %s, 'mapped', 100, 'auto_parent', NOW())
                       ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                           master_department_id = EXCLUDED.master_department_id,
                           mapping_status  = 'mapped',
                           confidence      = 100,
                           mapped_by       = 'auto_parent',
                           updated_at      = NOW()""",
                    (ab_src_id, ab_src_dept, dept_id_s),
                )
                parent_bound = True

        conn.commit()
        return {"ok": True, "department_id": dept_id_s, "department_name": name_s,
                "parent_bound": parent_bound}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()


_DICT_TABLE = {
    "holding":      ("dim_holding",      "holding_id",      "holding_name"),
    "organization": ("dim_organization",  "organization_id",  "organization_name"),
    "region":       ("dim_region",        "region_id",        "region_name"),
    "branch":       ("dim_branch",        "branch_id",        "branch_name"),
}


@router.get("/dict-entries")
def get_dict_entries(
    dict_type: str,
    search:    Optional[str] = None,
    _u=Depends(get_current_user),
):
    """Return active records from a master dictionary (holding / org / region / branch / department)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        if dict_type == "department":
            if search and search.strip():
                cur.execute(
                    "SELECT department_id, department_name, is_active FROM dim_department"
                    " WHERE COALESCE(is_deleted, FALSE) = FALSE"
                    " AND LOWER(department_name) LIKE %s"
                    " ORDER BY department_name LIMIT 200",
                    (f"%{search.strip().lower()}%",),
                )
            else:
                cur.execute(
                    "SELECT department_id, department_name, is_active FROM dim_department"
                    " WHERE COALESCE(is_deleted, FALSE) = FALSE"
                    " ORDER BY department_name LIMIT 200"
                )
        else:
            if dict_type not in _DICT_TABLE:
                raise HTTPException(400, f"Невідомий тип: {dict_type}")
            table, id_col, name_col = _DICT_TABLE[dict_type]
            if search and search.strip():
                cur.execute(
                    f"SELECT {id_col}, {name_col}, is_active FROM {table}"
                    f" WHERE is_active = TRUE AND LOWER({name_col}) LIKE %s"
                    f" ORDER BY {name_col} LIMIT 200",
                    (f"%{search.strip().lower()}%",),
                )
            else:
                cur.execute(
                    f"SELECT {id_col}, {name_col}, is_active FROM {table}"
                    f" WHERE is_active = TRUE ORDER BY {name_col} LIMIT 200"
                )
        rows = cur.fetchall()
        return [{"id": r[0], "name": r[1], "is_active": r[2]} for r in rows]
    finally:
        cur.close()
        conn.close()


@router.post("/create-dict-entry")
def create_dict_entry(body: CreateDictEntryRequest, _u=Depends(get_current_user)):
    """Create a missing master-dict record (holding / org / region / branch)."""
    name_s = (body.name or "").strip()
    if not name_s:
        raise HTTPException(400, "Назва обов'язкова")

    if body.entry_type not in _DICT_TABLE:
        raise HTTPException(400, f"Невідомий тип: {body.entry_type}")

    table, id_col, name_col = _DICT_TABLE[body.entry_type]
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {table} ({name_col}, is_active) VALUES (%s, TRUE)"
            f" RETURNING {id_col}, {name_col}",
            (name_s,),
        )
        r = cur.fetchone()
        conn.commit()
        return {"id": r[0], "name": r[1], "entry_type": body.entry_type}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close()
        conn.close()
