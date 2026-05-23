import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse

from middleware.permission_middleware import PermissionMiddleware
from services.audit_service import ensure_audit_table
from services.soft_delete_service import ensure_soft_delete_columns

from routers.articles import router as articles_router, ensure_article_table
from routers.article_mapping import router as mapping_router, ensure_import_sources_standalone
from routers.article_import import router as import_router
from routers.article_source_mapping import router as article_source_mapping_router
from routers.pnl_import import (
    router as pnl_import_router,
    ensure_article_mapping_table,
    ensure_department_mapping_table,
    ensure_pnl_column_mapping_table,
)
from routers.departments import router as departments_router, ensure_department_table
from routers.pnl_data import router as pnl_data_router
from routers.reference_data import router as reference_data_router
from routers.holdings import router as holdings_router, ensure_holding_table
from routers.organizations import router as organizations_router, ensure_organization_table
from routers.regions import router as regions_router, ensure_region_table
from routers.branches import router as branches_router, ensure_branch_table
from routers.sources import router as sources_router, ensure_source_table
from routers.pnl_structure import router as pnl_structure_router, ensure_pnl_structure_table
from routers.pnl_levels import router as pnl_levels_router, migrate_pnl_levels_from_articles
from routers.admin_access import router as admin_access_router, ensure_admin_tables
from routers.auth_router import router as auth_router
from routers.import_engine import router as import_engine_router
from services.import_engine import ensure_import_engine_tables
from routers.brands import router as brands_router, ensure_brand_table
from services.article_import_service import (
    ensure_source_staging_tables,
    migrate_legacy_article_mappings,
)

app = FastAPI(title="Planning Web")


@app.on_event("startup")
def init_db():
    ensure_article_table()
    ensure_department_table()
    ensure_holding_table()
    ensure_region_table()
    ensure_organization_table()
    ensure_branch_table()
    ensure_source_table()
    ensure_pnl_structure_table()
    ensure_import_sources_standalone()
    ensure_article_mapping_table()
    ensure_department_mapping_table()
    ensure_pnl_column_mapping_table()
    ensure_source_staging_tables()
    migrate_legacy_article_mappings()
    migrate_pnl_levels_from_articles()
    ensure_admin_tables()
    ensure_audit_table()
    ensure_soft_delete_columns()
    ensure_brand_table()
    ensure_import_engine_tables()


# Permission enforcement (must be added before CORS to run after it in Starlette chain)
app.add_middleware(PermissionMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost:8002",
        "http://127.0.0.1:8002",
        "https://metricore.com.ua",
        "http://metricore.com.ua",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API ROUTES
app.include_router(articles_router)
app.include_router(mapping_router)
app.include_router(import_router)
app.include_router(departments_router)
app.include_router(pnl_data_router)
app.include_router(reference_data_router)
app.include_router(holdings_router)
app.include_router(organizations_router)
app.include_router(regions_router)
app.include_router(branches_router)
app.include_router(sources_router)
app.include_router(pnl_structure_router)
app.include_router(pnl_import_router)
app.include_router(pnl_levels_router)
app.include_router(article_source_mapping_router)
app.include_router(admin_access_router)
app.include_router(auth_router)
app.include_router(import_engine_router)
app.include_router(brands_router)


FRONT_BUILD_DIR = r"T:\planning_front\build"
FRONT_STATIC_DIR = os.path.join(FRONT_BUILD_DIR, "static")
FRONT_INDEX_FILE = os.path.join(FRONT_BUILD_DIR, "index.html")


# REACT STATIC FILES
if os.path.isdir(FRONT_STATIC_DIR):
    app.mount(
        "/static",
        StaticFiles(directory=FRONT_STATIC_DIR),
        name="static",
    )


def frontend_not_built_response():
    return PlainTextResponse(
        "Frontend build is missing, but API is working.",
        status_code=200,
    )


# REACT INDEX
@app.get("/")
def serve_root():
    if os.path.isfile(FRONT_INDEX_FILE):
        return FileResponse(FRONT_INDEX_FILE)
    return frontend_not_built_response()


# REACT ROUTER SUPPORT
@app.get("/{full_path:path}")
def serve_react(full_path: str):
    if full_path.startswith("api"):
        return {"error": "API route not found"}

    if os.path.isfile(FRONT_INDEX_FILE):
        return FileResponse(FRONT_INDEX_FILE)
    return frontend_not_built_response()