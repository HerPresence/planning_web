"""
Permission middleware — centralized backend permission enforcement.

GET  requests require can_view for the matching menu_key.
POST/PUT/PATCH/DELETE require can_edit.
Admins bypass all permission checks (is_admin in JWT).
"""
import re
from jose import JWTError

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.utils import decode_token, is_jti_revoked, SECRET_KEY, ALGORITHM
from db import get_connection

# Routes that bypass permission checks entirely
_PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/me",
    "/static",
    "/favicon",
)

# Ordered list: (path_prefix, menu_key)  — most specific first.
_PATH_TO_MENU: list[tuple[str, str]] = [
    # Admin routes
    ("/api/admin/audit-log",              "auditLog"),
    ("/api/admin/permission-templates",   "permissions"),
    ("/api/admin/menu-items",             "permissions"),
    ("/api/admin/users",                  "users"),
    ("/api/admin/roles",                  "roles"),
    # Regular routes
    ("/api/pnl-levels/level2",            "masterL2"),
    ("/api/pnl-levels/level1",            "masterL1"),
    ("/api/pnl-levels",                   "masterL1"),
    ("/api/pnl-structure",                "pnlStructure"),
    ("/api/pnl-import",                   "pnlImport"),
    ("/api/pnl",                          "pnlData"),
    ("/api/article-source-mapping",       "importSources"),
    ("/api/import-sources",               "importSources"),
    ("/api/import-articles",              "pnlImport"),
    # Import engine — fact-turnover view maps to factTurnover;
    # source/mapping configuration maps to importSources;
    # load/commit/staging/batches map to factTurnover (sales_fact workflow)
    ("/api/import-engine/fact-turnover",  "factTurnover"),
    ("/api/import-engine/batches",        "factTurnover"),
    ("/api/import-engine/load",           "factTurnover"),
    ("/api/import-engine/commit",         "factTurnover"),
    ("/api/import-engine/staging",        "factTurnover"),
    ("/api/import-engine/sources",        "importSources"),
    ("/api/import-engine/field-mapping",  "importSources"),
    ("/api/import-engine/preview",        "importSources"),
    ("/api/import-engine/types",          "importSources"),
    ("/api/articles",                     "articles"),
    ("/api/departments",                  "departments"),
    ("/api/holdings",                     "holdings"),
    ("/api/organizations",                "organizations"),
    ("/api/regions",                      "regions"),
    ("/api/branches",                     "branches"),
    ("/api/sources",                      "sources"),
]

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _get_menu_key(path: str) -> str | None:
    # /api/admin/roles/{id}/permissions and /apply-template belong to "permissions"
    if path.startswith("/api/admin/roles/") and (
        "/permissions" in path or "/apply-template/" in path
    ):
        return "permissions"
    for prefix, key in _PATH_TO_MENU:
        if path.startswith(prefix):
            return key
    return None


def _parse_jwt(request: Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
        jti = payload.get("jti")
        if jti and is_jti_revoked(jti):
            return None  # token explicitly revoked
        return {
            "id": int(payload["sub"]),
            "email": payload.get("email", ""),
            "is_admin": payload.get("is_admin", False),
        }
    except (JWTError, KeyError, ValueError):
        return None


def _check_db_permission(user_id: int, menu_key: str, require_edit: bool) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        if require_edit:
            expr = "rp.can_edit"
        else:
            expr = "rp.can_view OR rp.can_edit"
        cur.execute(
            f"""
            SELECT bool_or({expr})
            FROM role_permissions rp
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = %s AND rp.menu_key = %s
            """,
            (user_id, menu_key),
        )
        row = cur.fetchone()
        return bool(row and row[0])
    finally:
        cur.close()
        conn.close()


class PermissionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Skip non-API paths
        if not path.startswith("/api/"):
            return await call_next(request)

        # Skip public routes (login, /api/me)
        for pub in _PUBLIC_PREFIXES:
            if path.startswith(pub):
                return await call_next(request)

        # Skip /api/reference — read-only lookup data, just need auth
        if path.startswith("/api/reference"):
            user = _parse_jwt(request)
            if not user:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return await call_next(request)

        # Determine which menu section this path belongs to
        menu_key = _get_menu_key(path)
        if menu_key is None:
            # Unknown API path — require authentication only
            user = _parse_jwt(request)
            if not user:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return await call_next(request)

        # Parse JWT
        user = _parse_jwt(request)
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # Admins bypass permission checks
        if user["is_admin"]:
            return await call_next(request)

        # Determine required permission
        require_edit = method not in READ_METHODS
        err = (
            "У вас немає прав для редагування цього розділу"
            if require_edit
            else "У вас немає прав для перегляду цього розділу"
        )

        if not _check_db_permission(user["id"], menu_key, require_edit):
            return JSONResponse({"detail": err}, status_code=403)

        return await call_next(request)
