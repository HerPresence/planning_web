from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers.articles import router as articles_router
from routers.article_mapping import router as mapping_router
from routers.article_import import router as import_router
from routers.departments import router as departments_router
from routers.pnl_data import router as pnl_data_router

app = FastAPI(title="Planning Web")


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


# REACT STATIC FILES
app.mount(
    "/static",
    StaticFiles(directory=r"T:\planning_front\build\static"),
    name="static",
)


# REACT INDEX
@app.get("/")
def serve_root():
    return FileResponse(r"T:\planning_front\build\index.html")


# REACT ROUTER SUPPORT
@app.get("/{full_path:path}")
def serve_react(full_path: str):
    if full_path.startswith("api"):
        return {"error": "API route not found"}

    return FileResponse(r"T:\planning_front\build\index.html")