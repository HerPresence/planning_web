from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user, require_admin
from auth.password_policy import validate_password_policy
from auth.utils import hash_password
from db import get_connection
from services.audit_service import log_action

router = APIRouter(prefix="/api/admin")

_tables_ensured = False

MENU_ITEMS_SEED = [
    ("home",          "Головна",          None,          "home",          1),
    ("articles",      "Статті PnL",       "directories", "articles",      2),
    ("importSources", "Відповідність",    "directories", "importSources", 3),
    ("masterL2",      "Master L2",        "directories", "masterL2",      4),
    ("masterL1",      "Master L1",        "directories", "masterL1",      5),
    ("departments",   "Підрозділи",       "directories", "departments",   6),
    ("holdings",      "Холдинги",         "directories", "holdings",      7),
    ("organizations", "Організації",      "directories", "organizations", 8),
    ("regions",       "Регіони",          "directories", "regions",       9),
    ("branches",      "Філії",            "directories", "branches",      10),
    ("sources",       "Джерела",          "directories", "sources",       11),
    ("pnlStructure",  "Структура PnL",    "directories", "pnlStructure",  12),
    ("cashflow",      "БДДС",             "planning",    "cashflow",      13),
    ("pnlData",       "План / Факт PnL",  "planning",    "pnlData",       14),
    ("pnlImport",     "Імпорт PnL",       "planning",    "pnlImport",     15),
    ("importData",    "Імпорт даних",     "planning",    "importData",    16),
    ("factTurnover",  "Факт продажів",    "planning",    "factTurnover",  17),
    ("budgets",       "Бюджети витрат",   "planning",    "budgets",       18),
    ("users",         "Користувачі",      "admin",       "users",         17),
    ("roles",         "Ролі",             "admin",       "roles",         18),
    ("permissions",   "Права доступу",    "admin",       "permissions",   19),
    ("auditLog",      "Журнал дій",       "admin",       "auditLog",      20),
    ("settings",      "Налаштування",     "admin",       "settings",      21),
]


