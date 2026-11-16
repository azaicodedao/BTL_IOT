"""
Vòng đời thiết bị (mục 10) + trạng thái online/offline (mục 8.3) tại server biên.

Registered --(gán phòng, thiết bị tự nhận khoá)--> Provisioned --(MQTT lần đầu)--> Active <-> Offline/Fault
Active/Offline -> Maintenance ; Any -> Suspended ; Any -> Decommissioned

Khoá KHÔNG bao giờ hiển thị cho người: thiết bị tự lấy qua kênh đăng ký (enrollment_service) bằng claim token.
Mọi thay đổi trạng thái được phản chiếu lên trung tâm qua sự kiện device.upsert.
"""

import logging
import time
from datetime import timedelta

from fastapi import HTTPException

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import Device, DeviceCredential, DeviceState as S, Enrollment, EnrollmentStatus
from backend.edge.app.services import uplink
from backend.edge.app.services.mqtt_bridge import bridge, topic
from backend.shared.db import utcnow
from backend.shared.security import new_secret, sha256

log = logging.getLogger("devices")

DEFAULT_CONFIG = {
    "camera": {"telemetry_interval_s": 30, "heartbeat_interval_s": 15, "camera_mode": "idle", "frame_interval_s": 0.2},
    "actuator": {"heartbeat_interval_s": 15},
}
SEND_TOPICS = ["telemetry", "heartbeat", "status", "event", "cmd/ack"]
RECV_TOPICS = ["cmd", "config", "display"]
HEARTBEAT_UPLINK_S = 30          # không đẩy mọi heartbeat lên trung tâm, chỉ làm mới last_seen/RSSI mỗi 30s

_ALL_LIVE = {S.REGISTERED, S.PROVISIONED, S.ACTIVE, S.OFFLINE, S.FAULT, S.MAINTENANCE}
# đích -> các trạng thái nguồn hợp lệ
MANUAL_TRANSITIONS = {
    S.MAINTENANCE: {S.ACTIVE, S.OFFLINE, S.FAULT},
    S.ACTIVE: {S.MAINTENANCE, S.SUSPENDED},              # thoát bảo trì / mở khoá
    S.SUSPENDED: _ALL_LIVE,
    S.DECOMMISSIONED: _ALL_LIVE | {S.SUSPENDED},
}
_last_uplink: dict[str, float] = {}


def device_dict(d: Device) -> dict:
    return {
        "device_id": d.device_id, "hardware_uid": d.hardware_uid, "type": d.type, "state": d.state,
        "online": bool(d.online), "room_code": d.room_code, "fw_version": d.fw_version, "hw_version": d.hw_version,
        "rssi": d.rssi, "last_seen": d.last_seen.isoformat() if d.last_seen else None, "config": d.config or {},
        "has_credentials": bool(d.credential and not d.credential.revoked_at), "edge_id": settings.edge_id,
    }


def notify(db, d: Device):
    """Phản chiếu trạng thái thiết bị lên trung tâm."""
    _last_uplink[d.device_id] = time.time()
    uplink.emit(db, "device.upsert", device_dict(d))


def get(db, device_id: str) -> Device:
    d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
    if d is None:
        raise HTTPException(404, "Không có thiết bị")
    return d


def room_devices(db, room_code: str, type_: str) -> list[Device]:
    return db.query(Device).filter(Device.room_code == room_code, Device.type == type_,
                                   Device.state.notin_([S.DECOMMISSIONED, S.SUSPENDED])).all()


def _enrollment(db, d: Device) -> Enrollment | None:
    return db.query(Enrollment).filter(Enrollment.device_id == d.device_id).one_or_none()


# ======================================================================== khoá

def _dynsec_role(d: Device) -> dict:
    base = f"classroom/{d.room_code}/{d.device_id}"
    return {"command": "createRole", "rolename": f"dev-{d.device_id}", "acls":
            [{"acltype": "publishClientSend", "topic": f"{base}/{t}", "allow": True} for t in SEND_TOPICS] +
            [{"acltype": "subscribePattern", "topic": f"{base}/{t}", "allow": True} for t in RECV_TOPICS]}


def _drop_broker_account(d: Device):
    """Xoá tài khoản + ACL trên broker (broker ngắt luôn phiên đang mở của thiết bị)."""
    bridge.dynsec({"command": "deleteClient", "username": d.device_id},
                  {"command": "deleteRole", "rolename": f"dev-{d.device_id}"})


