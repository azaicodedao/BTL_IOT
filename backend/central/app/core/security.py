import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from backend.central.app.core.config import settings


# ---------------------------------------------------------------- mật khẩu người dùng

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- JWT

def _token(sub: str, kind: str, ttl: timedelta, extra: dict | None = None) -> tuple[str, str]:
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": kind, "jti": jti, "iat": now, "exp": now + ttl, **(extra or {})}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti


def create_access_token(user_id: int, role: str) -> str:
    return _token(str(user_id), "access", timedelta(minutes=settings.access_token_minutes), {"role": role})[0]


def create_refresh_token(user_id: int) -> tuple[str, str]:
    return _token(str(user_id), "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(token: str, kind: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    return payload if payload.get("type") == kind else None
