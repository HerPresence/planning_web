"""
Planning engine test suite — no DB required.
Covers: overlapping priorities, period restriction, multi-scope AND,
        volume effect, fallback when kg=0, validation.

Run: python test_planning.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date
from services.planning_calc import (
    calc_plan_kg, calc_plan_dal,
    calc_fact_price_per_kg, calc_plan_price_per_kg,
    calc_plan_sales_vat,
    select_winning_rule,
    scope_matches_row, rule_matches_row,
    is_rule_active_for_month,
    validate_rule_params,
)

PASS = "\033[32mPASSED\033[0m"
FAIL = "\033[31mFAILED\033[0m"
_failures = []

def _check(name, condition, msg=""):
    if condition:
        print(f"  {PASS}  {name}")
    else:
        print(f"  {FAIL}  {name}{(' — ' + msg) if msg else ''}")
        _failures.append(name)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Overlapping priorities
# ─────────────────────────────────────────────────────────────────────────────
def test_overlapping_priorities():
    print("Test 1 — Overlapping priorities")
    # Rule A: region=Захід, price +5%, priority=10
    # Rule B: department=X, price +12%, priority=100
    # Both match the same row. Rule B (higher priority) should win.
    rule_a = {"rule_id": 1, "rule_type": "price_effect_pct", "effect_percent":  5.0, "priority": 10,  "scope_cnt": 1}
    rule_b = {"rule_id": 2, "rule_type": "price_effect_pct", "effect_percent": 12.0, "priority": 100, "scope_cnt": 1}

    winner = select_winning_rule([rule_a, rule_b])
    _check("rule_b wins by priority",        winner["rule_id"] == 2)
    _check("winning effect is +12%",          winner["effect_percent"] == 12.0)

    # Equal priority: more scopes (more specific) wins
    rule_c = {"rule_id": 3, "effect_percent": 7.0, "priority": 100, "scope_cnt": 2}
    rule_d = {"rule_id": 4, "effect_percent": 9.0, "priority": 100, "scope_cnt": 1}
    winner2 = select_winning_rule([rule_c, rule_d])
    _check("ties broken by scope_cnt DESC",   winner2["rule_id"] == 3)

    # Identical rules → deterministic (any stable max)
    rule_e = {"rule_id": 5, "effect_percent": 3.0, "priority": 50, "scope_cnt": 0}
    winner3 = select_winning_rule([rule_e])
    _check("single rule always wins",         winner3["rule_id"] == 5)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Period restriction
# ─────────────────────────────────────────────────────────────────────────────
def test_period_restriction():
    print("\nTest 2 — Period restriction")
    pf = date(2026, 3, 1)
    pt = date(2026, 5, 31)

    _check("March  → active",    is_rule_active_for_month(date(2026, 3, 1),  pf, pt))
    _check("May    → active",    is_rule_active_for_month(date(2026, 5, 1),  pf, pt))
    _check("Jan    → inactive",  not is_rule_active_for_month(date(2026, 1, 1), pf, pt))
    _check("June   → inactive",  not is_rule_active_for_month(date(2026, 6, 1), pf, pt))
    _check("no period = always", is_rule_active_for_month(date(2025, 1, 1), None, None))
    _check("open end",           is_rule_active_for_month(date(2030, 1, 1), pf, None))


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Multi-scope AND logic
# ─────────────────────────────────────────────────────────────────────────────
def test_multi_scope_and():
    print("\nTest 3 — Multi-scope AND logic")
    row_match    = {"region_name": "Захід", "brand_name": "Оболонь", "department_name": "Sales"}
    row_brand_no = {"region_name": "Захід", "brand_name": "Чернігівське"}
    row_region_no= {"region_name": "Київ",  "brand_name": "Оболонь"}

    scopes = [
        {"dimension_type": "region", "dimension_value": "Захід"},
        {"dimension_type": "brand",  "dimension_value": "Оболонь"},
    ]

    _check("Захід+Оболонь → match",          rule_matches_row(row_match, scopes))
    _check("Захід+Чернігівське → no match",  not rule_matches_row(row_brand_no, scopes))
    _check("Київ+Оболонь → no match",        not rule_matches_row(row_region_no, scopes))
    _check("empty scopes = all rows",         rule_matches_row(row_match, []))
    _check("all scope type → always",         scope_matches_row(row_match, {"dimension_type": "all"}))

    # Three-scope AND
    scopes3 = scopes + [{"dimension_type": "department", "dimension_value": "Sales"}]
    _check("3-scope full match",              rule_matches_row(row_match, scopes3))
    row_dept_no = {"region_name": "Захід", "brand_name": "Оболонь", "department_name": "Marketing"}
    _check("3-scope dept mismatch → no match", not rule_matches_row(row_dept_no, scopes3))


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Volume effect
# ─────────────────────────────────────────────────────────────────────────────
def test_volume_effect():
    print("\nTest 4 — Volume effect")
    fact_kg = 1000.0; fact_dal = 1200.0; fact_vat = 50_000.0
    vol_pct = 10.0; price_pct = 0.0; rev_pct = 0.0

    plan_kg  = calc_plan_kg(fact_kg, vol_pct)
    plan_dal = calc_plan_dal(fact_dal, vol_pct)
    ppkg     = calc_fact_price_per_kg(fact_vat, fact_kg)
    plan_ppkg = calc_plan_price_per_kg(ppkg, price_pct)
    plan_vat  = calc_plan_sales_vat(fact_vat, fact_kg, vol_pct, price_pct, rev_pct)

    _check("plan_kg  = fact_kg  × 1.10",      abs(plan_kg  - 1100.0) < 0.001)
    _check("plan_dal = fact_dal × 1.10",      abs(plan_dal - 1320.0) < 0.001)
    _check("fact_price_per_kg = 50.0",        abs(ppkg - 50.0) < 0.001)
    _check("plan_price_per_kg unchanged",      abs(plan_ppkg - 50.0) < 0.001)
    # plan_vat = fact_vat * vol (no price effect)
    _check("plan_vat = 50000 × 1.10 = 55000", abs(plan_vat - 55_000.0) < 0.01)

    # Combined vol + price
    plan_vat2 = calc_plan_sales_vat(fact_vat, fact_kg, 10.0, 5.0, 0.0)
    expected2 = fact_vat * 1.10 * 1.05  # 57750
    _check("vol+price combined",              abs(plan_vat2 - expected2) < 0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Fallback when kg=0
# ─────────────────────────────────────────────────────────────────────────────
def test_fallback_when_kg_zero():
    print("\nTest 5 — Fallback when kg=0")
    fact_vat = 5_000.0; fact_kg = 0.0
    rev_pct = 15.0; vol_pct = 20.0; price_pct = 10.0

    plan_vat = calc_plan_sales_vat(fact_vat, fact_kg, vol_pct, price_pct, rev_pct)
    expected = fact_vat * (1 + rev_pct / 100)   # 5750.0

    _check("plan_vat = fact_vat × rev only",   abs(plan_vat - expected) < 0.01)
    # Must NOT include volume or price
    wrong = fact_vat * (1 + rev_pct/100) * (1 + vol_pct/100) * (1 + price_pct/100)
    _check("vol+price NOT duplicated",          abs(plan_vat - wrong) > 1.0)
    _check("fact_price_per_kg = None",          calc_fact_price_per_kg(fact_vat, fact_kg) is None)
    _check("plan_price_per_kg = None",          calc_plan_price_per_kg(None, price_pct) is None)

    # revenue_effect = 0 → plan equals fact (no change)
    plan_zero = calc_plan_sales_vat(fact_vat, fact_kg, vol_pct, price_pct, 0.0)
    _check("rev=0 → plan_vat = fact_vat",      abs(plan_zero - fact_vat) < 0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Validation
# ─────────────────────────────────────────────────────────────────────────────
def test_validation():
    print("\nTest 6 — Validation")
    _check("period_from > period_to → error",
           len(validate_rule_params(period_from=date(2026, 6, 1), period_to=date(2026, 3, 1))) > 0)
    _check("valid period → no error",
           len(validate_rule_params(period_from=date(2026, 1, 1), period_to=date(2026, 12, 31))) == 0)
    _check("effect > 1000 → error",
           len(validate_rule_params(effect_percent=1001.0)) > 0)
    _check("effect < -100 → error",
           len(validate_rule_params(effect_percent=-101.0)) > 0)
    _check("effect = 1000 → valid",
           len(validate_rule_params(effect_percent=1000.0)) == 0)
    _check("invalid rule_type → error",
           len(validate_rule_params(rule_type="bad_type")) > 0)
    _check("valid rule_type → no error",
           len(validate_rule_params(rule_type="revenue_effect_pct")) == 0)
    _check("priority < 0 → error",
           len(validate_rule_params(priority=-1)) > 0)
    _check("priority = 0 → valid",
           len(validate_rule_params(priority=0)) == 0)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_overlapping_priorities()
    test_period_restriction()
    test_multi_scope_and()
    test_volume_effect()
    test_fallback_when_kg_zero()
    test_validation()

    print()
    if _failures:
        print(f"\033[31mFAILED: {len(_failures)} test(s): {', '.join(_failures)}\033[0m")
        sys.exit(1)
    else:
        total = 5 + 6 + 7 + 6 + 5 + 9  # per-test checks
        print(f"\033[32mAll tests PASSED.\033[0m")
