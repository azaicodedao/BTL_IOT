"""Cảnh báo, quy tắc cảnh báo, audit log, xuất CSV, WebSocket cho dashboard."""

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.central.app.core.db import SessionLocal, get_db
from backend.central.app.core.deps import admin_only, any_user, check_room, client_ip, operator_up, room_ids
from backend.central.app.core.security import decode_token
from backend.central.app.models import Alert, AlertRule, AuditLog, Device, ExamSession, Telemetry, User
from backend.central.app.services import alert_service
from backend.central.app.services.audit import audit
from backend.central.app.services.hub import Client, hub

router = APIRouter(prefix="/api/v1", tags=["alerts, audit, export"])


# ======================================================================== cảnh báo

class AlertPatch(BaseModel):
    state: Literal["acked", "resolved"]


@router.get("/alerts")
def list_alerts(state: Optional[str] = None, room_id: Optional[int] = None, kind: Optional[str] = None,
                limit: int = 100, db=Depends(get_db), user=Depends(any_user)):
    q = db.query(Alert)
    allowed = room_ids(user)
    if allowed is not None:
        q = q.filter(Alert.room_id.in_(allowed or [-1]))
    if state == "active":
        q = q.filter(Alert.state != "resolved")
    elif state:
        q = q.filter(Alert.state == state)
    if room_id:
        q = q.filter(Alert.room_id == room_id)
    if kind:
        q = q.filter(Alert.kind == kind)
    return [alert_service.alert_dict(a) for a in q.order_by(Alert.id.desc()).limit(min(limit, 1000))]


@router.patch("/alerts/{alert_id}", summary="Xác nhận / xử lý cảnh báo")
def patch_alert(alert_id: int, body: AlertPatch, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "Không có cảnh báo")
    check_room(user, a.room_id)
    a.state, a.acked_by = body.state, user.id
    if body.state == "resolved":
        a.resolved_at = datetime.now(timezone.utc)
    audit(db, user, f"alert.{body.state}", f"alert:{a.id}", client_ip(request))
    db.commit()
    out = alert_service.alert_dict(a)
    hub.publish_threadsafe("alert.updated", out, room_id=a.room_id)
    return out


# ======================================================================== quy tắc

class RuleIn(BaseModel):
    metric: Literal["temperature_c", "humidity_pct"]
    operator: Literal[">", "<", ">=", "<="]
    threshold: float
    duration_s: int = Field(0, ge=0, le=3600)
    severity: Literal["low", "medium", "high"] = "medium"
    enabled: bool = True
    message: Optional[str] = Field(None, max_length=200)


def rule_dict(r: AlertRule) -> dict:
    return {"id": r.id, "metric": r.metric, "operator": r.operator, "threshold": r.threshold,
            "duration_s": r.duration_s, "severity": r.severity, "enabled": r.enabled, "message": r.message}


@router.get("/alert-rules")
def list_rules(db=Depends(get_db), _=Depends(any_user)):
    return [rule_dict(r) for r in db.query(AlertRule).order_by(AlertRule.id)]


@router.post("/alert-rules", status_code=201)
def create_rule(body: RuleIn, request: Request, db=Depends(get_db), admin=Depends(admin_only)):
    r = AlertRule(**body.model_dump())
    db.add(r)
    db.flush()
    audit(db, admin, "rule.create", f"rule:{r.id}", client_ip(request), **body.model_dump())
    db.commit()
    return rule_dict(r)


@router.put("/alert-rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, request: Request, db=Depends(get_db), admin=Depends(admin_only)):
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(404, "Không có quy tắc")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    audit(db, admin, "rule.update", f"rule:{r.id}", client_ip(request), **body.model_dump())
    db.commit()
    return rule_dict(r)


# ======================================================================== audit, export

@router.get("/audit-logs")
def audit_logs(user_id: Optional[int] = None, action: Optional[str] = None, limit: int = 200,
               db=Depends(get_db), _=Depends(admin_only)):
    q = db.query(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action.ilike(f"{action}%"))
    return [{"id": a.id, "user_id": a.user_id, "user_email": email, "action": a.action, "target": a.target,
             "ip": a.ip, "ts": a.ts, "detail": a.detail}
            for a, email in q.order_by(AuditLog.id.desc()).limit(min(limit, 2000))]


@router.get("/export/telemetry.csv")
def export_telemetry(request: Request, device_id: str, frm: Optional[datetime] = Query(None, alias="from"),
                     to: Optional[datetime] = None, db=Depends(get_db), user=Depends(operator_up)):
    d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
    if d is None:
        raise HTTPException(404, "Không có thiết bị")
    check_room(user, d.room_id)
    to = to or datetime.now(timezone.utc)
    frm = frm or to - timedelta(days=1)
    rows = (db.query(Telemetry).filter(Telemetry.device_id == d.id, Telemetry.ts.between(frm, to))
            .order_by(Telemetry.ts).all())
    audit(db, user, "export.telemetry", device_id, client_ip(request), rows=len(rows))
    db.commit()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts_utc", "device_id", "msg_id", "temperature_c", "humidity_pct"])
    for r in rows:
        w.writerow([r.ts.isoformat(), device_id, r.msg_id, r.temperature_c, r.humidity_pct])
    name = f"telemetry_{device_id}_{frm:%Y%m%d%H%M}_{to:%Y%m%d%H%M}.csv"
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ======================================================================== WebSocket

async def _ws(websocket: WebSocket, token: str, kind: str | None = None, session_id: int | None = None):
    payload = decode_token(token or "", "access")
    with SessionLocal() as db:
        user = db.get(User, int(payload["sub"])) if payload else None
        if user is None or user.status != "active":
            await websocket.close(code=4401)
            return
        rooms = room_ids(user)
        if kind:
            s = db.get(ExamSession, session_id)
            if s is None or (rooms is not None and s.room_id not in rooms):
                await websocket.close(code=4403)
                return
    await websocket.accept()
    client = Client(websocket, rooms, kind, session_id)
    await hub.add(client)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.remove(client)


@router.websocket("/ws")
async def ws_all(websocket: WebSocket, token: str = ""):
    await _ws(websocket, token)


@router.websocket("/ws/exam/{session_id}")
async def ws_exam(websocket: WebSocket, session_id: int, token: str = ""):
    await _ws(websocket, token, "exam", session_id)
