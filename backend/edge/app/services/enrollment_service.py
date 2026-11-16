"""
Đăng ký thiết bị không chạm (zero-touch): thiết bị TỰ báo danh, quản trị viên chỉ bấm duyệt trên bảng điều khiển biên.

  thiết bị  POST /device-enrollments/announce          -> yêu cầu "pending" + claim token (chỉ trả 1 lần) + mã ghép
  quản trị  Đăng ký (đặt device_id)                     -> "registered"   (bảng devices: Registered)
  quản trị  Gán phòng                                   -> "approved"
  thiết bị  GET  /device-enrollments/{uid} (claim token) -> nhận device_id, khoá MQTT, topic, token gửi ảnh
  thiết bị  kết nối MQTT                                -> Active

Vì sao an toàn dù không ai gõ khoá:
- Khoá chỉ được sinh ra tại thời điểm chính thiết bị tới lấy và chỉ lưu bản băm; không hiển thị cho người, không nằm trong firmware.
- Claim token gắn yêu cầu với đúng thiết bị đã báo danh. Kẻ giả mạo MAC của thiết bị ĐÃ đăng ký không có token
  -> chỉ tạo được "yêu cầu ghép lại" phải được quản trị viên chấp nhận (đối chiếu mã ghép hiện trên thiết bị).
- Thiết bị mất khoá (bị thu hồi, đổi phòng, đổi địa chỉ server) tự quay lại hỏi bằng claim token: không cần nạp lại firmware.
"""

import logging
import re
import secrets

from fastapi import HTTPException

from backend.edge.app.core.config import settings
from backend.edge.app.models import Device, DeviceState as S, Enrollment, EnrollmentStatus as E
from backend.edge.app.services import device_service
from backend.shared.db import utcnow
from backend.shared.security import new_secret, same_secret, sha256

log = logging.getLogger("enrollment")

DEVICE_ID_RE = re.compile(r"^[a-z0-9-]{3,40}$")
UID_RE = re.compile(r"^[A-Za-z0-9:._-]{6,40}$")


def enrollment_dict(e: Enrollment) -> dict:
    return {"id": e.id, "hardware_uid": e.hardware_uid, "factory_name": e.factory_name, "device_type": e.device_type,
            "type": e.type, "firmware_version": e.firmware_version, "ip": e.ip, "status": e.status,
            "pair_code": e.pair_code, "reclaim_pending": bool(e.pending_claim_hash),
            "reclaim_pair_code": e.pending_pair_code, "announces": e.announces, "device_id": e.device_id,
            "first_seen": e.first_seen.isoformat(), "last_seen": e.last_seen.isoformat()}


def _kind(device_type: str) -> str:
    return "camera" if "cam" in device_type.lower() else "actuator"


def _new_claim() -> tuple[str, str, str]:
    token = new_secret(24)
    return token, sha256(token), f"{secrets.randbelow(10000):04d}"


def _claim_matches(token: str, hashed: str | None) -> bool:
    return bool(token and hashed) and same_secret(sha256(token), hashed)


def _waiting(e: Enrollment, claim_token: str | None = None, pair_code: str | None = None) -> dict:
    out = {"status": "pending", "poll_interval_s": settings.enrollment_poll_s, "pair_code": pair_code or e.pair_code}
    if claim_token:
        out["claim_token"] = claim_token
    return out


def suggest_device_id(db, e: Enrollment) -> str:
    tail = re.sub(r"[^a-z0-9]", "", e.hardware_uid.lower())[-6:]
    base = f"{'cam' if e.type == 'camera' else 'lcd'}-{tail}"
    candidate, n = base, 1
    while db.query(Device).filter(Device.device_id == candidate).first():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


# ======================================================================== phía thiết bị

def announce(db, body: dict, ip: str | None, claim_token: str) -> dict:
    uid = body["hardware_uid"]
    e = db.query(Enrollment).filter(Enrollment.hardware_uid == uid).one_or_none()
    if e is None:
        if db.query(Enrollment).filter(Enrollment.status == E.PENDING).count() >= settings.max_pending_enrollments:
            raise HTTPException(429, "Quá nhiều yêu cầu đăng ký đang chờ duyệt")
        token, hashed, code = _new_claim()
        e = Enrollment(hardware_uid=uid, factory_name=body.get("factory_name"), device_type=body["device_type"],
                       type=_kind(body["device_type"]), firmware_version=body.get("firmware_version"), ip=ip,
                       claim_hash=hashed, pair_code=code)
        db.add(e)
        log.info("Thiết bị mới báo danh: %s (%s) từ %s", e.factory_name, uid, ip)
        return _waiting(e, token)

    e.last_seen, e.ip, e.announces = utcnow(), ip, (e.announces or 0) + 1
    e.firmware_version = body.get("firmware_version") or e.firmware_version
    e.factory_name = body.get("factory_name") or e.factory_name

    if _claim_matches(claim_token, e.claim_hash):
        return status_for(db, e)                       # thiết bị cũ khởi động lại: không đổi token
    if _claim_matches(claim_token, e.pending_claim_hash):
        return _waiting(e, pair_code=e.pending_pair_code)

    # Không có / sai claim token
    if e.status == E.REJECTED:
        return {"status": "rejected", "poll_interval_s": 60}
    token, hashed, code = _new_claim()
    if e.status == E.PENDING:                          # chưa ai duyệt: token mới thay token cũ
        e.claim_hash, e.pair_code = hashed, code
        return _waiting(e, token)
    # Thiết bị đã đăng ký mà mất token (factory reset) hoặc ai đó giả MAC: chờ quản trị viên chấp nhận ghép lại
    e.pending_claim_hash, e.pending_pair_code = hashed, code
    log.warning("Thiết bị đã đăng ký %s báo danh lại không có claim token (IP %s) - chờ chấp nhận ghép lại",
                e.device_id, ip)
    return _waiting(e, token, code)


