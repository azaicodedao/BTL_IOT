"""
Thao tác quản lý thiết bị dùng chung cho 2 lối vào:
  - bảng điều khiển biên (routers/console.py, quản trị viên tại chỗ)
  - lệnh từ trung tâm qua kênh WebSocket (uplink.rpc_handlers, người dùng dashboard đã được trung tâm phân quyền)
"""

import shutil

from fastapi import HTTPException

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.services import command_service, device_service, exam_state, uplink
from backend.edge.app.services.audit import audit

CONFIG_KEYS = {"telemetry_interval_s": (5, 3600), "heartbeat_interval_s": (5, 300), "frame_interval_s": (0.1, 60)}
ROLES = ("checkin", "exam", "both")


def faces_dir(student_id: int):
    return settings.storage_dir / "faces" / str(int(student_id))


def valid_sample_id(sample_id: str) -> bool:
    return 0 < len(sample_id) <= 40 and sample_id.replace("-", "").isalnum()


def apply_config(db, device, changes: dict, actor: str, ip: str | None = None) -> dict:
    """Đổi cấu hình từ xa (publish retain lên .../config). Raises 422 nếu tham số ngoài miền."""
    clean = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key in CONFIG_KEYS:
            lo, hi = CONFIG_KEYS[key]
            if not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise HTTPException(422, f"{key} phải trong khoảng {lo}..{hi}")
            clean[key] = value
        elif key == "role":
            if value not in ROLES:
                raise HTTPException(422, "role phải là checkin | exam | both")
            clean[key] = value
        elif key == "stream_url":
            clean[key] = str(value)[:200]
        else:
            raise HTTPException(422, f"Không hỗ trợ tham số {key}")
    if not clean:
        raise HTTPException(422, "Không có tham số nào")
    device_service.push_config(db, device, clean)
    if "role" in clean and device.room_code:
        exam_state.sync_camera_mode(db, device.room_code)
    audit(db, actor, "device.config", device.device_id, ip, **clean)
    device_service.notify(db, device)
    return device_service.device_dict(device)


# ---------------------------------------------------------------- lệnh từ trung tâm

def _rpc_sync(_params):
    uplink.request_sync()
    return {"ok": True}


def _rpc_command(p):
    with session_scope() as db:
        d = device_service.get(db, p["device_id"])
        audit(db, p.get("issued_by", "central"), "device.command", d.device_id, None,
              cmd=p["action"], **(p.get("params") or {}))
        return command_service.send(db, d, p["action"], p.get("params") or {}, p.get("issued_by", "central"))


def _rpc_room_buzzer(p):
    ids = command_service.buzz_room(p["room_code"], p["pattern"], p.get("issued_by", "central"),
                                    auto_stop_s=p.get("auto_stop_s"))
    return {"commands": ids}


def _rpc_commands(p):
    with session_scope() as db:
        return command_service.history(db, device_service.get(db, p["device_id"]), int(p.get("limit", 50)))


def _rpc_config(p):
    with session_scope() as db:
        return apply_config(db, device_service.get(db, p["device_id"]), p.get("changes") or {},
                            p.get("actor", "central"))


def _rpc_face_samples(p):
    folder = faces_dir(p["student_id"])
    return [f.stem for f in folder.glob("*.jpg")] if folder.is_dir() else []


def _rpc_face_delete(p):
    """Xoá ảnh khuôn mặt cắt lưu tại biên (1 mẫu hoặc toàn bộ khi SV rút đồng ý)."""
    folder = faces_dir(p["student_id"])
    sample_id = p.get("sample_id")
    if sample_id:
        if valid_sample_id(sample_id):
            (folder / f"{sample_id}.jpg").unlink(missing_ok=True)
    else:
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


uplink.rpc_handlers.update({
    "sync": _rpc_sync, "command": _rpc_command, "room_buzzer": _rpc_room_buzzer, "commands": _rpc_commands,
    "config": _rpc_config, "face_samples": _rpc_face_samples, "face_delete": _rpc_face_delete,
})
