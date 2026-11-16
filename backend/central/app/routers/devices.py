"""
Thiết bị nhìn từ trung tâm: bản phản chiếu trạng thái + cấu hình / lệnh chuyển xuống server biên + telemetry.

Đăng ký, gán phòng, cấp khoá, vòng đời (mục 10) thực hiện ở bảng điều khiển của server biên - nơi thiết bị tự báo danh.
Camera realtime: trung tâm chỉ cấp "vé", trình duyệt xem trực tiếp từ biên (ảnh không qua trung tâm).
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.central.app.core.db import get_db
from backend.central.app.core.deps import any_user, check_room, client_ip, operator_up, room_ids
from backend.central.app.models import Device, Edge, Telemetry
from backend.central.app.services import edge_link
from backend.central.app.services.audit import audit
from backend.central.app.services.edge_events import device_dict
from backend.shared.db import utcnow

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class ConfigIn(BaseModel):
    telemetry_interval_s: Optional[int] = Field(None, ge=5, le=3600)
    heartbeat_interval_s: Optional[int] = Field(None, ge=5, le=300)
    role: Optional[Literal["checkin", "exam", "both"]] = None          # camera ở cửa / trong phòng / cả hai
    stream_url: Optional[str] = Field(None, max_length=200)
    frame_interval_s: Optional[float] = Field(None, ge=0.1, le=60)


class CommandIn(BaseModel):
    action: Literal["buzzer", "display"]
    pattern: Optional[Literal["once", "continuous", "stop"]] = None
    text: Optional[str] = Field(None, max_length=32)


def _get(db, device_id: str, user=None) -> Device:
    d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
    if d is None:
        raise HTTPException(404, "Không có thiết bị")
    if user is not None:
        check_room(user, d.room_id)
    return d


@router.get("")
def list_devices(db=Depends(get_db), user=Depends(any_user)):
    q = db.query(Device).order_by(Device.device_id)
    allowed = room_ids(user)
    if allowed is not None:
        q = q.filter(Device.room_id.in_(allowed or [-1]))
    return [{**device_dict(d), "edge_online": edge_link.online(d.edge_id)} for d in q]


@router.put("/{device_id}/config", summary="Cấu hình từ xa (qua server biên -> publish retain lên .../config)")
def put_config(device_id: str, body: ConfigIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    d = _get(db, device_id, user)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Không có tham số nào")
    out = edge_link.call(d.edge_id, "config", {"device_id": d.device_id, "changes": changes,
                                               "actor": f"user:{user.id}"})
    audit(db, user, "device.config", d.device_id, client_ip(request), **changes)
    db.commit()
    return out


@router.post("/{device_id}/commands", status_code=202, summary="Gửi lệnh (còi once/continuous/stop, hiển thị)")
def send_command(device_id: str, body: CommandIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    d = _get(db, device_id, user)
    params = {"pattern": body.pattern} if body.action == "buzzer" else {"text": body.text}
    cmd = edge_link.call(d.edge_id, "command", {"device_id": d.device_id, "action": body.action, "params": params,
                                                "issued_by": f"user:{user.id}"})
    audit(db, user, "device.command", d.device_id, client_ip(request), cmd_id=cmd.get("cmd_id"), **params)
    db.commit()
    return cmd


@router.get("/{device_id}/commands", summary="Lịch sử lệnh (lưu tại server biên)")
def list_commands(device_id: str, limit: int = 50, db=Depends(get_db), user=Depends(any_user)):
    d = _get(db, device_id, user)
    return edge_link.call(d.edge_id, "commands", {"device_id": d.device_id, "limit": limit})


@router.get("/{device_id}/stream-ticket", summary="Vé xem camera realtime trực tiếp từ server biên (MJPEG)")
def stream_ticket(device_id: str, db=Depends(get_db), user=Depends(any_user)):
    d = _get(db, device_id, user)
    if d.type != "camera":
        raise HTTPException(400, "Không phải camera")
    edge = db.get(Edge, d.edge_id) if d.edge_id else None
    return {"url": edge_link.ticket_url(edge, f"/devices/{d.device_id}/stream", f"stream:{d.device_id}", user.id)}


# ---------------------------------------------------------------- telemetry

AGG_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@router.get("/{device_id}/telemetry", summary="Tra cứu / tổng hợp nhiệt độ, độ ẩm")
def telemetry(device_id: str, frm: Optional[datetime] = Query(None, alias="from"), to: Optional[datetime] = None,
              agg: Literal["raw", "1m", "5m", "15m", "1h"] = "raw", db=Depends(get_db), user=Depends(any_user)):
    d = _get(db, device_id, user)
    to = to or utcnow()
    frm = frm or to - timedelta(hours=6)
    rows = (db.query(Telemetry.ts, Telemetry.temperature_c, Telemetry.humidity_pct)
            .filter(Telemetry.device_id == d.id, Telemetry.ts.between(frm, to)).order_by(Telemetry.ts).limit(20000).all())
    if agg == "raw":
        return [{"ts": ts, "temperature_c": t, "humidity_pct": h} for ts, t, h in rows]
    # Gộp theo ô thời gian trong Python (tối đa 20000 dòng) để không phụ thuộc hàm riêng của PostgreSQL
    step, buckets = AGG_S[agg], defaultdict(list)
    for ts, t, h in rows:
        buckets[int(ts.timestamp()) // step * step].append((t, h))
    out = []
    for start in sorted(buckets):
        temps, hums = zip(*buckets[start])
        out.append({"ts": datetime.fromtimestamp(start, tz=utcnow().tzinfo),
                    "temperature_c": round(sum(temps) / len(temps), 2), "humidity_pct": round(sum(hums) / len(hums), 1),
                    "temp_min": min(temps), "temp_max": max(temps), "n": len(temps)})
    return out
