"""Phụ thuộc dùng chung cho router: người dùng hiện tại, kiểm tra vai trò, phạm vi phòng, xác thực server biên."""

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.central.app.core.config import settings
from backend.central.app.core.db import get_db
from backend.central.app.core.security import decode_token
from backend.shared.security import same_secret
from backend.central.app.models import Role, User

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập")
    payload = decode_token(creds.credentials, "access")
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token không hợp lệ hoặc đã hết hạn")
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tài khoản không tồn tại hoặc đã bị khoá")
    return user


def require(*roles: str):
    """Dependency: chỉ cho phép các vai trò được liệt kê (ma trận quyền mục 11)."""
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bạn không có quyền thực hiện thao tác này")
        return user
    return checker


admin_only = require(Role.ADMIN)
operator_up = require(Role.ADMIN, Role.OPERATOR)
any_user = require(*Role.ALL)


def room_ids(user: User) -> set[int] | None:
    """None = mọi phòng (admin). Operator/Viewer chỉ thấy 'phòng được gán'."""
    return None if user.role == Role.ADMIN else {r.id for r in user.rooms}


def check_room(user: User, room_id: int | None):
    allowed = room_ids(user)
    if allowed is not None and room_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bạn không được gán phòng này")


def edge_auth(x_edge_id: str | None = Header(None), authorization: str = Header("")) -> str:
    """Xác thực server biên (X-Edge-Id + Bearer khoá chung). Trả edge_id."""
    expected = settings.edge_token_map().get(x_edge_id or "")
    token = authorization.removeprefix("Bearer ").strip()
    if not expected or not token or not same_secret(token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Server biên không hợp lệ")
    return x_edge_id


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
