"""
Lệnh điều khiển có xác nhận (FR7, mục 8.3), thực hiện hoàn toàn tại biên để độ trễ không phụ thuộc Internet:
pending -> sent -> acked | failed | timeout. Chờ ACK tối đa 5s, gửi lại tối đa 2 lần,
lệnh có expires_at để thiết bị không thực thi lệnh cũ sau khi kết nối lại.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import Command, Device, DeviceState as S
from backend.edge.app.services import uplink
from backend.edge.app.services.device_service import room_devices
from backend.edge.app.services.mqtt_bridge import bridge, topic
from backend.shared.db import utcnow

log = logging.getLogger("commands")

BUZZER_PATTERNS = {"once", "continuous", "stop"}
_ack_events: dict[str, asyncio.Event] = {}
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop):
    global _loop
    _loop = loop


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if dt else None


def command_dict(c: Command, device: Device) -> dict:
    return {"id": c.id, "cmd_id": c.cmd_id, "device_id": device.device_id, "room_code": device.room_code,
            "action": c.action, "params": c.params, "status": c.status, "attempts": c.attempts,
            "issued_by": c.issued_by, "result": c.result, "ts_created": _iso(c.ts_created),
            "ts_sent": _iso(c.ts_sent), "ts_ack": _iso(c.ts_ack), "expires_at": _iso(c.expires_at)}


def _notify(db, c: Command, d: Device):
    uplink.emit(db, "command.updated", command_dict(c, d), ttl_s=60)


def create(db, device: Device, action: str, params: dict, issued_by: str) -> Command:
    """Tạo lệnh (chưa gửi). Gọi schedule(cmd_id) SAU khi commit."""
    if device.type != "actuator":
        raise HTTPException(400, "Chỉ gửi lệnh tới node chấp hành (ESP8266)")
    if device.state not in (S.ACTIVE, S.OFFLINE, S.FAULT):
        raise HTTPException(409, f"Thiết bị đang ở trạng thái {device.state}, không nhận lệnh")
    if action == "buzzer" and params.get("pattern") not in BUZZER_PATTERNS:
        raise HTTPException(422, "pattern phải là once | continuous | stop")
    if action == "display" and not str(params.get("text", "")).strip():
        raise HTTPException(422, "display cần text")
    if action not in ("buzzer", "display"):
        raise HTTPException(422, "action phải là buzzer | display")

    cmd = Command(cmd_id=f"c-{secrets.token_hex(4)}", device_id=device.id, action=action, params=params,
                  issued_by=issued_by, expires_at=utcnow() + timedelta(seconds=settings.command_ttl_s))
    db.add(cmd)
    db.flush()
    return cmd


def schedule(cmd_id: str):
    """Gửi lệnh ở nền. An toàn khi gọi từ luồng khác."""
    if _loop is None:
        return
    try:
        here = asyncio.get_running_loop() is _loop
    except RuntimeError:
        here = False
    if here:
        _loop.create_task(_dispatch(cmd_id))
    else:
        asyncio.run_coroutine_threadsafe(_dispatch(cmd_id), _loop)


def _send_attempt(cmd_id: str, attempt: int) -> bool:
    """Publish 1 lần. Trả False nếu lệnh đã kết thúc (acked/failed/hết hạn) và không cần chờ ACK nữa."""
    with session_scope() as db:
        c = db.query(Command).filter(Command.cmd_id == cmd_id).one()
        if c.status in ("acked", "failed"):
            return False
        d = db.get(Device, c.device_id)
        if utcnow() > c.expires_at:
            c.status, c.result = "timeout", "expired"
            _notify(db, c, d)
            return False
        payload = {"cmd_id": c.cmd_id, "action": c.action, **(c.params or {}),
                   "expires_at": c.expires_at.isoformat(timespec="seconds").replace("+00:00", "Z")}
        ok = bridge.publish(topic(d.room_code, d.device_id, "cmd"), payload, qos=1)
        c.status, c.attempts, c.ts_sent = "sent", attempt, utcnow()
        if not ok:
            c.result = "broker_unavailable"
        _notify(db, c, d)
        return True


def _mark_no_ack(cmd_id: str):
    with session_scope() as db:
        c = db.query(Command).filter(Command.cmd_id == cmd_id).one()
        if c.status == "sent":
            c.status, c.result = "timeout", "no_ack"
            _notify(db, c, db.get(Device, c.device_id))


async def _dispatch(cmd_id: str):
    from starlette.concurrency import run_in_threadpool

    event = _ack_events.setdefault(cmd_id, asyncio.Event())
    try:
        for attempt in range(1, settings.command_max_retries + 2):
            if not await run_in_threadpool(_send_attempt, cmd_id, attempt):
                return
            try:
                await asyncio.wait_for(event.wait(), settings.command_ack_timeout_s)
                return                                             # on_ack đã cập nhật trạng thái
            except asyncio.TimeoutError:
                log.warning("Lệnh %s chưa có ACK (lần %d)", cmd_id, attempt)
        await run_in_threadpool(_mark_no_ack, cmd_id)
    finally:
        _ack_events.pop(cmd_id, None)


def on_ack(room_code, device_id, payload):
    cmd_id = str(payload.get("cmd_id", ""))
    with session_scope() as db:
        c = db.query(Command).filter(Command.cmd_id == cmd_id).one_or_none()
        d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
        if c is None or d is None or c.device_id != d.id or c.status in ("acked", "failed"):
            return                                                 # lệnh lạ hoặc ACK lặp (QoS 1)
        result = str(payload.get("result", "ok"))[:60]
        c.status = "acked" if result in ("ok", "duplicate") else "failed"
        c.result, c.ts_ack = result, utcnow()
        _notify(db, c, d)
    ev = _ack_events.get(cmd_id)
    if ev and _loop:
        _loop.call_soon_threadsafe(ev.set)


def send(db, device: Device, action: str, params: dict, issued_by: str) -> dict:
    """Tạo + commit + gửi 1 lệnh. Trả command_dict."""
    cmd = create(db, device, action, params, issued_by)
    out = command_dict(cmd, device)
    db.commit()
    schedule(cmd.cmd_id)
    return out


def room_buzzer(db, room_code: str, pattern: str, issued_by: str) -> list[str]:
    """Tạo lệnh còi cho mọi node chấp hành trong phòng. Gọi schedule() cho từng cmd_id SAU khi commit."""
    return [create(db, d, "buzzer", {"pattern": pattern}, issued_by).cmd_id
            for d in room_devices(db, room_code, "actuator") if d.state in (S.ACTIVE, S.OFFLINE, S.FAULT)]


def buzz_room(room_code: str, pattern: str, issued_by: str, auto_stop_s: float | None = None) -> list[str]:
    """Bật/tắt còi cả phòng ngay (dùng cho AI cho vào phòng, giám thị xác nhận gian lận)."""
    with session_scope() as db:
        ids = room_buzzer(db, room_code, pattern, issued_by)
    for cid in ids:
        schedule(cid)
    if ids and pattern == "continuous" and auto_stop_s and _loop:
        # Còi liên tục tự tắt nếu giám thị chưa bấm tắt (mục 3.6)
        _loop.call_soon_threadsafe(
            lambda: _loop.call_later(auto_stop_s, lambda: _loop.run_in_executor(
                None, buzz_room, room_code, "stop", "auto-stop")))
    return ids


def history(db, device: Device, limit: int = 50) -> list[dict]:
    q = db.query(Command).filter(Command.device_id == device.id).order_by(Command.id.desc()).limit(min(limit, 500))
    return [command_dict(c, device) for c in q]