def poll(db, uid: str, claim_token: str) -> dict:
    e = db.query(Enrollment).filter(Enrollment.hardware_uid == uid).one_or_none()
    if e is None:
        raise HTTPException(404, "Chưa có yêu cầu đăng ký - hãy gọi announce")
    if _claim_matches(claim_token, e.pending_claim_hash):
        return _waiting(e, pair_code=e.pending_pair_code)
    if not _claim_matches(claim_token, e.claim_hash):
        raise HTTPException(401, "Claim token không hợp lệ - hãy gọi announce lại")
    e.last_seen = utcnow()
    return status_for(db, e)


def status_for(db, e: Enrollment) -> dict:
    """Trạng thái cho thiết bị đã chứng minh claim token. Nếu đã được duyệt + gán phòng: cấp khoá mới ngay tại đây."""
    slow = {"poll_interval_s": 30}
    if e.status == E.REJECTED:
        return {"status": "rejected", "poll_interval_s": 60}
    if e.status == E.PENDING:
        return _waiting(e)
    d = db.query(Device).filter(Device.device_id == e.device_id).one_or_none()
    if d is None or d.state == S.DECOMMISSIONED:
        return {"status": "rejected", "poll_interval_s": 60}
    if d.state == S.SUSPENDED:
        return {"status": "suspended", **slow}
    if e.status == E.REGISTERED or not d.room_code:
        return {"status": "registered", "device_id": d.device_id, "poll_interval_s": settings.enrollment_poll_s}
    d.fw_version = e.firmware_version or d.fw_version
    try:
        creds = device_service.issue_credentials(db, d)
    except RuntimeError as err:
        raise HTTPException(503, f"Broker chưa sẵn sàng: {err}")
    log.info("Đã cấp khoá cho %s (phòng %s)", d.device_id, d.room_code)
    return {"status": "provisioned", **creds}


# ======================================================================== phía quản trị viên

def get(db, enrollment_id: int) -> Enrollment:
    e = db.get(Enrollment, enrollment_id)
    if e is None:
        raise HTTPException(404, "Không có yêu cầu đăng ký")
    return e


def register(db, e: Enrollment, device_id: str | None) -> Device:
    """Đăng ký thiết bị đang chờ -> bảng devices (Registered). Bước gán phòng tách riêng."""
    if e.status != E.PENDING:
        raise HTTPException(409, f"Yêu cầu đang ở trạng thái {e.status}")
    device_id = (device_id or suggest_device_id(db, e)).strip().lower()
    if not DEVICE_ID_RE.match(device_id):
        raise HTTPException(422, "Mã thiết bị chỉ gồm a-z, 0-9, dấu gạch ngang (3-40 ký tự)")
    if db.query(Device).filter(Device.device_id == device_id).first():
        raise HTTPException(409, "Mã thiết bị đã tồn tại")
    d = Device(device_id=device_id, hardware_uid=e.hardware_uid, type=e.type, state=S.REGISTERED,
               fw_version=e.firmware_version, hw_version=e.factory_name, config={})
    db.add(d)
    e.status, e.device_id = E.REGISTERED, device_id
    db.flush()
    device_service.notify(db, d)
    return d


def reject(db, e: Enrollment):
    if e.device_id:
        raise HTTPException(409, "Thiết bị đã đăng ký - dùng 'Thu hồi' trong danh sách thiết bị")
    e.status = E.REJECTED


def remove(db, e: Enrollment):
    """Xoá yêu cầu chưa đăng ký (thiết bị có thể báo danh lại từ đầu)."""
    if e.device_id:
        raise HTTPException(409, "Thiết bị đã đăng ký - xoá trong danh sách thiết bị")
    db.delete(e)


def accept_reclaim(db, e: Enrollment, accept: bool):
    """Thiết bị đã đăng ký xin ghép lại (mất claim token). Chấp nhận -> token mới có hiệu lực, khoá cũ bị thu hồi."""
    if not e.pending_claim_hash:
        raise HTTPException(409, "Không có yêu cầu ghép lại")
    if accept:
        e.claim_hash, e.pair_code = e.pending_claim_hash, e.pending_pair_code
        d = db.query(Device).filter(Device.device_id == e.device_id).one_or_none()
        if d is not None and d.room_code and d.state not in (S.SUSPENDED, S.DECOMMISSIONED):
            device_service.rekey(db, d)
    e.pending_claim_hash = e.pending_pair_code = None
