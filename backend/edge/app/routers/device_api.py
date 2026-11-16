"""API dành cho THIẾT BỊ: tự đăng ký (enrollment) và gửi ảnh. Thiết bị tìm server này qua mDNS."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import get_db
from backend.edge.app.core.deps import client_ip
from backend.edge.app.models import Device, DeviceState as S, Frame, FrameJob
from backend.edge.app.services import enrollment_service, exam_state, stream_service
from backend.shared.db import utcnow
from backend.shared.security import same_secret, sha256

router = APIRouter(prefix="/api/v1", tags=["thiết bị"])


class AnnounceIn(BaseModel):
    hardware_uid: str = Field(..., pattern=enrollment_service.UID_RE.pattern, examples=["24:6F:28:AA:BB:CC"])
    factory_name: Optional[str] = Field(None, max_length=60, examples=["ESP32CAM-AABBCC"])
    device_type: str = Field(..., max_length=30, examples=["esp32_cam"])
    firmware_version: Optional[str] = Field(None, max_length=30)
    timestamp: Optional[str] = None
    nonce: Optional[str] = Field(None, max_length=64)


def _bearer(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


@router.post("/device-enrollments/announce",
             summary="Thiết bị tự báo danh. Lần đầu nhận claim_token (giữ bí mật, gửi lại ở các lần sau)")
def announce(body: AnnounceIn, request: Request, authorization: str = Header(""), db=Depends(get_db)):
    out = enrollment_service.announce(db, body.model_dump(), client_ip(request), _bearer(authorization))
    db.commit()
    return out


@router.get("/device-enrollments/{hardware_uid}",
            summary="Thiết bị hỏi kết quả (Authorization: Bearer <claim_token>). Đã duyệt + gán phòng -> trả khoá")
def poll(hardware_uid: str, authorization: str = Header(""), db=Depends(get_db)):
    out = enrollment_service.poll(db, hardware_uid, _bearer(authorization))
    db.commit()
    return out


# ---------------------------------------------------------------- ảnh

# Lấy mẫu ảnh cho AI (lưu đĩa + tạo job). Các khung còn lại chỉ phát realtime, không lưu.
AI_SAMPLE_S = {"checkin": 1.0, "exam": 2.0}
_last_sample: dict[str, datetime] = {}


@router.post("/devices/{device_id}/frames",
             summary="Thiết bị gửi ảnh JPEG (body = bytes ảnh, Authorization: Bearer <token thiết bị>)")
async def upload_frame(device_id: str, request: Request, authorization: str = Header(""), db=Depends(get_db)):
    d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
    token = _bearer(authorization)
    if d is None or not d.token_hash or not token or not same_secret(sha256(token), d.token_hash):
        raise HTTPException(401, "Token thiết bị không hợp lệ")
    if d.state in (S.SUSPENDED, S.DECOMMISSIONED, S.REGISTERED) or d.type != "camera":
        raise HTTPException(403, f"Thiết bị đang ở trạng thái {d.state}")

    body = await request.body()
    if len(body) > settings.frame_max_bytes:
        raise HTTPException(413, f"Ảnh vượt {settings.frame_max_bytes // 1024} KB")
    if not body.startswith(b"\xff\xd8"):
        raise HTTPException(415, "Ảnh phải là JPEG")

    now = utcnow()
    stream_service.push(device_id, now, body)                 # realtime cho người đang xem tại biên

    exam = exam_state.active_exam(db, d.room_code)
    kind = exam_state.MODE_BY_PHASE.get(exam.phase) if exam else None
    if kind and (d.config or {}).get("camera_mode") != kind:
        kind = None                       # camera này chỉ đang xem trước, không phải camera của giai đoạn hiện tại
    last = _last_sample.get(device_id)
    if kind is None or (last and (now - last).total_seconds() < AI_SAMPLE_S[kind]):
        return Response('{"status":"streamed"}', status_code=200, media_type="application/json")
    _last_sample[device_id] = now

    rel = f"images/{now:%Y%m%d}/{device_id}_{now:%H%M%S_%f}.jpg"
    path = settings.storage_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    frame = Frame(device_id=device_id, ts=now, file_path=rel,
                  expires_at=now + timedelta(days=settings.frame_retention_days))
    db.add(frame)
    db.flush()
    db.add(FrameJob(frame_id=frame.id, kind=kind, exam_id=exam.exam_id))
    db.commit()
    return Response(f'{{"status":"accepted","frame_id":{frame.id},"kind":"{kind}"}}', status_code=202,
                    media_type="application/json")