def issue_credentials(db, d: Device) -> dict:
    """Sinh mật khẩu MQTT + token HTTP riêng, tạo tài khoản + ACL trên broker, chỉ lưu bản băm.

    Chỉ được gọi khi chính thiết bị (đã chứng minh bằng claim token) tới lấy khoá.
    Raises: RuntimeError nếu broker không phản hồi.
    """
    mqtt_password, http_token = new_secret(), new_secret(32)
    _drop_broker_account(d)
    bridge.dynsec(_dynsec_role(d),
                  {"command": "createClient", "username": d.device_id, "password": mqtt_password,
                   "roles": [{"rolename": f"dev-{d.device_id}"}]})

    if d.credential is None:
        d.credential = DeviceCredential(mqtt_user=d.device_id, secret_hash=sha256(mqtt_password))
    else:
        d.credential.secret_hash, d.credential.revoked_at = sha256(mqtt_password), None
        d.credential.created_at = utcnow()
    d.token_hash = sha256(http_token) if d.type == "camera" else None
    d.config = {**DEFAULT_CONFIG[d.type], **(d.config or {})}
    d.state, d.online = S.PROVISIONED, False
    db.flush()

    base = f"classroom/{d.room_code}/{d.device_id}"
    bridge.publish(f"{base}/config", d.config, qos=1, retain=True)      # thiết bị nhận ngay khi kết nối lần đầu
    notify(db, d)
    return {
        "device_id": d.device_id, "room_code": d.room_code,
        "mqtt": {"host": settings.public_host, "port": settings.mqtt_port, "client_id": d.device_id,
                 "username": d.device_id, "password": mqtt_password},
        "topics": {"base": base, "telemetry": f"{base}/telemetry", "heartbeat": f"{base}/heartbeat",
                   "status": f"{base}/status", "event": f"{base}/event", "cmd": f"{base}/cmd",
                   "ack": f"{base}/cmd/ack", "config": f"{base}/config", "display": f"{base}/display"},
        "http": {"frames_url": f"{settings.public_url}/api/v1/devices/{d.device_id}/frames",
                 "token": http_token} if d.type == "camera" else None,
        "config": d.config,
    }


def _revoke(db, d: Device):
    """Thu hồi khoá hiện tại. Thiết bị bị ngắt MQTT, tự quay lại kênh đăng ký để xin khoá mới (nếu còn được phép)."""
    _drop_broker_account(d)
    if d.room_code:                                   # dọn bản tin retain ở topic cũ
        for kind in ("config", "display"):
            bridge.publish(topic(d.room_code, d.device_id, kind), "", qos=1, retain=True)
    if d.credential:
        d.credential.revoked_at = utcnow()
    d.token_hash, d.online = None, False


def assign_room(db, d: Device, room_code: str):
    """Gán / đổi phòng. Topic và ACL phụ thuộc phòng nên khoá cũ bị thu hồi; thiết bị tự nhận khoá mới."""
    if d.state in (S.DECOMMISSIONED, S.SUSPENDED):
        raise HTTPException(409, f"Thiết bị đang ở trạng thái {d.state}")
    old_room = d.room_code
    _revoke(db, d)
    d.room_code, d.state = room_code, S.REGISTERED
    enr = _enrollment(db, d)
    if enr:
        enr.status = EnrollmentStatus.APPROVED
    db.flush()
    notify(db, d)
    return old_room


def rekey(db, d: Device):
    """Cấp lại khoá: thu hồi khoá hiện tại, thiết bị tự nhận khoá mới ở lần hỏi kế tiếp."""
    if not d.room_code:
        raise HTTPException(409, "Thiết bị chưa được gán phòng")
    assign_room(db, d, d.room_code)


def transition(db, d: Device, to: str):
    allowed_from = MANUAL_TRANSITIONS.get(to)
    if allowed_from is None or d.state not in allowed_from:
        raise HTTPException(409, f"Không thể chuyển {d.state} -> {to}")

    if to == S.SUSPENDED:
        bridge.dynsec({"command": "disableClient", "username": d.device_id})     # ngắt phiên hiện có
        d.online = False
    elif to == S.DECOMMISSIONED:
        _revoke(db, d)
        enr = _enrollment(db, d)
        if enr:
            enr.status = EnrollmentStatus.REJECTED
    elif d.state == S.SUSPENDED:
        bridge.dynsec({"command": "enableClient", "username": d.device_id})

    if to == S.ACTIVE and not d.online:
        # thoát bảo trì / mở khoá: chờ heartbeat thật mới coi là Active
        has_key = d.credential and not d.credential.revoked_at
        to = S.OFFLINE if d.last_seen and has_key else S.PROVISIONED if has_key else S.REGISTERED
    d.state = to
    db.flush()
    notify(db, d)


def delete(db, d: Device):
    """Xoá hẳn bản ghi (chỉ khi đã thu hồi / chưa cấp khoá) để phần cứng có thể đăng ký lại từ đầu."""
    if d.state not in (S.DECOMMISSIONED, S.REGISTERED):
        raise HTTPException(409, "Chỉ xoá được thiết bị đã thu hồi hoặc chưa cấp khoá")
    if d.state != S.DECOMMISSIONED:
        _revoke(db, d)
        d.state = S.DECOMMISSIONED
    notify(db, d)
    db.query(Enrollment).filter(Enrollment.device_id == d.device_id).delete()
    db.delete(d)


