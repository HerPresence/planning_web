import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from routers.departments import router
    print("SUCCESS: departments router imported successfully")
    print(f"router type: {type(router)}")
    print(f"router prefix: {getattr(router, 'prefix', 'no prefix')}")
except Exception as e:
    print(f"ERROR importing departments router: {e}")
    import traceback
    traceback.print_exc()
