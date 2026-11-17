from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.central.app.core.db import get_db
from backend.central.app.core.deps import client_ip, current_user
from backend.central.app.core.security import (create_access_token, create_refresh_token, decode_token, hash_password,
                                   verify_password)
from backend.central.app.models import RevokedToken, User
from backend.central.app.services.audit import audit

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8)


def user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "status": u.status,
            "rooms": [{"id": r.id, "code": r.code, "name": r.name} for r in u.rooms], "created_at": u.created_at}


def _tokens(u: User) -> dict:
    refresh, _ = create_refresh_token(u.id)
    return {"access_token": create_access_token(u.id, u.role), "refresh_token": refresh,
            "token_type": "bearer", "user": user_dict(u)}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db=Depends(get_db)):
    u = db.query(User).filter(User.email == body.email.strip().lower()).one_or_none()
    if u is None or not verify_password(body.password, u.password_hash):
        audit(db, None, "auth.login_failed", body.email, client_ip(request))
        db.commit()
        raise HTTPException(401, "Sai email hoặc mật khẩu")
    if u.status != "active":
        raise HTTPException(403, "Tài khoản đã bị khoá")
    audit(db, u, "auth.login", u.email, client_ip(request))
    db.commit()
    return _tokens(u)


@router.post("/auth/refresh")
def refresh(body: RefreshIn, db=Depends(get_db)):
    p = decode_token(body.refresh_token, "refresh")
    if p is None or db.get(RevokedToken, p["jti"]):
        raise HTTPException(401, "Refresh token không hợp lệ")
    u = db.get(User, int(p["sub"]))
    if u is None or u.status != "active":
        raise HTTPException(401, "Tài khoản không khả dụng")
    db.add(RevokedToken(jti=p["jti"]))                 # xoay vòng: mỗi refresh token chỉ dùng 1 lần
    db.commit()
    return _tokens(u)


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshIn, request: Request, db=Depends(get_db), user: User = Depends(current_user)):
    p = decode_token(body.refresh_token, "refresh")
    if p and not db.get(RevokedToken, p["jti"]):
        db.add(RevokedToken(jti=p["jti"]))
    audit(db, user, "auth.logout", user.email, client_ip(request))
    db.commit()


@router.get("/users/me")
def me(user: User = Depends(current_user)):
    return user_dict(user)


@router.patch("/users/me/password", status_code=204)
def change_password(body: PasswordIn, request: Request, db=Depends(get_db), user: User = Depends(current_user)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Mật khẩu cũ không đúng")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "user.change_password", user.email, client_ip(request))
    db.commit()
