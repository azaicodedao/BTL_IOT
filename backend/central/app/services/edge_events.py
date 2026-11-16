"""
Xử lý sự kiện server biên đẩy lên (POST /api/v1/edge/events). Mỗi sự kiện: {"id", "type", "ts", "data"}.

  device.upsert     bản phản chiếu thiết bị + cảnh báo offline / lỗi cảm biến
  device.event      sự kiện cục bộ của thiết bị (chỉ chuyển tiếp cho dashboard)
  telemetry         lưu chuỗi thời gian, đánh giá quy tắc cảnh báo -> trả khuyến nghị cho LCD
  command.updated   tiến trình lệnh (chuyển tiếp cho dashboard, suy ra còi đang bật / tắt)
  checkin.admitted  AI tại biên cho SV vào phòng     checkin.rejected  lượt bị từ chối     checkin.seen  đang xác nhận
  exam.suspicion    nghi vấn gian lận -> sự kiện + cảnh báo chờ giám thị duyệt

Biên gửi "ít nhất 1 lần": loại trùng theo (edge_id, event_id). Sự kiện không bao giờ chứa ảnh, chỉ có frame_ref.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from backend.central.app.models import (AlertRule, CheckinAttempt, CheckinRecord, Device, DeviceState as S, EdgeEvent,
                                ExamPhase, ExamSession, Room, Student, SuspicionEvent, Telemetry)
from backend.central.app.services import alert_service
from backend.central.app.services.hub import hub
from backend.shared.db import utcnow

log = logging.getLogger("edge_events")

_breach_since: dict[tuple[int, int], datetime] = {}                      # (device, rule) -> lúc bắt đầu vượt ngưỡng
_OPS = {">": float.__gt__, "<": float.__lt__, ">=": float.__ge__, "<=": float.__le__}
_SUSPICION_LABEL = {"phone": "dùng điện thoại", "head_turn": "quay đầu nhiều"}


def _ts(raw) -> datetime:
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return utcnow()


def get_or_create_room(db, code: str | None) -> Room | None:
    """Phòng do biên báo lên mà trung tâm chưa có -> tự tạo (quản trị viên đặt lại tên sau)."""
    if not code:
        return None
    room = db.query(Room).filter(Room.code == code).one_or_none()
    if room is None:
        room = Room(code=code, name=f"Phòng {code}")
        db.add(room)
        db.flush()
    return room


def device_dict(d: Device) -> dict:
    return {"id": d.id, "device_id": d.device_id, "edge_id": d.edge_id, "type": d.type, "state": d.state,
            "online": bool(d.online), "room_id": d.room_id, "room_code": d.room.code if d.room else None,
            "fw_version": d.fw_version, "hw_version": d.hw_version, "rssi": d.rssi, "last_seen": d.last_seen,
            "config": d.config or {}}


# ======================================================================== thiết bị

def on_device_upsert(db, edge_id: str, data: dict, ts):
    d = db.query(Device).filter(Device.device_id == data["device_id"]).one_or_none()
    if d is None:
        d = Device(device_id=data["device_id"], type=data["type"])
        db.add(d)
    before = d.state
    room = get_or_create_room(db, data.get("room_code"))
    d.edge_id, d.type, d.room, d.room_id = edge_id, data["type"], room, room.id if room else None
    d.state, d.online = data["state"], bool(data.get("online"))
    d.fw_version, d.hw_version, d.rssi = data.get("fw_version"), data.get("hw_version"), data.get("rssi")
    d.config = data.get("config") or {}
    if data.get("last_seen"):
        d.last_seen = _ts(data["last_seen"])
    db.flush()

    # Cảnh báo suy ra từ chuyển trạng thái (FR6): offline / lỗi cảm biến; tự đóng khi hết
    if d.state == S.OFFLINE and before != S.OFFLINE:
        alert_service.create(db, kind="device_offline", severity="high", room_id=d.room_id, device_id=d.id,
                             message=f"{d.device_id} mất kết nối")
    elif d.state != S.OFFLINE:
        alert_service.resolve(db, "device_offline", device_id=d.id)
    if d.state == S.FAULT and before != S.FAULT:
        alert_service.create(db, kind="device_fault", severity="medium", room_id=d.room_id, device_id=d.id,
                             message=f"{d.device_id}: lỗi cảm biến liên tục")
    elif d.state != S.FAULT:
        alert_service.resolve(db, "device_fault", device_id=d.id)
    hub.publish_threadsafe("device.status", device_dict(d), room_id=d.room_id)


def on_device_event(db, edge_id, data, ts):
    room = db.query(Room).filter(Room.code == data.get("room_code")).one_or_none()
    hub.publish_threadsafe("device.event", data, room_id=room.id if room else None)


def on_command_updated(db, edge_id, data, ts):
    room = db.query(Room).filter(Room.code == data.get("room_code")).one_or_none()
    room_id = room.id if room else None
    hub.publish_threadsafe("command.updated", data, room_id=room_id)
    pattern = (data.get("params") or {}).get("pattern")
    if data.get("action") == "buzzer" and data.get("status") == "acked" and pattern in ("continuous", "stop"):
        hub.publish_threadsafe("exam.alarm_on" if pattern == "continuous" else "exam.alarm_off",
                               {"device_id": data.get("device_id"), "cmd_id": data.get("cmd_id")}, room_id=room_id)


# ======================================================================== telemetry + quy tắc cảnh báo

def evaluate_rules(db, d: Device, values: dict, ts: datetime) -> list[str]:
    """Trả về danh sách khuyến nghị đang có hiệu lực (hiển thị LCD + dashboard)."""
    advice = []
    for rule in db.query(AlertRule).filter(AlertRule.enabled.is_(True), AlertRule.metric.in_(list(values))).all():
        v, key = values[rule.metric], (d.id, rule.id)
        if v is not None and _OPS.get(rule.operator, float.__gt__)(v, rule.threshold):
            first = _breach_since.setdefault(key, ts)
            if ts - first >= timedelta(seconds=rule.duration_s or 0):
                unit = "°C" if rule.metric == "temperature_c" else "%RH"
                alert_service.create(
                    db, kind=rule.metric, severity=rule.severity, room_id=d.room_id, device_id=d.id, rule_id=rule.id,
                    message=f"{d.room.code}: {v:.1f}{unit} {rule.operator} {rule.threshold:g}{unit}. "
                            f"{rule.message or ''}".strip())
                if rule.message:
                    advice.append(rule.message)
        elif _breach_since.pop(key, None) is not None:
            alert_service.resolve(db, rule.metric, device_id=d.id, rule_id=rule.id)
    return advice


def on_telemetry(db, edge_id, data, ts):
    d = db.query(Device).filter(Device.device_id == data.get("device_id")).one_or_none()
    if d is None or d.room is None:
        return None
    msg_id, when = str(data["msg_id"])[:40], _ts(data.get("ts"))
    temp, hum = float(data["temperature_c"]), float(data["humidity_pct"])
    # UNIQUE(device_id, msg_id): bản tin QoS 1 lặp không tạo dòng thứ hai
    if db.query(Telemetry.id).filter_by(device_id=d.id, msg_id=msg_id).first():
        return None
    db.add(Telemetry(device_id=d.id, ts=when, msg_id=msg_id, temperature_c=temp, humidity_pct=hum))
    db.flush()
    advice = evaluate_rules(db, d, {"temperature_c": temp, "humidity_pct": hum}, when)
    hub.publish_threadsafe("telemetry", {"device_id": d.device_id, "ts": when, "temperature_c": temp,
                                         "humidity_pct": hum, "advice": advice}, room_id=d.room_id)
    return {"advice": advice}


# ======================================================================== kết quả AI từ biên

def _open_exam(db, exam_id) -> ExamSession | None:
    s = db.get(ExamSession, int(exam_id))
    return s if s is not None and s.status != ExamPhase.CLOSED else None


def _event_exam(db, data, ts):
    """Accept delayed durable events recorded during a session, even if it closed while offline."""
    s = db.get(ExamSession, int(data["exam_id"]))
    if s is not None and s.status == ExamPhase.CLOSED:
        when = _ts(data.get("ts") or ts)
        if not s.ended_at or when > s.ended_at or when < s.started_at:
            return None
    return s


def on_checkin_admitted(db, edge_id, data, ts):
    from backend.central.app.routers.sessions import record_dict

    s = _event_exam(db, data, ts)
    st = db.get(Student, int(data["student_id"]))
    if s is None or st is None:
        return None
    r = db.query(CheckinRecord).filter_by(exam_id=s.id, student_id=st.id).first()
    if r is not None and (r.status != "absent" or r.method != "auto"):
        return None                                   # giữ quyết định thủ công và kết quả đã có
    if r is None:
        r = CheckinRecord(exam_id=s.id, student_id=st.id)
        db.add(r)
    r.ts, r.confidence = _ts(data.get("ts") or ts), data.get("confidence")
    r.method, r.status, r.edge_id, r.frame_ref = "auto", "admitted", edge_id, data.get("frame_ref")
    db.flush()
    db.refresh(r)
    hub.publish_threadsafe("checkin.admitted", record_dict(r), room_id=s.room_id, session_kind="exam", session_id=s.id)


def on_checkin_rejected(db, edge_id, data, ts):
    from backend.central.app.routers.sessions import attempt_dict

    s = _event_exam(db, data, ts)
    if s is None:
        return None
    a = CheckinAttempt(exam_id=s.id, result=data["result"], student_id=data.get("student_id"),
                       confidence=data.get("confidence"), ts=_ts(data.get("ts")), edge_id=edge_id,
                       frame_ref=data.get("frame_ref"))
    db.add(a)
    db.flush()
    db.refresh(a)
    hub.publish_threadsafe("checkin.rejected", attempt_dict(a), room_id=s.room_id, session_kind="exam", session_id=s.id)


def on_checkin_seen(db, edge_id, data, ts):
    s = _open_exam(db, data["exam_id"])
    if s is not None:
        hub.publish_threadsafe("checkin.seen", data, room_id=s.room_id, session_kind="exam", session_id=s.id)


def on_suspicion(db, edge_id, data, ts):
    from backend.central.app.routers.sessions import event_dict

    s = _event_exam(db, data, ts)
    if s is None:
        return None
    e = SuspicionEvent(exam_id=s.id, type=data["type"], score=float(data["score"]),
                       student_code=data.get("student_code"), detail=data.get("detail"), ts=_ts(data.get("ts")),
                       edge_id=edge_id, frame_ref=data.get("frame_ref"))
    db.add(e)
    db.flush()
    who = e.student_code or "chưa rõ danh tính"
    alert_service.create(db, kind="suspicion", severity=data.get("severity") or "high", room_id=s.room_id,
                         event_id=e.id, message=f"{s.room.code}: nghi vấn {_SUSPICION_LABEL.get(e.type, e.type)} "
                                                f"({who}), điểm {e.score:.2f}")
    hub.publish_threadsafe("exam.suspicion", event_dict(e, s.room_id), room_id=s.room_id,
                           session_kind="exam", session_id=s.id)


HANDLERS = {
    "device.upsert": on_device_upsert, "device.event": on_device_event, "command.updated": on_command_updated,
    "telemetry": on_telemetry, "checkin.admitted": on_checkin_admitted, "checkin.rejected": on_checkin_rejected,
    "checkin.seen": on_checkin_seen, "exam.suspicion": on_suspicion,
}


def process(db, edge_id: str, events: list[dict], acknowledged: list | None = None) -> dict:
    """Xử lý 1 lô theo thứ tự, mỗi sự kiện 1 giao dịch. Trả {event_id: kết quả} cho các sự kiện có phản hồi.

    Chỉ xác nhận sau commit, hoặc khi ID đã xử lý. Lỗi giữ lại ở biên để gửi lại.
    """
    results = {}
    for ev in events:
        event_id, handler = str(ev.get("id", ""))[:32], HANDLERS.get(ev.get("type"))
        if not event_id or handler is None:
            continue
        try:
            db.add(EdgeEvent(edge_id=edge_id, event_id=event_id))
            db.flush()
        except IntegrityError:
            db.rollback()
            if acknowledged is not None:
                acknowledged.append(event_id)
            continue                                  # đã xử lý ở lần gửi trước
        try:
            out = handler(db, edge_id, ev.get("data") or {}, ev.get("ts"))
            db.commit()
            if acknowledged is not None:
                acknowledged.append(event_id)
            if out is not None:
                results[event_id] = out
        except Exception:
            db.rollback()
            log.exception("Lỗi xử lý sự kiện %s từ %s", ev.get("type"), edge_id)
    return results


def prune(db, days: int = 2):
    db.query(EdgeEvent).filter(EdgeEvent.ts < utcnow() - timedelta(days=days)).delete()