def ensure_admin_tables() -> None:
    global _tables_ensured
    if _tables_ensured:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                full_name     TEXT      NOT NULL,
                email         TEXT      NOT NULL UNIQUE,
                password_hash TEXT      NOT NULL,
                is_active     BOOLEAN   DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT NOW(),
                updated_at    TIMESTAMP DEFAULT NOW()
            )
        """)

        # Migrate: add security columns if not present
        for col_sql in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS force_change_password   BOOLEAN   DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at     TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at           TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts   INTEGER   DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until            TIMESTAMP",
        ]:
            cur.execute(col_sql)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_jti   TEXT      NOT NULL UNIQUE,
                ip_address  TEXT,
                user_agent  TEXT,
                created_at  TIMESTAMP DEFAULT NOW(),
                expires_at  TIMESTAMP NOT NULL,
                revoked_at  TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS permission_templates (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                permissions JSONB NOT NULL DEFAULT '[]',
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        # Seed default templates
        default_templates = [
            ("CFO Full",         "Повний доступ до планування та звітності",
             [{"menu_key": k, "can_view": True, "can_edit": True}
              for k in ["home","pnlData","pnlImport","cashflow","budgets","articles","departments","holdings","organizations","regions","branches","sources","pnlStructure","importSources","importData","masterL1","masterL2"]]),
            ("Controller",       "Перегляд всіх даних, редагування плану",
             [{"menu_key": k, "can_view": True, "can_edit": k in ["pnlData","pnlImport"]}
              for k in ["home","pnlData","pnlImport","cashflow","budgets","articles","departments","holdings","organizations","regions","branches","sources","pnlStructure","importSources","importData","masterL1","masterL2"]]),
            ("Read Only",        "Тільки перегляд даних",
             [{"menu_key": k, "can_view": True, "can_edit": False}
              for k in ["home","pnlData","cashflow","budgets","articles","departments","holdings","organizations","regions","branches","sources","pnlStructure","masterL1","masterL2"]]),
            ("Department Manager","Перегляд підрозділу",
             [{"menu_key": k, "can_view": True, "can_edit": k in ["pnlData"]}
              for k in ["home","pnlData","departments"]]),
        ]
        import json as _json
        for tname, tdesc, tperms in default_templates:
            cur.execute("""
                INSERT INTO permission_templates (name, description, permissions)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO NOTHING
            """, (tname, tdesc, _json.dumps(tperms)))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_data_scope (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                scope_type  TEXT    NOT NULL,
                scope_value TEXT    NOT NULL,
                can_edit    BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMP DEFAULT NOW(),
                UNIQUE (user_id, scope_type, scope_value)
            )
        """)
        cur.execute(
            "ALTER TABLE user_data_scope ADD COLUMN IF NOT EXISTS can_edit BOOLEAN NOT NULL DEFAULT FALSE"
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id          SERIAL PRIMARY KEY,
                role_name   TEXT      NOT NULL UNIQUE,
                description TEXT      DEFAULT '',
                is_active   BOOLEAN   DEFAULT TRUE,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, role_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id         SERIAL PRIMARY KEY,
                menu_key   TEXT    NOT NULL UNIQUE,
                menu_name  TEXT    NOT NULL,
                parent_key TEXT,
                route      TEXT    NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active  BOOLEAN DEFAULT TRUE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                id         SERIAL PRIMARY KEY,
                role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                menu_key   TEXT    NOT NULL,
                can_view   BOOLEAN DEFAULT FALSE,
                can_edit   BOOLEAN DEFAULT FALSE,
                can_create BOOLEAN DEFAULT FALSE,
                UNIQUE (role_id, menu_key)
            )
        """)
        cur.execute(
            "ALTER TABLE role_permissions ADD COLUMN IF NOT EXISTS can_create BOOLEAN DEFAULT FALSE"
        )

        # Seed menu items (DO NOTHING so existing customizations are preserved)
        for (menu_key, menu_name, parent_key, route, sort_order) in MENU_ITEMS_SEED:
            cur.execute("""
                INSERT INTO menu_items (menu_key, menu_name, parent_key, route, sort_order)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (menu_key) DO NOTHING
            """, (menu_key, menu_name, parent_key, route, sort_order))

        # Fix sort_order for items that may have been seeded before factTurnover was added
        cur.execute(
            "UPDATE menu_items SET sort_order = 17 WHERE menu_key = 'budgets' AND sort_order = 16"
        )
        # Shift factTurnover and budgets to make room for importData at sort_order 16
        cur.execute(
            "UPDATE menu_items SET sort_order = 17 WHERE menu_key = 'factTurnover' AND sort_order = 16"
        )
        cur.execute(
            "UPDATE menu_items SET sort_order = 18 WHERE menu_key = 'budgets' AND sort_order = 17"
        )

        # Seed Admin role
        cur.execute("""
            INSERT INTO roles (role_name, description, is_active)
            VALUES ('Admin', 'Повний доступ до системи', TRUE)
            ON CONFLICT (role_name) DO NOTHING
        """)

        cur.execute("SELECT id FROM roles WHERE role_name = 'Admin'")
        admin_role_row = cur.fetchone()
        if admin_role_row:
            admin_role_id = admin_role_row[0]
            cur.execute("SELECT menu_key FROM menu_items")
            for (mk,) in cur.fetchall():
                cur.execute("""
                    INSERT INTO role_permissions (role_id, menu_key, can_view, can_edit, can_create)
                    VALUES (%s, %s, TRUE, TRUE, TRUE)
                    ON CONFLICT (role_id, menu_key) DO UPDATE SET can_view = TRUE, can_edit = TRUE, can_create = TRUE
                """, (admin_role_id, mk))

        # Seed factTurnover permissions for standard non-admin roles (if those roles exist)
        # DO NOTHING — never overwrite manually-set permissions
        _fact_role_perms = [
            ("CFO",                  True,  True,  True),
            ("Business Controller",  True,  False, False),
            ("Viewer",               True,  False, False),
        ]
        for role_name, can_view, can_edit, can_create in _fact_role_perms:
            cur.execute("SELECT id FROM roles WHERE role_name = %s", (role_name,))
            rrow = cur.fetchone()
            if rrow:
                cur.execute("""
                    INSERT INTO role_permissions (role_id, menu_key, can_view, can_edit, can_create)
                    VALUES (%s, 'factTurnover', %s, %s, %s)
                    ON CONFLICT (role_id, menu_key) DO NOTHING
                """, (rrow[0], can_view, can_edit, can_create))

        # Seed importData permissions for standard non-admin roles (if those roles exist)
        # DO NOTHING — never overwrite manually-set permissions
        _import_data_role_perms = [
            ("CFO",                  True,  True,  True),
            ("Business Controller",  True,  False, False),
        ]
        for role_name, can_view, can_edit, can_create in _import_data_role_perms:
            cur.execute("SELECT id FROM roles WHERE role_name = %s", (role_name,))
            rrow = cur.fetchone()
            if rrow:
                cur.execute("""
                    INSERT INTO role_permissions (role_id, menu_key, can_view, can_edit, can_create)
                    VALUES (%s, 'importData', %s, %s, %s)
                    ON CONFLICT (role_id, menu_key) DO NOTHING
                """, (rrow[0], can_view, can_edit, can_create))

        # Seed admin user
        _admin_plain = "Admin123!"

        cur.execute(
            "SELECT id FROM users WHERE LOWER(email) = 'admin@metricore.com.ua'"
        )
        existing_admin = cur.fetchone()

        if existing_admin:
            admin_user_id = existing_admin[0]
            # First time migration: force password change if admin never changed it
            cur.execute("""
                UPDATE users
                SET force_change_password = TRUE
                WHERE id = %s AND password_changed_at IS NULL AND force_change_password = FALSE
            """, (admin_user_id,))
        else:
            cur.execute("""
                INSERT INTO users (full_name, email, password_hash, is_active, force_change_password)
                VALUES ('Адміністратор', 'admin@metricore.com.ua', %s, TRUE, TRUE)
                RETURNING id
            """, (hash_password(_admin_plain),))
            admin_user_id = cur.fetchone()[0]

        if admin_role_row:
            cur.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (admin_user_id, admin_role_id),
            )

        conn.commit()
        _tables_ensured = True
        print("[startup] ensure_admin_tables: done")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"ensure_admin_tables failed: {exc}") from exc
    finally:
        cur.close()
        conn.close()


# ── Request models ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    full_name: str
    email: str
    password: str
    role_ids: List[int] = []


class UserUpdate(BaseModel):
    full_name: str
    email: str
    role_ids: List[int] = []


class RoleCreate(BaseModel):
    role_name: str
    description: str = ""


class RoleUpdate(BaseModel):
    role_name: str
    description: str = ""


class PermissionItem(BaseModel):
    menu_key: str
    can_view: bool
    can_edit: bool
    can_create: bool = False


class PermissionsUpdate(BaseModel):
    permissions: List[PermissionItem]


class ResetPasswordBody(BaseModel):
    new_password: str
    confirm_password: str
    force_change_password: bool = True


class ScopeItem(BaseModel):
    scope_type: str
    scope_value: str
    can_edit: bool = False


class ScopeUpdate(BaseModel):
    scopes: List[ScopeItem]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _user_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "full_name": row[1],
        "email": row[2],
        "is_active": row[3],
        "created_at": str(row[4]),
        "roles": row[5] or [],
        "role_ids": row[6] or [],
    }


_USER_SELECT = """
    SELECT u.id, u.full_name, u.email, u.is_active, u.created_at,
           array_agg(r.role_name ORDER BY r.role_name) FILTER (WHERE r.id IS NOT NULL) AS roles,
           array_agg(r.id       ORDER BY r.role_name) FILTER (WHERE r.id IS NOT NULL) AS role_ids
    FROM users u
    LEFT JOIN user_roles ur ON ur.user_id = u.id
    LEFT JOIN roles r ON r.id = ur.role_id
