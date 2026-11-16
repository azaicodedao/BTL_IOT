"""Xác thực tại server biên: quản trị viên bảng điều khiển, vé do trung tâm ký, token dịch vụ của worker AI."""

import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.edge.app.core.config import settings
from backend.shared.db import utcnow
from backend.shared.security import ALGORITHM, same_secret, verify_ticket

bearer = HTTPBearer(auto_error=False)

LOGIN_MAX_FAILS = 5              # sai quá 5 lần / phút từ 1 IP -> tạm chặn
_fails: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def login(password: str, ip: str | None) -> str:
    """Đăng nhập bảng điều khiển biên bằng mật khẩu cục bộ. Trả JWT. Raises 401 / 429."""
    recent = _fails[ip or "?"]
    while recent and time.time() - recent[0] > 60:
        recent.popleft()
    if len(recent) >= LOGIN_MAX_FAILS:
        raise HTTPException(429, "Sai mật khẩu quá nhiều lần, thử lại sau 1 phút")
    if not same_secret(password, settings.admin_password):
        recent.append(time.time())
        raise HTTPException(401, "Sai mật khẩu")
    recent.clear()
    return jwt.encode({"type": "console", "exp": utcnow() + timedelta(hours=settings.console_token_hours)},
                      settings.jwt_secret, algorithm=ALGORITHM)


def _is_console_token(token: str | None) -> bool:
    try:
        return jwt.decode(token or "", settings.jwt_secret, algorithms=[ALGORITHM]).get("type") == "console"
    except JWTError:
        return False


def console_admin(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if creds is None or not _is_console_token(creds.credentials):
        raise HTTPException(401, "Chưa đăng nhập bảng điều khiển biên")
    return "console"


def viewer(scope: str, token: str = "", ticket: str = "") -> str:
    """Cho phép quản trị viên biên (?token=) hoặc người dùng trung tâm mang vé đúng phạm vi (?ticket=).

    Dùng query thay vì header vì <img src> / tải tệp trực tiếp từ trình duyệt không gắn được header.
    """
    if _is_console_token(token):
        return "console"
    payload = verify_ticket(settings.central_token, ticket, scope)
    if payload is None:
        raise HTTPException(401, "Vé không hợp lệ hoặc đã hết hạn")
    return f"user:{payload.get('sub')}"


def service_only(x_service_token: str | None = Header(None)) -> str:
    """API nội bộ cho worker AI chạy cùng máy."""
    if not x_service_token or not same_secret(x_service_token, settings.service_token):
        raise HTTPException(401, "Sai service token")
    return "worker"
