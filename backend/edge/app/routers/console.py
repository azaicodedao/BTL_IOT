"""
API của bảng điều khiển biên (frontend/edge-console): duyệt thiết bị tự báo danh, gán phòng, vòng đời, cấu hình, lệnh thử,
xem camera. Đăng nhập bằng mật khẩu cục bộ của server biên (EDGE_ADMIN_PASSWORD).

Các đường dẫn xem ảnh (camera, ảnh bằng chứng, khuôn mặt) còn nhận "vé" do trung tâm ký để người dùng dashboard
lấy ảnh TRỰC TIẾP từ biên - ảnh không đi qua server trung tâm.
"""

from typing import Literal, Optional

import cv2
import numpy as np
import requests
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic import field_validator
from backend.edge.app.services.camera_sources import manager as camera_sources, validate_url
from starlette.concurrency import run_in_threadpool

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import get_db
from backend.edge.app.core.deps import client_ip, console_admin, login, viewer
from backend.edge.app.models import AuditLog, Device, DeviceState as S, Enrollment, EnrollmentStatus, Frame, Room
from backend.edge.app.services import (command_service, device_service, enrollment_service, exam_state, mdns, ops,
                               stream_service, uplink)
from backend.edge.app.services.audit import audit
from backend.edge.app.services.mqtt_bridge import bridge

router = APIRouter(prefix="/edge/v1", tags=["bảng điều khiển biên"])


class LoginIn(BaseModel):
    password: str


class CameraSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def video_url(cls, value):
        return validate_url(value)


@router.get("/camera-sources")
def list_camera_sources(_=Depends(console_admin)):
    return camera_sources.list()


