import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse

from routers.articles import router as articles_router, ensure_article_table
from routers.article_mapping import router as mapping_router, ensure_import_sources_standalone
from routers.article_import import router as import_router
from routers.pnl_import import (
    router as pnl_import_router,
    ensure_article_mapping_table,
    ensure_department_mapping_table,
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


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8002",
        "http://localhost:8002",
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