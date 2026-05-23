import os
import uuid
from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "planning-system-jwt-secret-2024-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# pbkdf2_sha256: pure-Python, no 72-byte limit, no C extension required.
# bcrypt kept as deprecated so existing bcrypt hashes can still be verified.
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    default="pbkdf2_sha256",
    deprecated=["bcrypt"],
)


def hash_password(password: str) -> str:
    """Hash a plain-text password. Never pass an existing hash here."""
    if not isinstance(password, str):
        raise TypeError("password must be str")
    if password.startswith(("$pbkdf2", "$2b$", "$2a$", "$2y$")):
        raise ValueError("hash_password received an already-hashed value")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify plain password against a stored pbkdf2 or bcrypt hash."""
    if not plain or not hashed:
        return False
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, email: str, is_admin: bool):
    """Returns (token, jti)."""
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    jti = str(uuid.uuid4())
    token = jwt.encode(
        {"sub": str(user_id), "email": email, "is_admin": is_admin, "exp": expire, "jti": jti},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token, jti


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def is_jti_revoked(jti: str) -> bool:
    """Returns True if this token JTI was explicitly revoked (logout)."""
    try:
        from db import get_connection  # local import to avoid circular dep at module level
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT revoked_at FROM user_sessions WHERE token_jti = %s",
                (jti,)
            )
            row = cur.fetchone()
            # Token not in table = issued before session tracking — allow it
            return row is not None and row[0] is not None
        finally:
            cur.close()
            conn.close()
    except Exception:
        return False  # DB error must not block auth
