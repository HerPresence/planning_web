"""
Planning router — Phase 3 (multi-effect rules).
Prefix: /api/planning
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import get_current_user
from services.planning_engine import (
    copy_rule,
    create_effect, create_rule,
    delete_effect, delete_rule,
    generate_first_draft,
    get_dept_mapping_coverage,
    get_planning_readiness,
    get_plans_overview,
    delete_version,
    get_dim_options,
    get_fact_plan, get_fact_plan_aggregated,
    get_generation_log, get_generation_status,
    get_plan_dept_options, get_plan_pg_options,
    get_rules, get_scenario, get_scenarios,
    get_turnover_dept_options, get_turnover_pg_options,
    get_version, get_versions,
    lock_version, update_effect, update_rule, update_scenario,
    create_scenario, create_version,
)

router = APIRouter(prefix="/api/planning")


# ── Request / response models ─────────────────────────────────────────────────

class CreateScenarioBody(BaseModel):
    scenario_code: str
    scenario_name: str
    scenario_type: str = "draft"
    description:   Optional[str] = None

class UpdateScenarioBody(BaseModel):
    scenario_name: Optional[str] = None
    description:   Optional[str] = None
    is_active:     Optional[bool] = None

class CreateVersionBody(BaseModel):
    version_name: str
    description:  Optional[str] = None

class ScopeItem(BaseModel):
    dimension_type:  str
    dimension_value: str = ""
    dimension_label: Optional[str] = None

class EffectItem(BaseModel):
    period_from:    Optional[date] = None
    period_to:      Optional[date] = None
    rule_type:      str
    effect_percent: float = Field(default=0.0, ge=-100.0, le=1000.0)
    priority:       int   = Field(default=100, ge=0, le=9999)
    is_active:      bool  = True

class CreateRuleBody(BaseModel):
    rule_name: str
    scopes:    List[ScopeItem] = []
    effects:   List[EffectItem] = []

class UpdateRuleBody(BaseModel):
    rule_name: Optional[str]  = None
    is_active: Optional[bool] = None
    scopes:    Optional[List[ScopeItem]] = None

class CreateEffectBody(BaseModel):
    period_from:    Optional[date] = None
    period_to:      Optional[date] = None
    rule_type:      str
    effect_percent: float = Field(default=0.0, ge=-100.0, le=1000.0)
    priority:       int   = Field(default=100, ge=0, le=9999)
    is_active:      bool  = True

class UpdateEffectBody(BaseModel):
    period_from:    Optional[date]  = None
    period_to:      Optional[date]  = None
    clear_period:   bool            = False
    rule_type:      Optional[str]   = None
    effect_percent: Optional[float] = Field(default=None, ge=-100.0, le=1000.0)
    priority:       Optional[int]   = Field(default=None, ge=0, le=9999)
    is_active:      Optional[bool]  = None

class GenerateFirstDraftBody(BaseModel):
    scenario_id:        int
    version_id:         int
    base_period_from:   date
    base_period_to:     date
    target_period_from: date
    target_period_to:   date
    global_revenue_pct: float = Field(default=0.0, ge=-100.0, le=1000.0)
    global_volume_pct:  float = Field(default=0.0, ge=-100.0, le=1000.0)
    global_price_pct:   float = Field(default=0.0, ge=-100.0, le=1000.0)
    department_uids:    Optional[List[str]] = None
    product_group_uids: Optional[List[str]] = None
    source_ids:         Optional[List[int]] = None
    replace_existing:   bool = True


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/scenarios")
def list_scenarios(page=1, page_size=50, is_active: Optional[bool] = None, scenario_type: Optional[str] = None, _u=Depends(get_current_user)):
    return get_scenarios(page=page, page_size=page_size, is_active=is_active, scenario_type=scenario_type)

@router.post("/scenarios", status_code=201)
def create_scenario_endpoint(body: CreateScenarioBody, _u=Depends(get_current_user)):
    try:
        return create_scenario(scenario_code=body.scenario_code.strip(), scenario_name=body.scenario_name.strip(), scenario_type=body.scenario_type, description=body.description, created_by=_u["id"])
    except Exception as exc:
        raise HTTPException(400, str(exc))

@router.get("/scenarios/{scenario_id}")
def get_scenario_endpoint(scenario_id: int, _u=Depends(get_current_user)):
    s = get_scenario(scenario_id)
    if not s: raise HTTPException(404, f"Scenario {scenario_id} not found")
    return s

@router.patch("/scenarios/{scenario_id}")
def update_scenario_endpoint(scenario_id: int, body: UpdateScenarioBody, _u=Depends(get_current_user)):
    s = update_scenario(scenario_id=scenario_id, scenario_name=body.scenario_name, description=body.description, is_active=body.is_active)
    if not s: raise HTTPException(404, f"Scenario {scenario_id} not found")
    return s


# ══════════════════════════════════════════════════════════════════════════════
# VERSIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/scenarios/{scenario_id}/versions")
def list_versions(scenario_id: int, page=1, page_size=50, _u=Depends(get_current_user)):
    if not get_scenario(scenario_id): raise HTTPException(404, f"Scenario {scenario_id} not found")
    return get_versions(scenario_id=scenario_id, page=page, page_size=page_size)

@router.post("/scenarios/{scenario_id}/versions", status_code=201)
def create_version_endpoint(scenario_id: int, body: CreateVersionBody, _u=Depends(get_current_user)):
    if not get_scenario(scenario_id): raise HTTPException(404, f"Scenario {scenario_id} not found")
    try:
        return create_version(scenario_id=scenario_id, version_name=body.version_name.strip(), description=body.description, created_by=_u["id"])
    except Exception as exc:
        raise HTTPException(400, str(exc))

@router.get("/versions/{version_id}")
def get_version_endpoint(version_id: int, _u=Depends(get_current_user)):
    v = get_version(version_id)
    if not v: raise HTTPException(404, f"Version {version_id} not found")
    return v

@router.post("/versions/{version_id}/lock")
def lock_version_endpoint(version_id: int, _u=Depends(get_current_user)):
    try:
        return lock_version(version_id=version_id, locked_by=_u["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PLANNING RULES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/rules")
def list_rules(scenario_id: int, version_id: int, is_active: Optional[bool] = None, _u=Depends(get_current_user)):
    return get_rules(scenario_id=scenario_id, version_id=version_id, is_active=is_active)

@router.post("/rules", status_code=201)
def create_rule_endpoint(scenario_id: int, version_id: int, body: CreateRuleBody, _u=Depends(get_current_user)):
    try:
        return create_rule(
            scenario_id=scenario_id, version_id=version_id,
            rule_name=body.rule_name.strip(),
            scopes=[s.dict() for s in body.scopes],
            effects=[e.dict() for e in body.effects],
            created_by=_u["id"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.patch("/rules/{rule_id}")
def update_rule_endpoint(rule_id: int, body: UpdateRuleBody, _u=Depends(get_current_user)):
    try:
        r = update_rule(
            rule_id=rule_id,
            rule_name=body.rule_name,
            is_active=body.is_active,
            scopes=[s.dict() for s in body.scopes] if body.scopes is not None else None,
        )
        if not r: raise HTTPException(404, f"Rule {rule_id} not found")
        return r
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule_endpoint(rule_id: int, _u=Depends(get_current_user)):
    if not delete_rule(rule_id): raise HTTPException(404, f"Rule {rule_id} not found")

@router.post("/rules/{rule_id}/copy", status_code=201)
def copy_rule_endpoint(rule_id: int, _u=Depends(get_current_user)):
    try:
        return copy_rule(rule_id=rule_id, created_by=_u["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# RULE EFFECTS CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/rules/{rule_id}/effects", status_code=201)
def create_effect_endpoint(rule_id: int, body: CreateEffectBody, _u=Depends(get_current_user)):
    try:
        return create_effect(
            rule_id=rule_id,
            rule_type=body.rule_type,
            effect_percent=body.effect_percent,
            period_from=body.period_from,
            period_to=body.period_to,
            priority=body.priority,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.patch("/effects/{effect_id}")
def update_effect_endpoint(effect_id: int, body: UpdateEffectBody, _u=Depends(get_current_user)):
    try:
        r = update_effect(
            effect_id=effect_id,
            rule_type=body.rule_type,
            effect_percent=body.effect_percent,
            period_from=body.period_from,
            period_to=body.period_to,
            clear_period=body.clear_period,
            priority=body.priority,
            is_active=body.is_active,
        )
        if not r: raise HTTPException(404, f"Effect {effect_id} not found")
        return r
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.delete("/effects/{effect_id}", status_code=204)
def delete_effect_endpoint(effect_id: int, _u=Depends(get_current_user)):
    if not delete_effect(effect_id): raise HTTPException(404, f"Effect {effect_id} not found")


# ══════════════════════════════════════════════════════════════════════════════
# DIMENSION OPTIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dim-options/{dim_type}")
def dim_options_endpoint(dim_type: str, search: str = "", limit: int = 50, _u=Depends(get_current_user)):
    return get_dim_options(dim_type=dim_type, search=search, limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# FACT PLAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/fact-plan")
def list_fact_plan(
    scenario_id: Optional[int]=None, version_id: Optional[int]=None,
    period_from: Optional[str]=None, period_to: Optional[str]=None,
    region: Optional[str]=None, branch: Optional[str]=None,
    holding: Optional[str]=None, organization: Optional[str]=None,
    department_name: Optional[str]=None, department_uid: Optional[str]=None,
    product_group_name: Optional[str]=None, product_group_uid: Optional[str]=None,
    brand_name: Optional[str]=None,
    page: int=1, page_size: int=100, _u=Depends(get_current_user),
):
    return get_fact_plan(
        scenario_id=scenario_id, version_id=version_id,
        period_from=period_from, period_to=period_to,
        region=region, branch=branch, holding=holding, organization=organization,
        department_name=department_name, department_uid=department_uid,
        product_group_name=product_group_name, product_group_uid=product_group_uid,
        brand_name=brand_name, page=page, page_size=page_size,
    )

@router.get("/fact-plan/aggregated")
def aggregated_plan(scenario_id: int, version_id: int, group_by: str="month", period_from: Optional[str]=None, period_to: Optional[str]=None, _u=Depends(get_current_user)):
    if group_by not in ("month", "department", "product_group"):
        raise HTTPException(400, "group_by must be: month | department | product_group")
    return get_fact_plan_aggregated(scenario_id=scenario_id, version_id=version_id, group_by=group_by, period_from=period_from, period_to=period_to)

@router.get("/fact-plan/filter-options/departments")
def plan_dept_options(scenario_id: int, version_id: int, search: str="", limit: int=50, _u=Depends(get_current_user)):
    return get_plan_dept_options(scenario_id=scenario_id, version_id=version_id, search=search, limit=limit)

@router.get("/fact-plan/filter-options/product-groups")
def plan_pg_options(scenario_id: int, version_id: int, search: str="", limit: int=50, _u=Depends(get_current_user)):
    return get_plan_pg_options(scenario_id=scenario_id, version_id=version_id, search=search, limit=limit)

@router.get("/fact-plan/filter-options/turnover-departments")
def turnover_dept_options(search: str="", period_from: Optional[str]=None, period_to: Optional[str]=None, limit: int=50, _u=Depends(get_current_user)):
    return get_turnover_dept_options(search=search, period_from=period_from, period_to=period_to, limit=limit)

@router.get("/fact-plan/filter-options/turnover-product-groups")
def turnover_pg_options(search: str="", period_from: Optional[str]=None, period_to: Optional[str]=None, limit: int=50, _u=Depends(get_current_user)):
    return get_turnover_pg_options(search=search, period_from=period_from, period_to=period_to, limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# GENERATION
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/generate-first-draft")
def generate_endpoint(body: GenerateFirstDraftBody, _u=Depends(get_current_user)):
    try:
        return generate_first_draft(
            scenario_id=body.scenario_id, version_id=body.version_id,
            base_period_from=body.base_period_from, base_period_to=body.base_period_to,
            target_period_from=body.target_period_from, target_period_to=body.target_period_to,
            global_revenue_pct=body.global_revenue_pct,
            global_volume_pct=body.global_volume_pct,
            global_price_pct=body.global_price_pct,
            department_uids=body.department_uids,
            product_group_uids=body.product_group_uids,
            source_ids=body.source_ids,
            replace_existing=body.replace_existing,
            created_by=_u["id"],
            created_by_name=_u.get("name") or _u.get("email") or str(_u["id"]),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.get("/status/{generation_id}")
def generation_status_endpoint(generation_id: int, _u=Depends(get_current_user)):
    s = get_generation_status(generation_id)
    if not s: raise HTTPException(404, f"Generation log {generation_id} not found")
    return s

@router.get("/generation-log")
def list_generation_log_endpoint(scenario_id: Optional[int]=None, version_id: Optional[int]=None, page: int=1, page_size: int=20, _u=Depends(get_current_user)):
    return get_generation_log(scenario_id=scenario_id, version_id=version_id, page=page, page_size=page_size)


# ══════════════════════════════════════════════════════════════════════════════
# DEPARTMENT MAPPING COVERAGE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/department-mapping-coverage")
def dept_mapping_coverage_endpoint(
    period_from: Optional[str] = None,
    period_to:   Optional[str] = None,
    source_id:   Optional[int] = None,
    _u=Depends(get_current_user),
):
    return get_dept_mapping_coverage(period_from=period_from, period_to=period_to, source_id=source_id)


@router.get("/planning-readiness")
def planning_readiness_endpoint(
    period_from: Optional[str] = None,
    period_to:   Optional[str] = None,
    source_id:   Optional[int] = None,
    _u=Depends(get_current_user),
):
    return get_planning_readiness(period_from=period_from, period_to=period_to, source_id=source_id)


@router.get("/plans-overview")
def plans_overview_endpoint(_u=Depends(get_current_user)):
    return get_plans_overview()


# ── Quick Map Department (inline from Planning) ───────────────────────────────

class QuickMapDeptBody(BaseModel):
    source_id:              int
    source_department_id:   str
    source_department_name: Optional[str] = None
    action:                 str           # attach_existing | create_new
    master_department_id:   Optional[str] = None
    create_payload:         Optional[dict] = None  # {department_name, organization_name, branch, region, holding}


@router.post("/quick-map-department")
def quick_map_department(body: QuickMapDeptBody, _u=Depends(get_current_user)):
    """
    Inline mapping from Planning page.
    attach_existing → upsert department_source_mapping.
    create_new      → insert dim_department, then upsert mapping.
    """
    from db import get_connection
    conn = get_connection(); cur = conn.cursor()
    try:
        master_id = None

        if body.action == "attach_existing":
            if not body.master_department_id:
                raise HTTPException(400, "master_department_id є обов'язковим для attach_existing")
            cur.execute("SELECT 1 FROM dim_department WHERE department_id=%s AND is_active=TRUE",
                        (body.master_department_id,))
            if not cur.fetchone():
                raise HTTPException(404, f"Master department '{body.master_department_id}' не знайдено")
            master_id = body.master_department_id

        elif body.action == "create_new":
            if not body.create_payload:
                raise HTTPException(400, "create_payload є обов'язковим для create_new")
            cp = body.create_payload
            dept_id   = (cp.get("department_id")    or body.source_department_id or "").strip()
            dept_name = (cp.get("department_name")  or "").strip()
            org_name  = (cp.get("organization_name") or "").strip()
            if not dept_id:   raise HTTPException(400, "department_id обов'язковий")
            if not dept_name: raise HTTPException(400, "department_name обов'язковий")
            if not org_name:  raise HTTPException(400, "organization_name обов'язковий")

            cur.execute("SELECT 1 FROM dim_department WHERE department_id=%s", (dept_id,))
            if cur.fetchone():
                raise HTTPException(409, f"department_id '{dept_id}' вже існує в dim_department")

            # Soft duplicate check: same name+org+branch combo
            cur.execute(
                """SELECT department_id, department_name, organization_name
                   FROM dim_department
                   WHERE LOWER(TRIM(department_name)) = %s
                     AND LOWER(TRIM(organization_name)) = %s
                     AND COALESCE(LOWER(TRIM(branch_name)),'') = %s
                     AND COALESCE(is_deleted, FALSE) = FALSE""",
                (dept_name.lower(), org_name.lower(),
                 (cp.get("branch_name") or "").strip().lower()),
            )
            dupes = [{"department_id": r[0], "department_name": r[1], "organization_name": r[2]}
                     for r in cur.fetchall()]
            if dupes and not cp.get("force_create"):
                return {"success": False, "possible_duplicates": dupes,
                        "message": f"Знайдено {len(dupes)} схожих підрозділів. Вкажіть force_create=true щоб все одно створити."}

            cur.execute(
                """INSERT INTO dim_department
                       (department_id, department_name, organization_name,
                        branch_name, region_name, holding_name,
                        parent_department_id, parent_department_name, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE)""",
                (dept_id, dept_name, org_name,
                 (cp.get("branch_name")  or "").strip() or None,
                 (cp.get("region_name")  or "").strip() or None,
                 (cp.get("holding_name") or "").strip() or None,
                 (cp.get("parent_department_id")   or "").strip() or None,
                 (cp.get("parent_department_name") or "").strip() or None),
            )
            master_id = dept_id
        else:
            raise HTTPException(400, f"Невідома дія: {body.action}")

        # Upsert the mapping record
        cur.execute(
            """INSERT INTO department_source_mapping
                   (source_id, source_department_id, master_department_id,
                    mapping_status, confidence, mapped_by, updated_at, mapping_method)
               VALUES (%s, %s, %s, 'mapped', 100, %s, NOW(), 'quick_map')
               ON CONFLICT (source_id, source_department_id) DO UPDATE SET
                   master_department_id = EXCLUDED.master_department_id,
                   mapping_status  = 'mapped',
                   confidence      = 100,
                   mapped_by       = EXCLUDED.mapped_by,
                   mapping_method  = 'quick_map',
                   updated_at      = NOW()""",
            (body.source_id, body.source_department_id, master_id, _u["id"]),
        )
        conn.commit()
        return {"success": True, "mapping_created": True, "master_department_id": master_id}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        cur.close(); conn.close()


@router.delete("/versions/{version_id}", status_code=204)
def delete_version_endpoint(version_id: int, _u=Depends(get_current_user)):
    try:
        if not delete_version(version_id):
            raise HTTPException(404, f"Version {version_id} not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
