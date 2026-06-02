"""
Pure calculation functions for the planning engine.
No DB dependency — safe to import in tests.
Mirrors the SQL logic in planning_engine.py.
"""
from datetime import date
from typing import Optional


def calc_plan_kg(fact_kg: float, vol_pct: float) -> float:
    return fact_kg * (1 + vol_pct / 100)


def calc_plan_dal(fact_dal: float, vol_pct: float) -> float:
    return fact_dal * (1 + vol_pct / 100)


def calc_fact_price_per_kg(fact_vat: float, fact_kg: float) -> Optional[float]:
    return fact_vat / fact_kg if fact_kg > 0 else None


def calc_plan_price_per_kg(fact_price_per_kg: Optional[float], price_pct: float) -> Optional[float]:
    return fact_price_per_kg * (1 + price_pct / 100) if fact_price_per_kg is not None else None


def calc_plan_sales_vat(fact_vat: float, fact_kg: float,
                        vol_pct: float, price_pct: float, rev_pct: float) -> float:
    """
    Calculation model:
      kg > 0:  plan_vat = fact_vat * vol_mult * price_mult
               (revenue_effect does NOT apply — price × vol model is sufficient)
      kg = 0:  plan_vat = fact_vat * rev_mult   (revenue-only fallback)
    """
    if fact_kg > 0:
        return fact_vat * (1 + vol_pct / 100) * (1 + price_pct / 100)
    return fact_vat * (1 + rev_pct / 100)


def select_winning_rule(rules_for_type: list) -> dict:
    """
    Priority logic: highest priority wins for the same effect type.
    Ties broken by scope_cnt DESC (more specific rule wins).
    Returns {} if list is empty.
    """
    if not rules_for_type:
        return {}
    return max(rules_for_type, key=lambda r: (r.get("priority", 0), r.get("scope_cnt", 0)))


def scope_matches_row(row: dict, scope: dict) -> bool:
    """Returns True if a single scope condition matches the row dict."""
    dim_type = scope.get("dimension_type", "all")
    if dim_type == "all":
        return True
    field_map = {
        "holding":           "holding_name",
        "organization":      "organization_name",
        "region":            "region_name",
        "branch":            "branch_name",
        "parent_department": "parent_dept_name",
        "department":        "department_name",
        "department_uid":    "department_uid",
        "product_group":     "product_group_name",
        "product_group_uid": "product_group_uid",
        "brand":             "brand_name",
        "brand_uid":         "brand_uid",
    }
    field = field_map.get(dim_type)
    if not field:
        # source_id — compare as string
        return str(row.get("source_id", "")) == scope.get("dimension_value", "")
    return (row.get(field) or "") == scope.get("dimension_value", "")


def rule_matches_row(row: dict, scopes: list) -> bool:
    """AND logic: ALL scope conditions must match."""
    if not scopes:
        return True  # no scopes = match all rows
    return all(scope_matches_row(row, s) for s in scopes)


def is_rule_active_for_month(target_month: date, period_from=None, period_to=None) -> bool:
    """Returns True if a rule's period covers target_month."""
    if period_from is not None and target_month < period_from:
        return False
    if period_to is not None and target_month > period_to:
        return False
    return True


def validate_rule_params(rule_type=None, effect_percent=None, priority=None,
                         period_from=None, period_to=None) -> list:
    """Returns a list of error strings; empty list = valid."""
    errors = []
    valid_types = {"revenue_effect_pct", "volume_effect_pct", "price_effect_pct"}
    if rule_type is not None and rule_type not in valid_types:
        errors.append(f"rule_type must be one of: {sorted(valid_types)}")
    if effect_percent is not None:
        ep = float(effect_percent)
        if ep < -100 or ep > 1000:
            errors.append(f"effect_percent must be between -100 and 1000 (got {ep})")
    if priority is not None and int(priority) < 0:
        errors.append(f"priority must be >= 0 (got {priority})")
    if period_from is not None and period_to is not None and period_from > period_to:
        errors.append(f"period_from ({period_from}) must be <= period_to ({period_to})")
    return errors