@router.post("/camera-sources", status_code=201)
def add_camera_source(body: CameraSourceIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    source = camera_sources.create(body.name, body.url)
    audit(db, "console", "camera_source.add", source["id"], client_ip(request))
    db.commit()
    return source


@router.put("/camera-sources/{source_id}")
def update_camera_source(source_id: str, body: CameraSourceIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    source = camera_sources.update(source_id, body.name, body.url, body.enabled)
    audit(db, "console", "camera_source.update", source_id, client_ip(request))
    db.commit()
    return source


@router.delete("/camera-sources/{source_id}", status_code=204)
def delete_camera_source(source_id: str, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    camera_sources.remove(source_id)
    audit(db, "console", "camera_source.delete", source_id, client_ip(request))
    db.commit()


class RegisterIn(BaseModel):
    device_id: Optional[str] = Field(None, description="Bỏ trống -> tự đặt theo loại + 6 ký tự cuối MAC")


class AssignIn(BaseModel):
    room_code: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,20}$")


class TransitionIn(BaseModel):
    to: Literal["maintenance", "active", "suspended", "decommissioned"]
    reason: Optional[str] = None


class ConfigIn(BaseModel):
    telemetry_interval_s: Optional[int] = None
    heartbeat_interval_s: Optional[int] = None
    frame_interval_s: Optional[float] = None
    role: Optional[Literal["checkin", "exam", "both"]] = None
    stream_url: Optional[str] = None


class CommandIn(BaseModel):
    action: Literal["buzzer", "display"]
    pattern: Optional[Literal["once", "continuous", "stop"]] = None
    text: Optional[str] = Field(None, max_length=32)


def _broker(fn, *args):
    """Thao tác cần broker: lỗi Dynamic Security -> 503 thay vì 500."""
    try:
        return fn(*args)
    except RuntimeError as e:
        raise HTTPException(503, f"Không cấu hình được broker MQTT: {e}")


# ---------------------------------------------------------------- đăng nhập, trạng thái

@router.post("/auth/login")
def do_login(body: LoginIn, request: Request, db=Depends(get_db)):
    ip = client_ip(request)
    try:
        token = login(body.password, ip)
    except HTTPException:
        audit(db, "console", "auth.login_failed", None, ip)
        db.commit()
        raise
    audit(db, "console", "auth.login", None, ip)
    db.commit()
    return {"token": token, "edge_id": settings.edge_id, "name": settings.name}


@router.get("/status", summary="Sức khoẻ server biên: broker, liên kết trung tâm, mDNS")
def status(db=Depends(get_db), _=Depends(console_admin)):
    return {
        "edge_id": settings.edge_id, "name": settings.name, "public_url": settings.public_url,
        "mqtt": {"connected": bridge.connected.is_set(), "host": settings.mqtt_host, "port": settings.mqtt_port},
        "central": {"url": settings.central_url, **uplink.state, "outbox": uplink.backlog(),
                    "queue": uplink.queue_status()},
        "mdns": mdns.state,
        "devices": db.query(Device).count(),
        "pending": db.query(Enrollment).filter(Enrollment.status == EnrollmentStatus.PENDING).count(),
        "exams": [{"exam_id": e.exam_id, "room_code": e.room_code, "phase": e.phase, "name": e.name,
                   "roster": len(e.students or {})} for e in db.query(exam_state.ExamState).all()],
    }


@router.post("/sync/retry", status_code=202)
def retry_sync(_=Depends(console_admin)):
    uplink.request_flush()
    uplink.request_sync()
    return {"queued": True, "message": "Đã yêu cầu đồng bộ lại; dữ liệu chờ vẫn được lưu tại biên"}


@router.get("/rooms", summary="Phòng (đồng bộ từ trung tâm) + phòng đang có thiết bị")
def rooms(db=Depends(get_db), _=Depends(console_admin)):
    out = {r.code: r.name for r in db.query(Room).all()}
    for (code,) in db.query(Device.room_code).filter(Device.room_code.isnot(None)).distinct():
        out.setdefault(code, None)
    return [{"code": c, "name": n} for c, n in sorted(out.items())]


# ---------------------------------------------------------------- yêu cầu đăng ký

@router.get("/enrollments", summary="Thiết bị tự báo danh: đang chờ, bị từ chối, xin ghép lại")
def enrollments(db=Depends(get_db), _=Depends(console_admin)):
    q = db.query(Enrollment).filter(Enrollment.status.in_([EnrollmentStatus.PENDING, EnrollmentStatus.REJECTED])
                                    | Enrollment.pending_claim_hash.isnot(None))
    return [{**enrollment_service.enrollment_dict(e), "suggested_device_id": enrollment_service.suggest_device_id(db, e)}
            for e in q.order_by(Enrollment.last_seen.desc())]


@router.post("/enrollments/{enrollment_id}/register", status_code=201, summary="Đăng ký thiết bị đang chờ")
def register(enrollment_id: int, body: RegisterIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    e = enrollment_service.get(db, enrollment_id)
    d = enrollment_service.register(db, e, body.device_id)
    audit(db, "console", "device.register", d.device_id, client_ip(request), hardware_uid=e.hardware_uid)
    db.commit()
    return device_service.device_dict(d)


@router.post("/enrollments/{enrollment_id}/reject", summary="Từ chối yêu cầu")
def reject(enrollment_id: int, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    e = enrollment_service.get(db, enrollment_id)
    enrollment_service.reject(db, e)
    audit(db, "console", "enrollment.reject", e.hardware_uid, client_ip(request))
    db.commit()
    return enrollment_service.enrollment_dict(e)


@router.delete("/enrollments/{enrollment_id}", status_code=204, summary="Xoá yêu cầu (thiết bị được báo danh lại)")
def remove_enrollment(enrollment_id: int, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    e = enrollment_service.get(db, enrollment_id)
    enrollment_service.remove(db, e)
    audit(db, "console", "enrollment.delete", e.hardware_uid, client_ip(request))
    db.commit()


@router.post("/enrollments/{enrollment_id}/reclaim", summary="Chấp nhận / bác bỏ thiết bị đã đăng ký xin ghép lại")
def reclaim(enrollment_id: int, accept: bool, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    e = enrollment_service.get(db, enrollment_id)
    _broker(enrollment_service.accept_reclaim, db, e, accept)
    audit(db, "console", "enrollment.reclaim_accept" if accept else "enrollment.reclaim_deny", e.device_id,
          client_ip(request))
    db.commit()
    return enrollment_service.enrollment_dict(e)


# ---------------------------------------------------------------- thiết bị

@router.get("/devices")
def devices(db=Depends(get_db), _=Depends(console_admin)):
    waiting = {e.device_id for e in db.query(Enrollment).filter(Enrollment.status == EnrollmentStatus.APPROVED)}
    return [{**device_service.device_dict(d), "viewers": stream_service.viewers(d.device_id),
             "awaiting_key": d.device_id in waiting and d.state == S.REGISTERED}
            for d in db.query(Device).order_by(Device.device_id)]


@router.post("/devices/{device_id}/assign", summary="Gán / đổi phòng. Thiết bị tự nhận khoá MQTT + topic mới")
def assign(device_id: str, body: AssignIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    d = device_service.get(db, device_id)
    old = _broker(device_service.assign_room, db, d, body.room_code)
    for room in {old, body.room_code} - {None}:
        exam_state.sync_camera_mode(db, room)
    audit(db, "console", "device.assign_room", d.device_id, client_ip(request), frm=old, to=body.room_code)
    db.commit()
    return device_service.device_dict(d)


@router.post("/devices/{device_id}/rekey", summary="Cấp lại khoá (thu hồi khoá cũ, thiết bị tự nhận khoá mới)")
def rekey(device_id: str, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    d = device_service.get(db, device_id)
    _broker(device_service.rekey, db, d)
    audit(db, "console", "device.rekey", d.device_id, client_ip(request))
    db.commit()
    return device_service.device_dict(d)


@router.post("/devices/{device_id}/transition", summary="Bảo trì / tạm khoá / mở khoá / thu hồi")
def transition(device_id: str, body: TransitionIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    d = device_service.get(db, device_id)
    before = d.state
    _broker(device_service.transition, db, d, body.to)
    if d.room_code:
        exam_state.sync_camera_mode(db, d.room_code)
    audit(db, "console", "device.transition", d.device_id, client_ip(request), frm=before, to=d.state,
          reason=body.reason)
    db.commit()
    return device_service.device_dict(d)


@router.delete("/devices/{device_id}", status_code=204, summary="Xoá bản ghi thiết bị đã thu hồi / chưa cấp khoá")
def delete_device(device_id: str, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    d = device_service.get(db, device_id)
    _broker(device_service.delete, db, d)
    audit(db, "console", "device.delete", device_id, client_ip(request))
    db.commit()


@router.put("/devices/{device_id}/config")
def put_config(device_id: str, body: ConfigIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    out = ops.apply_config(db, device_service.get(db, device_id), body.model_dump(exclude_none=True), "console",
                           client_ip(request))
    db.commit()
    return out


@router.post("/devices/{device_id}/commands", status_code=202, summary="Gửi lệnh thử (còi, hiển thị LCD)")
def send_command(device_id: str, body: CommandIn, request: Request, db=Depends(get_db), _=Depends(console_admin)):
    d = device_service.get(db, device_id)
    params = {"pattern": body.pattern} if body.action == "buzzer" else {"text": body.text}
    audit(db, "console", "device.command", d.device_id, client_ip(request), cmd=body.action, **params)
    return command_service.send(db, d, body.action, params, "console")


@router.get("/devices/{device_id}/commands")
def list_commands(device_id: str, limit: int = 30, db=Depends(get_db), _=Depends(console_admin)):
    return command_service.history(db, device_service.get(db, device_id), limit)


@router.get("/audit-logs")
def audit_logs(limit: int = 200, db=Depends(get_db), _=Depends(console_admin)):
    return [{"id": a.id, "actor": a.actor, "action": a.action, "target": a.target, "ip": a.ip,
             "ts": a.ts.isoformat(), "detail": a.detail}
            for a in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))]


# ---------------------------------------------------------------- ảnh (chỉ phục vụ tại biên)

@router.get("/devices/{device_id}/stream", summary="Camera realtime (MJPEG). ?token= (biên) hoặc ?ticket= (trung tâm ký)")
def stream(device_id: str, token: str = "", ticket: str = "", db=Depends(get_db)):
    viewer(f"stream:{device_id}", token, ticket)
    d = device_service.get(db, device_id)
    if d.type != "camera":
        raise HTTPException(400, "Không phải camera")
    gen = stream_service.mjpeg(device_id,
                               on_join=lambda: exam_state.sync_camera_mode_for_device(device_id),
                               on_leave=lambda: exam_state.sync_camera_mode_for_device(device_id))
    return StreamingResponse(gen, media_type=f"multipart/x-mixed-replace; boundary={stream_service.BOUNDARY}",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/frames/{frame_id}", summary="Ảnh bằng chứng lưu tại biên (vé scope frame:<id>)")
def frame(frame_id: int, request: Request, token: str = "", ticket: str = "", db=Depends(get_db)):
    who = viewer(f"frame:{frame_id}", token, ticket)
    f = db.get(Frame, frame_id)
    path = settings.storage_dir / f.file_path if f else None
    if path is None or not path.exists():
        raise HTTPException(404, "Ảnh không còn (đã hết hạn lưu trữ hoặc bị xoá)")
    audit(db, who, "frame.view", f"frame:{frame_id}", client_ip(request))
    db.commit()
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- khuôn mặt: ảnh ở biên, chỉ vector lên trung tâm

def _face_crop(img, bbox, pad=0.35):
    """Cắt vùng mặt nới rộng 35%, thu về tối đa 256px để làm ảnh xem lại."""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    H, W = img.shape[:2]
    crop = img[max(int(y1 - pad * h), 0):min(int(y2 + pad * h), H), max(int(x1 - pad * w), 0):min(int(x2 + pad * w), W)]
    scale = 256 / max(crop.shape[:2])
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else crop


@router.post("/faces/{student_id}/samples",
             summary="Đăng ký khuôn mặt: ảnh xử lý tại biên, chỉ vector 512 chiều được gửi lên trung tâm")
async def enroll_faces(student_id: int, request: Request, files: list[UploadFile] = File(...), token: str = "",
                       ticket: str = "", db=Depends(get_db)):
    import uuid

    from backend.edge.app.services import face_model

    who = viewer(f"faces:{student_id}", token, ticket)
    samples, crops, rejected = [], {}, []
    for f in files[:10]:
        img = cv2.imdecode(np.frombuffer(await f.read(), np.uint8), cv2.IMREAD_COLOR)
        found = await run_in_threadpool(face_model.faces, img, 60) if img is not None else []
        if len(found) != 1:
            rejected.append({"file": f.filename, "reason": "không thấy mặt rõ" if not found else "nhiều hơn 1 mặt"})
            continue
        sample_id = str(uuid.uuid4())
        samples.append({"id": sample_id, "vector": found[0].normed_embedding.astype(float).tolist()})
        crops[sample_id] = _face_crop(img, found[0].bbox)

    total = None
    if samples:
        try:
            total = (await run_in_threadpool(uplink.post, "/faces", {"student_id": student_id, "samples": samples})
                     )["face_samples"]
        except requests.HTTPError as e:
            detail = e.response.json().get("detail") if e.response is not None else str(e)
            raise HTTPException(e.response.status_code if e.response is not None else 502, detail)
        except requests.RequestException as e:
            raise HTTPException(503, f"Chưa gửi được vector lên trung tâm: {e}")
        folder = ops.faces_dir(student_id)
        folder.mkdir(parents=True, exist_ok=True)
        for sample_id, crop in crops.items():
            cv2.imwrite(str(folder / f"{sample_id}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    audit(db, who, "face.enroll", f"student:{student_id}", client_ip(request), added=len(samples))
    db.commit()
    return {"added": len(samples), "rejected": rejected, "face_samples": total,
            "sample_ids": [s["id"] for s in samples]}


@router.get("/faces/{student_id}/samples/{sample_id}/image", summary="Ảnh cắt khuôn mặt của 1 mẫu (lưu tại biên)")
def face_image(student_id: int, sample_id: str, request: Request, token: str = "", ticket: str = "",
               db=Depends(get_db)):
    who = viewer(f"faces:{student_id}", token, ticket)
    path = ops.faces_dir(student_id) / f"{sample_id}.jpg"
    if not ops.valid_sample_id(sample_id) or not path.exists():
        raise HTTPException(404, "Không có ảnh cho mẫu này")
    audit(db, who, "face.view", f"student:{student_id}", client_ip(request), sample=sample_id)
    db.commit()
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
