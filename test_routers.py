import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from routers.articles import router as articles_router
    print("SUCCESS: articles router imported")

    from routers.article_mapping import router as mapping_router
    print("SUCCESS: mapping router imported")

    from routers.article_import import router as import_router
    print("SUCCESS: import router imported")

    from routers.departments import router as departments_router
    print("SUCCESS: departments router imported")

    print("All routers imported successfully")

except Exception as e:
    print(f"ERROR importing routers: {e}")
    import traceback
    traceback.print_exc()