"""


# ── Users ──────────────────────────────────────────────────────────────────────

@router.get("/users")
def get_users(_u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(_USER_SELECT + " GROUP BY u.id ORDER BY u.id")
        return [_user_row_to_dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


@router.post("/users")
def create_user(body: UserCreate, _u=Depends(get_current_user)):
    ensure_admin_tables()
    if not _u["is_admin"]:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT bool_or(rp.can_create)
                FROM role_permissions rp
                JOIN user_roles ur ON ur.role_id = rp.role_id
                WHERE ur.user_id = %s AND rp.menu_key = 'users'
            """, (_u["id"],))
            row = cur.fetchone()
            if not row or not row[0]:
                raise HTTPException(403, "У вас немає прав для створення користувачів")
            if body.role_ids:
                cur.execute(
                    "SELECT role_name FROM roles WHERE id = ANY(%s::int[])", (body.role_ids,)
                )
                if any(r[0] == "Admin" for r in cur.fetchall()):
                    raise HTTPException(403, "Недостатньо прав для призначення ролі Admin")
        finally:
            cur.close()
            conn.close()
    name = body.full_name.strip()
    email = body.email.strip().lower()
    if not name or not email:
        raise HTTPException(400, "ПІБ та email обов'язкові")
    policy_errors = validate_password_policy(body.password, email)
    if policy_errors:
        raise HTTPException(400, "; ".join(policy_errors))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE LOWER(email) = %s", (email,))
        if cur.fetchone():
            raise HTTPException(409, f"Користувач з email '{email}' вже існує")
        cur.execute("""
            INSERT INTO users (full_name, email, password_hash, is_active)
            VALUES (%s, %s, %s, TRUE) RETURNING id
        """, (name, email, hash_password(body.password)))
        user_id = cur.fetchone()[0]
        for rid in body.role_ids:
            cur.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, rid),
            )
        conn.commit()
        cur.execute(_USER_SELECT + " WHERE u.id = %s GROUP BY u.id", (user_id,))
        return _user_row_to_dict(cur.fetchone())
    finally:
        cur.close(); conn.close()


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, _u=Depends(require_admin)):
    ensure_admin_tables()
    name = body.full_name.strip()
    email = body.email.strip().lower()
    if not name or not email:
        raise HTTPException(400, "ПІБ та email обов'язкові")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE LOWER(email) = %s AND id != %s", (email, user_id))
        if cur.fetchone():
            raise HTTPException(409, f"Email '{email}' вже використовується")
        cur.execute("""
            UPDATE users SET full_name = %s, email = %s, updated_at = NOW()
            WHERE id = %s RETURNING id
        """, (name, email, user_id))
        if not cur.fetchone():
            raise HTTPException(404, "Користувача не знайдено")
        cur.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
        for rid in body.role_ids:
            cur.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, rid),
            )
        conn.commit()
        log_action("user_updated", user_id=_u["id"], user_email=_u["email"],
                   entity_type="user", entity_id=user_id, menu_key="users",
                   new_value={"full_name": name, "email": email, "role_ids": body.role_ids})
        cur.execute(_USER_SELECT + " WHERE u.id = %s GROUP BY u.id", (user_id,))
        return _user_row_to_dict(cur.fetchone())
    finally:
        cur.close(); conn.close()


