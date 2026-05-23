from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from auth.dependencies import get_current_user, bearer
from auth.password_policy import validate_password_policy
from auth.utils import create_access_token, hash_password, verify_password, decode_token, TOKEN_EXPIRE_HOURS
from db import get_connection
from services.audit_service import log_action

router = APIRouter()

_MAX_ATTEMPTS = 5
_LOCK_MINUTES = 15


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


def _fmt(dt):
    return dt.isoformat() if dt else None


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    email = body.email.strip().lower()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, full_name, email, password_hash, is_active,
                   failed_login_attempts, locked_until, force_change_password
            FROM users WHERE LOWER(email) = %s
        """, (email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Невірний email або пароль")

        (user_id, full_name, user_email, pw_hash,
         is_active, failed_attempts, locked_until, force_change) = row

        if not is_active:
            raise HTTPException(status_code=403, detail="Обліковий запис деактивовано")

        if locked_until and locked_until > datetime.now():
            remaining = max(1, int((locked_until - datetime.now()).total_seconds() / 60))
            raise HTTPException(
                status_code=423,
                detail=f"Обліковий запис тимчасово заблоковано. Спробуйте через {remaining} хв."
            )

        if not verify_password(body.password, pw_hash):
            new_attempts = (failed_attempts or 0) + 1
            if new_attempts >= _MAX_ATTEMPTS:
                lock_until = datetime.now() + timedelta(minutes=_LOCK_MINUTES)
                cur.execute("""
                    UPDATE users SET failed_login_attempts = %s, locked_until = %s WHERE id = %s
                """, (new_attempts, lock_until, user_id))
                conn.commit()
                raise HTTPException(
                    status_code=423,
                    detail=f"Забагато невдалих спроб. Обліковий запис заблоковано на {_LOCK_MINUTES} хв."
                )
            cur.execute(
                "UPDATE users SET failed_login_attempts = %s WHERE id = %s",
                (new_attempts, user_id)
            )
            conn.commit()
            log_action("login_failed", user_id=user_id, user_email=email,
                       entity_type="user", entity_id=user_id, menu_key="auth",
                       new_value={"attempts": new_attempts})
            raise HTTPException(status_code=401, detail="Невірний email або пароль")

        # Correct password — reset counters, record login time
        cur.execute("""
            UPDATE users
            SET failed_login_attempts = 0, locked_until = NULL, last_login_at = NOW()
            WHERE id = %s
        """, (user_id,))

        cur.execute("""
            SELECT 1 FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = %s AND r.role_name = 'Admin' AND r.is_active = TRUE
        """, (user_id,))
        is_admin = cur.fetchone() is not None

        conn.commit()
        log_action("login_success", user_id=user_id, user_email=user_email,
                   entity_type="user", entity_id=user_id, menu_key="auth")
        token, jti = create_access_token(user_id, user_email, is_admin)

        # Record session for revocation support
        try:
            ip = request.client.host if request.client else None
            ua = request.headers.get("user-agent")
            expires = datetime.now() + timedelta(hours=TOKEN_EXPIRE_HOURS)
            cur.execute("""
                INSERT INTO user_sessions (user_id, token_jti, ip_address, user_agent, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (token_jti) DO NOTHING
            """, (user_id, jti, ip, ua, expires))
            conn.commit()
        except Exception:
            pass  # session tracking must not block login

        return {
            "access_token": token,
            "token_type": "bearer",
            "force_change_password": bool(force_change),
            "user": {
                "id": user_id,
                "full_name": full_name,
                "email": user_email,
                "is_admin": is_admin,
            },
        }
    finally:
        cur.close()
        conn.close()


@router.post("/api/auth/change-password")
def change_password(body: ChangePasswordBody, user=Depends(get_current_user)):
    if body.new_password != body.confirm_password:
        raise HTTPException(400, "Паролі не співпадають")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT password_hash, email FROM users WHERE id = %s",
            (user["id"],)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Користувача не знайдено")
        pw_hash, email = row

        if not verify_password(body.current_password, pw_hash):
            raise HTTPException(400, "Поточний пароль невірний")

        policy_errors = validate_password_policy(body.new_password, email)
        if policy_errors:
            raise HTTPException(400, "; ".join(policy_errors))

        cur.execute("""
            UPDATE users
            SET password_hash         = %s,
                password_changed_at   = NOW(),
                force_change_password = FALSE,
                updated_at            = NOW()
            WHERE id = %s
        """, (hash_password(body.new_password), user["id"]))
        conn.commit()
        log_action("password_changed", user_id=user["id"], user_email=user["email"],
                   entity_type="user", entity_id=user["id"], menu_key="profile")
        return {"ok": True, "message": "Пароль успішно змінено"}
    finally:
        cur.close()
        conn.close()


@router.post("/api/auth/logout")
def logout(
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
):
    try:
        payload = decode_token(credentials.credentials)
        jti = payload.get("jti")
        if jti:
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute(
                    "UPDATE user_sessions SET revoked_at = NOW() WHERE token_jti = %s",
                    (jti,)
                )
                conn.commit()
            finally:
                cur.close()
                conn.close()
    except Exception:
        pass  # revocation failure must not block the response
    log_action("logout", user_id=user["id"], user_email=user["email"],
               entity_type="user", entity_id=user["id"], menu_key="auth")
    return {"ok": True}


@router.get("/api/me")
def get_me(user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, full_name, email, is_active,
                   force_change_password, password_changed_at, last_login_at
            FROM users WHERE id = %s
        """, (user["id"],))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Користувача не знайдено")

        cur.execute("""
            SELECT r.role_name FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = %s ORDER BY r.role_name
        """, (user["id"],))
        roles = [r[0] for r in cur.fetchall()]

        return {
            "id": row[0],
            "full_name": row[1],
            "email": row[2],
            "is_active": row[3],
            "is_admin": user["is_admin"],
            "force_change_password": bool(row[4]),
            "password_changed_at": _fmt(row[5]),
            "last_login_at": _fmt(row[6]),
            "roles": roles,
        }
    finally:
        cur.close()
        conn.close()


@router.get("/api/me/permissions")
def get_my_permissions(user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if user["is_admin"]:
            cur.execute(
                "SELECT menu_key FROM menu_items WHERE is_active = TRUE ORDER BY sort_order"
            )
            return [{"menu_key": r[0], "can_view": True, "can_edit": True, "can_create": True}
                    for r in cur.fetchall()]

        cur.execute("""
            SELECT mi.menu_key,
                   COALESCE(bool_or(rp.can_view),   FALSE) AS can_view,
                   COALESCE(bool_or(rp.can_edit),   FALSE) AS can_edit,
                   COALESCE(bool_or(rp.can_create), FALSE) AS can_create
            FROM menu_items mi
            LEFT JOIN (
                SELECT rp2.menu_key, rp2.can_view, rp2.can_edit, rp2.can_create
                FROM role_permissions rp2
                INNER JOIN user_roles ur2
                        ON ur2.role_id = rp2.role_id AND ur2.user_id = %s
            ) rp ON rp.menu_key = mi.menu_key
            WHERE mi.is_active = TRUE
            GROUP BY mi.menu_key, mi.sort_order
            ORDER BY mi.sort_order
        """, (user["id"],))
        return [
            {"menu_key": r[0], "can_view": r[1], "can_edit": r[2], "can_create": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()
