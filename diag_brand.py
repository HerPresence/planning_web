"""
Diagnostic script - run from D:\Metricore\planning_web:
  python diag_brand.py
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== Calling get_brand_mapping_coverage() directly ===")
try:
    from services.planning_engine import get_brand_mapping_coverage
    result = get_brand_mapping_coverage()
    print(f"SUCCESS! Keys: {list(result.keys())}")
    print(f"  total_fact_rows={result['total_fact_rows']}")
    print(f"  unique_fact_brands={result['unique_fact_brands']}")
    print(f"  mapped_brands={result['mapped_brands']}")
    print(f"  unmapped_brands={result['unmapped_brands']}")
    print(f"  coverage_pct={result['coverage_pct']}")
    print(f"  unmapped_brands_list count={len(result['unmapped_brands_list'])}")
    if result['unmapped_brands_list']:
        print(f"  first unmapped: {result['unmapped_brands_list'][0]}")
    print("\n=== FUNCTION OK ===")
except Exception:
    print("\n=== FUNCTION FAILED ===")
    traceback.print_exc()

print("\n=== Testing JSON serialization ===")
try:
    import json
    result2 = get_brand_mapping_coverage()
    json_str = json.dumps(result2)
    print(f"JSON serialization OK, length={len(json_str)}")
except Exception:
    print("JSON serialization FAILED:")
    traceback.print_exc()