@router.patch("/users/{user_id}/toggle")
def toggle_user(user_id: int, _u=Depends(require_admin)):
    ensure_admin_tables()
    if user_id == _u["id"]:
        raise HTTPException(400, "Не можна деактивувати власний обліковий запис")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE users SET is_active = NOT is_active, updated_at = NOW()
            WHERE id = %s RETURNING id, full_name, is_active
        """, (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Користувача не знайдено")
        conn.commit()
        log_action("user_toggled", user_id=_u["id"], user_email=_u["email"],
                   entity_type="user", entity_id=user_id, menu_key="users",
                   new_value={"is_active": row[2]})
        return {"id": row[0], "full_name": row[1], "is_active": row[2]}
    finally:
        cur.close(); conn.close()


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, body: ResetPasswordBody, _u=Depends(require_admin)):
    ensure_admin_tables()
    if user_id == _u["id"]:
        raise HTTPException(400, "Використовуйте 'Мій профіль' для зміни власного пароля")
    if body.new_password != body.confirm_password:
        raise HTTPException(400, "Паролі не співпадають")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Користувача не знайдено")
        email = row[0]
        policy_errors = validate_password_policy(body.new_password, email)
        if policy_errors:
            raise HTTPException(400, "; ".join(policy_errors))
        cur.execute("""
            UPDATE users
            SET password_hash          = %s,
                password_changed_at    = NOW(),
                force_change_password  = %s,
                failed_login_attempts  = 0,
                locked_until           = NULL,
                updated_at             = NOW()
            WHERE id = %s
        """, (hash_password(body.new_password), body.force_change_password, user_id))
        conn.commit()
        log_action("password_reset", user_id=_u["id"], user_email=_u["email"],
                   entity_type="user", entity_id=user_id, menu_key="users",
                   new_value={"force_change_password": body.force_change_password})
        return {"ok": True}
    finally:
        cur.close(); conn.close()


@router.post("/users/{user_id}/logout-all")
def logout_all_user_sessions(user_id: int, _u=Depends(require_admin)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Користувача не знайдено")
        cur.execute(
            "UPDATE user_sessions SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,)
        )
        count = cur.rowcount
        conn.commit()
        log_action("sessions_revoked", user_id=_u["id"], user_email=_u["email"],
                   entity_type="user", entity_id=user_id, menu_key="users",
                   new_value={"sessions_revoked": count})
        return {"ok": True, "sessions_revoked": count}
    finally:
        cur.close(); conn.close()


@router.get("/users/{user_id}/scope")
def get_user_scope(user_id: int, _u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Користувача не знайдено")
        cur.execute(
            "SELECT id, scope_type, scope_value, can_edit FROM user_data_scope WHERE user_id = %s ORDER BY scope_type, scope_value",
            (user_id,)
        )
        return [{"id": r[0], "scope_type": r[1], "scope_value": r[2], "can_edit": r[3]} for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


@router.put("/users/{user_id}/scope")
def set_user_scope(user_id: int, body: ScopeUpdate, _u=Depends(require_admin)):
    ensure_admin_tables()
    from services.rls_service import VALID_SCOPE_TYPES
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Користувача не знайдено")
        for item in body.scopes:
            if item.scope_type not in VALID_SCOPE_TYPES:
                raise HTTPException(400, f"Невірний тип обмеження: {item.scope_type}")
            if not item.scope_value.strip():
                raise HTTPException(400, "Значення обмеження не може бути порожнім")
        cur.execute("DELETE FROM user_data_scope WHERE user_id = %s", (user_id,))
        for item in body.scopes:
            cur.execute(
                "INSERT INTO user_data_scope (user_id, scope_type, scope_value, can_edit) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (user_id, item.scope_type, item.scope_value.strip(), item.can_edit)
            )
        conn.commit()
        log_action("scope_updated", user_id=_u["id"], user_email=_u["email"],
                   entity_type="user", entity_id=user_id, menu_key="users",
                   new_value={"scopes": [{"type": s.scope_type, "value": s.scope_value, "can_edit": s.can_edit} for s in body.scopes]})
        cur.execute(
            "SELECT id, scope_type, scope_value, can_edit FROM user_data_scope WHERE user_id = %s ORDER BY scope_type, scope_value",
            (user_id,)
        )
        return [{"id": r[0], "scope_type": r[1], "scope_value": r[2], "can_edit": r[3]} for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


@router.get("/permission-templates")
def get_permission_templates(_u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, description, permissions FROM permission_templates ORDER BY id")
        return [{"id": r[0], "name": r[1], "description": r[2], "permissions": r[3]} for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


@router.post("/roles/{role_id}/apply-template/{template_id}")
def apply_permission_template(role_id: int, template_id: int, _u=Depends(require_admin)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT permissions FROM permission_templates WHERE id = %s", (template_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Шаблон не знайдено")
        perms = row[0]  # list of {menu_key, can_view, can_edit}
        cur.execute("SELECT id FROM roles WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Роль не знайдено")
        for perm in perms:
            can_edit = bool(perm.get("can_edit"))
            can_view = bool(perm.get("can_view")) or can_edit
            cur.execute("""
                INSERT INTO role_permissions (role_id, menu_key, can_view, can_edit)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (role_id, menu_key) DO UPDATE SET can_view = %s, can_edit = %s
            """, (role_id, perm["menu_key"], can_view, can_edit, can_view, can_edit))
        conn.commit()
        log_action("permissions_updated", user_id=_u["id"], user_email=_u["email"],
                   entity_type="role_permissions", entity_id=role_id, menu_key="permissions",
                   new_value={"template_id": template_id})
        return {"ok": True}
    finally:
        cur.close(); conn.close()


# ── Roles ──────────────────────────────────────────────────────────────────────

@router.get("/roles")
def get_roles(_u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, role_name, description, is_active, created_at FROM roles ORDER BY id"
        )
        return [
            {"id": r[0], "role_name": r[1], "description": r[2],
             "is_active": r[3], "created_at": str(r[4])}
            for r in cur.fetchall()
        ]
    finally:
        cur.close(); conn.close()


@router.post("/roles")
def create_role(body: RoleCreate, _u=Depends(require_admin)):
    ensure_admin_tables()
    name = body.role_name.strip()
    if not name:
        raise HTTPException(400, "Назва ролі обов'язкова")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM roles WHERE LOWER(role_name) = LOWER(%s)", (name,))
        if cur.fetchone():
            raise HTTPException(409, f"Роль '{name}' вже існує")
        cur.execute("""
            INSERT INTO roles (role_name, description, is_active)
            VALUES (%s, %s, TRUE)
            RETURNING id, role_name, description, is_active, created_at
        """, (name, body.description.strip()))
        row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "role_name": row[1], "description": row[2],
                "is_active": row[3], "created_at": str(row[4])}
    finally:
        cur.close(); conn.close()


@router.put("/roles/{role_id}")
def update_role(role_id: int, body: RoleUpdate, _u=Depends(require_admin)):
    ensure_admin_tables()
    name = body.role_name.strip()
    if not name:
        raise HTTPException(400, "Назва ролі обов'язкова")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM roles WHERE LOWER(role_name) = LOWER(%s) AND id != %s", (name, role_id))
        if cur.fetchone():
            raise HTTPException(409, f"Роль '{name}' вже існує")
        cur.execute("""
            UPDATE roles SET role_name = %s, description = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, role_name, description, is_active, created_at
        """, (name, body.description.strip(), role_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Роль не знайдено")
        conn.commit()
        return {"id": row[0], "role_name": row[1], "description": row[2],
                "is_active": row[3], "created_at": str(row[4])}
    finally:
        cur.close(); conn.close()


@router.patch("/roles/{role_id}/toggle")
def toggle_role(role_id: int, _u=Depends(require_admin)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT role_name FROM roles WHERE id = %s", (role_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Роль не знайдено")
        if row[0] == "Admin":
            raise HTTPException(400, "Роль Admin не можна деактивувати")
        cur.execute("""
            UPDATE roles SET is_active = NOT is_active, updated_at = NOW()
            WHERE id = %s RETURNING id, role_name, is_active
        """, (role_id,))
        row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "role_name": row[1], "is_active": row[2]}
    finally:
        cur.close(); conn.close()


# ── Menu items ─────────────────────────────────────────────────────────────────

@router.get("/menu-items")
def get_menu_items(_u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, menu_key, menu_name, parent_key, route, sort_order, is_active
            FROM menu_items WHERE is_active = TRUE ORDER BY sort_order
        """)
        return [
            {"id": r[0], "menu_key": r[1], "menu_name": r[2], "parent_key": r[3],
             "route": r[4], "sort_order": r[5], "is_active": r[6]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close(); conn.close()


# ── Permissions ────────────────────────────────────────────────────────────────

@router.get("/roles/{role_id}/permissions")
def get_role_permissions(role_id: int, _u=Depends(get_current_user)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT mi.menu_key, mi.menu_name, mi.parent_key, mi.sort_order,
                   COALESCE(rp.can_view, FALSE),
                   COALESCE(rp.can_edit, FALSE),
                   COALESCE(rp.can_create, FALSE)
            FROM menu_items mi
            LEFT JOIN role_permissions rp
                   ON rp.role_id = %s AND rp.menu_key = mi.menu_key
            WHERE mi.is_active = TRUE
            ORDER BY mi.sort_order
        """, (role_id,))
        return [
            {"menu_key": r[0], "menu_name": r[1], "parent_key": r[2],
             "sort_order": r[3], "can_view": r[4], "can_edit": r[5], "can_create": r[6]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close(); conn.close()


@router.put("/roles/{role_id}/permissions")
def update_role_permissions(role_id: int, body: PermissionsUpdate, _u=Depends(require_admin)):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM roles WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Роль не знайдено")
        for perm in body.permissions:
            can_view = perm.can_view or perm.can_edit or perm.can_create
            can_edit = perm.can_edit
            can_create = perm.can_create
            cur.execute("""
                INSERT INTO role_permissions (role_id, menu_key, can_view, can_edit, can_create)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (role_id, menu_key) DO UPDATE
                SET can_view = %s, can_edit = %s, can_create = %s
            """, (role_id, perm.menu_key, can_view, can_edit, can_create,
                  can_view, can_edit, can_create))
        conn.commit()
        log_action("permissions_updated", user_id=_u["id"], user_email=_u["email"],
                   entity_type="role_permissions", entity_id=role_id, menu_key="permissions")
        return {"ok": True}
    finally:
        cur.close(); conn.close()


# ── Audit Log ─────────────────────────────────────────────────────────────────

@router.get("/audit-log")
def get_audit_log(
    user_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    menu_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
    offset: int = 0,
    _u=Depends(get_current_user),
):
    ensure_admin_tables()
    conn = get_connection()
    cur = conn.cursor()
    try:
        where = []
        params = []
        if user_id:
            where.append("user_id = %s"); params.append(user_id)
        if action:
            where.append("action = %s"); params.append(action)
        if entity_type:
            where.append("entity_type = %s"); params.append(entity_type)
        if menu_key:
            where.append("menu_key = %s"); params.append(menu_key)
        if date_from:
            where.append("created_at >= %s"); params.append(date_from)
        if date_to:
            where.append("created_at <= %s"); params.append(date_to + " 23:59:59")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        cur.execute(
            f"SELECT COUNT(*) FROM audit_log {clause}",
            params
        )
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT id, user_id, user_email, action, entity_type, entity_id,
                       menu_key, old_value, new_value, ip_address, user_agent, created_at
                FROM audit_log {clause}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset]
        )
        rows = cur.fetchall()
        return {
            "total": total,
            "items": [
                {"id": r[0], "user_id": r[1], "user_email": r[2], "action": r[3],
                 "entity_type": r[4], "entity_id": r[5], "menu_key": r[6],
                 "old_value": r[7], "new_value": r[8], "ip_address": r[9],
                 "user_agent": r[10], "created_at": str(r[11])}
                for r in rows
            ],
        }
    finally:
        cur.close(); conn.close()