def push_config(db, d: Device, changes: dict):
    d.config = {**(d.config or {}), **changes}
    if d.room_code and d.credential and not d.credential.revoked_at:
        bridge.publish(topic(d.room_code, d.device_id, "config"), d.config, qos=1, retain=True)


def push_display(room_code: str, payload: dict):
    """Giá trị hiển thị LCD (retain) cho mọi node chấp hành trong phòng."""
    with session_scope() as db:
        ids = [lcd.device_id for lcd in room_devices(db, room_code, "actuator")]
    for device_id in ids:
        bridge.publish(topic(room_code, device_id, "display"), payload, qos=0, retain=True)


# ======================================================================== bản tin từ thiết bị

def _lookup(db, room_code: str, device_id: str) -> Device | None:
    d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
    if d is None or d.room_code != room_code:
        log.warning("Bản tin từ thiết bị lạ hoặc sai phòng: %s/%s", room_code, device_id)
        return None
    return None if d.state in (S.DECOMMISSIONED, S.SUSPENDED, S.REGISTERED) else d


def _came_online(d: Device):
    d.online, d.last_seen = True, utcnow()
    # Provisioned -> Active (kết nối lần đầu), Offline -> Active (heartbeat trở lại).
    # Fault giữ nguyên tới khi cảm biến hết lỗi; Maintenance giữ nguyên tới khi quản trị viên chuyển.
    if d.state in (S.PROVISIONED, S.OFFLINE):
        d.state = S.ACTIVE


def _went_offline(d: Device, reason: str) -> bool:
    if not d.online and d.state == S.OFFLINE:
        return False
    d.online = False
    if d.state in (S.ACTIVE, S.FAULT, S.PROVISIONED) and d.last_seen:
        d.state = S.OFFLINE
        log.info("%s mất kết nối: %s", d.device_id, reason)
    return True


def on_status(room_code, device_id, payload):
    with session_scope() as db:
        d = _lookup(db, room_code, device_id)
        if d is None:
            return
        state = payload.get("state") or payload.get("value")
        if payload.get("fw"):
            d.fw_version = str(payload["fw"])[:30]
        if payload.get("hw"):
            d.hw_version = str(payload["hw"])[:60]
        if state == "online":
            _came_online(d)
        elif state == "offline":            # Last Will: mất kết nối đột ngột
            _went_offline(d, "Last Will")
        db.flush()
        notify(db, d)


def on_heartbeat(room_code, device_id, payload):
    with session_scope() as db:
        d = _lookup(db, room_code, device_id)
        if d is None:
            return
        before, first_rssi = (d.online, d.state), d.rssi is None
        _came_online(d)
        if isinstance(payload.get("rssi"), (int, float)):
            d.rssi = int(payload["rssi"])
        if payload.get("fw"):
            d.fw_version = str(payload["fw"])[:30]
        db.flush()
        if before != (d.online, d.state) or (first_rssi and d.rssi is not None) or time.time() - _last_uplink.get(device_id, 0) >= HEARTBEAT_UPLINK_S:
            notify(db, d)


def on_event(room_code, device_id, payload):
    """Sự kiện cục bộ: boot, sensor_error (count = số lần lỗi liên tiếp), sensor_ok, connected..."""
    with session_scope() as db:
        d = _lookup(db, room_code, device_id)
        if d is None:
            return
        kind = payload.get("type")
        log.info("[%s] event %s %s", device_id, kind, payload)
        detail = None
        if kind == "sensor_error" and int(payload.get("count", 1)) >= 3 and d.state == S.ACTIVE:
            d.state, detail = S.FAULT, str(payload.get("detail", "cảm biến"))[:60]
        elif kind == "sensor_ok" and d.state == S.FAULT:
            d.state = S.ACTIVE
        db.flush()
        notify(db, d)
        uplink.emit(db, "device.event", {"device_id": d.device_id, "room_code": d.room_code, "type": kind,
                                         "detail": detail or payload.get("detail")}, ttl_s=300)


def check_offline():
    """Gọi định kỳ: quá heartbeat_timeout_s không nhận heartbeat -> Offline."""
    limit = utcnow() - timedelta(seconds=settings.heartbeat_timeout_s)
    with session_scope() as db:
        for d in db.query(Device).filter(Device.online.is_(True), Device.last_seen < limit).all():
            if _went_offline(d, f"không có heartbeat > {settings.heartbeat_timeout_s}s"):
                db.flush()
                notify(db, d)
