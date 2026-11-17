from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.central.app.core.db import get_db
from backend.central.app.core.deps import admin_only, client_ip
from backend.central.app.core.security import hash_password
from backend.central.app.models import Role, Room, User
from backend.central.app.routers.auth import user_dict
from backend.central.app.services.audit import audit

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserIn(BaseModel):
    email: str = Field(..., max_length=120)
    full_name: str = Field(..., max_length=120)
    password: str = Field(..., min_length=8)
    role: Literal["admin", "operator", "viewer"] = Role.VIEWER
    room_ids: list[int] = []


class UserPatch(BaseModel):
    full_name: Optional[str] = None
    role: Optional[Literal["admin", "operator", "viewer"]] = None
    status: Optional[Literal["active", "locked"]] = None
    room_ids: Optional[list[int]] = None
    password: Optional[str] = Field(None, min_length=8)


@router.get("")
def list_users(db=Depends(get_db), _=Depends(admin_only)):
    return [user_dict(u) for u in db.query(User).order_by(User.id)]


@router.post("", status_code=201)
def create_user(body: UserIn, request: Request, db=Depends(get_db), admin=Depends(admin_only)):
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Email đã tồn tại")
    u = User(email=email, full_name=body.full_name, role=body.role, password_hash=hash_password(body.password))
    u.rooms = db.query(Room).filter(Room.id.in_(body.room_ids)).all() if body.room_ids else []
    db.add(u)
    db.flush()
    audit(db, admin, "user.create", email, client_ip(request), role=body.role)
    db.commit()
    return user_dict(u)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, db=Depends(get_db), admin=Depends(admin_only)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Không có người dùng")
    if u.id == admin.id and (body.status == "locked" or (body.role and body.role != Role.ADMIN)):
        raise HTTPException(400, "Không thể tự khoá hoặc tự hạ quyền chính mình")
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    for k in ("full_name", "role", "status"):
        if k in changes:
            setattr(u, k, changes[k])
    if body.room_ids is not None:
        u.rooms = db.query(Room).filter(Room.id.in_(body.room_ids)).all()
    if body.password:
        u.password_hash = hash_password(body.password)
        changes["password"] = "reset"
    action = "user.lock" if body.status == "locked" else "user.unlock" if body.status == "active" else "user.update"
    audit(db, admin, action, u.email, client_ip(request), **changes)
    db.commit()
    return user_dict(u)
