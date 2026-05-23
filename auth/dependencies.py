from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from auth.utils import decode_token, is_jti_revoked
from db import get_connection

bearer = HTTPBearer(auto_error=False)


def _parse(credentials: HTTPAuthorizationCredentials | None) -> dict | None:
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        jti = payload.get("jti")
        if jti and is_jti_revoked(jti):
            return None  # explicitly logged out
        return {
            "id": int(payload["sub"]),
            "email": payload["email"],
            "is_admin": payload.get("is_admin", False),
        }
    except (JWTError, KeyError, ValueError):
        return None


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    user = _parse(credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    user = _parse(credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict | None:
    return _parse(credentials)


def require_permission(menu_key: str, action: str = "view"):
    """
    Factory that returns a FastAPI dependency.
    action: "edit" requires can_edit; "view" requires can_view OR can_edit.
    Admins bypass all checks.
    """
    err_msg = (
        "У вас немає прав для редагування цього розділу"
        if action == "edit"
        else "У вас немає прав для перегляду цього розділу"
    )

    def _dep(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
        user = _parse(credentials)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if user["is_admin"]:
            return user
        conn = get_connection()
        cur = conn.cursor()
        try:
            expr = "rp.can_edit" if action == "edit" else "rp.can_view OR rp.can_edit"
            cur.execute(
                f"""
                SELECT bool_or({expr})
                FROM role_permissions rp
                JOIN user_roles ur ON ur.role_id = rp.role_id
                WHERE ur.user_id = %s AND rp.menu_key = %s
                """,
                (user["id"], menu_key),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                raise HTTPException(status_code=403, detail=err_msg)
            return user
        finally:
            cur.close()
            conn.close()

    return _dep
