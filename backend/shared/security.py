"""Băm khoá, so sánh an toàn và "vé" ngắn hạn giữa server trung tâm và server biên.

Vé (ticket): ảnh thật không bao giờ đi qua server trung tâm. Khi người dùng cần xem camera / ảnh bằng chứng /
đăng ký khuôn mặt, trung tâm kiểm tra quyền rồi ký 1 vé JWT ngắn hạn bằng khoá chung của server biên đó;
trình duyệt mang vé tới thẳng server biên. Biên chỉ kiểm tra chữ ký + phạm vi (scope), không cần biết người dùng.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

ALGORITHM = "HS256"


def new_secret(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def same_secret(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def make_ticket(key: str, scope: str, subject: str, ttl_s: int = 300) -> str:
    """Ký vé. scope ví dụ: "stream:cam-01", "frame:123", "faces:45"."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"type": "ticket", "scope": scope, "sub": subject, "iat": now,
                       "exp": now + timedelta(seconds=ttl_s)}, key, algorithm=ALGORITHM)


def verify_ticket(key: str, ticket: str, scope: str) -> dict | None:
    """Trả payload nếu vé hợp lệ, chưa hết hạn và đúng scope; ngược lại None."""
    try:
        payload = jwt.decode(ticket or "", key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    return payload if payload.get("type") == "ticket" and payload.get("scope") == scope else None
