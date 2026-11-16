"""Cảnh báo (FR6): nhiệt độ/độ ẩm vượt ngưỡng, thiết bị offline/lỗi, nghi vấn gian lận."""

from datetime import datetime, timezone

from backend.central.app.models import Alert
from backend.central.app.services.hub import hub

# Các loại chỉ có tối đa 1 cảnh báo đang mở cho mỗi thiết bị (tránh spam mỗi 30 giây)
SINGLETON_KINDS = {"device_offline", "device_fault", "temperature_c", "humidity_pct"}


def alert_dict(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "state": a.state,
            "room_id": a.room_id, "device_id": a.device_id, "event_id": a.event_id, "rule_id": a.rule_id,
            "acked_by": a.acked_by, "ts": a.ts, "resolved_at": a.resolved_at}


def create(db, *, kind: str, severity: str, message: str, room_id=None, device_id=None,
           event_id=None, rule_id=None) -> Alert | None:
    if kind in SINGLETON_KINDS:
        # == None sinh ra "= NULL" (luôn sai) -> phải dùng IS NULL
        same_rule = Alert.rule_id.is_(None) if rule_id is None else Alert.rule_id == rule_id
        same_dev = Alert.device_id.is_(None) if device_id is None else Alert.device_id == device_id
        exists = db.query(Alert).filter(Alert.kind == kind, same_dev, same_rule, Alert.state != "resolved").first()
        if exists:
            return None
    a = Alert(kind=kind, severity=severity, message=message[:300], room_id=room_id, device_id=device_id,
              event_id=event_id, rule_id=rule_id)
    db.add(a)
    db.flush()
    hub.publish_threadsafe("alert.created", alert_dict(a), room_id=room_id)
    return a


def resolve(db, kind: str, *, device_id=None, rule_id=None):
    """Tự đóng cảnh báo khi điều kiện hết (thiết bị online lại, nhiệt độ về ngưỡng)."""
    q = db.query(Alert).filter(Alert.kind == kind, Alert.device_id == device_id, Alert.state != "resolved")
    if rule_id is not None:
        q = q.filter(Alert.rule_id == rule_id)
    for a in q.all():
        a.state, a.resolved_at = "resolved", datetime.now(timezone.utc)
        hub.publish_threadsafe("alert.updated", alert_dict(a), room_id=a.room_id)
