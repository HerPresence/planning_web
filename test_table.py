import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from routers.departments import ensure_department_table
    print("SUCCESS: ensure_department_table imported successfully")

    # Try to call the function
    ensure_department_table()
    print("SUCCESS: ensure_department_table executed successfully")

except Exception as e:
    print(f"ERROR with ensure_department_table: {e}")
    import traceback
    traceback.print_exc()
