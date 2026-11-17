"""
Trạng thái ca thi tại biên. Trung tâm là nơi quyết định (mở ca, bắt đầu làm bài, kết thúc); biên kéo về
"trạng thái mong muốn" qua uplink.sync rồi áp dụng: bật đúng camera, nạp vector danh sách dự thi cho worker AI.

Chế độ camera của phòng do sync_camera_mode quyết định ở một chỗ duy nhất:
  checkin -> "checkin" | in_progress -> "exam" | không có ca nhưng có người xem -> "preview" | còn lại -> "idle"
"""

import logging

from backend.edge.app.core.db import session_scope
from backend.edge.app.models import (CheckinDone, CheckinHit, Device, DeviceState as S, ExamState, Room, SuspicionState)
from backend.edge.app.services import device_service, gallery, stream_service, uplink

log = logging.getLogger("exam")

MODE_BY_PHASE = {"checkin": "checkin", "in_progress": "exam"}
FRAME_INTERVAL_S = 0.2           # ~5 khung/giây khi đang phát hình


def active_exam(db, room_code: str | None) -> ExamState | None:
    return db.query(ExamState).filter(ExamState.room_code == room_code).first() if room_code else None


def camera_role(cam: Device) -> str:
    """checkin: camera ở cửa | exam: camera trong phòng | both: 1 camera làm cả hai."""
    return (cam.config or {}).get("role") or "both"


def camera_for_phase(db, room_code: str, phase_role: str) -> Device | None:
    """Camera dùng cho giai đoạn: đang hoạt động trước; trong đó ưu tiên đúng vai trò -> "both" -> camera khác."""
    cams = device_service.room_devices(db, room_code, "camera")
    if not cams:
        return None
    role_rank = {phase_role: 0, "both": 1}
    return min(cams, key=lambda c: (0 if c.online and c.state in (S.ACTIVE, S.FAULT) else 1,
                                    role_rank.get(camera_role(c), 2)))


def sync_camera_mode(db, room_code: str):
    exam = active_exam(db, room_code)
    mode_now = MODE_BY_PHASE.get(exam.phase) if exam else None
    active_cam = camera_for_phase(db, room_code, mode_now) if mode_now else None
    for cam in device_service.room_devices(db, room_code, "camera"):
        if active_cam is not None and cam.id == active_cam.id:
            mode = mode_now
        else:
            mode = "preview" if stream_service.viewers(cam.device_id) else "idle"
        cfg = cam.config or {}
        if cfg.get("camera_mode") != mode or cfg.get("frame_interval_s") != FRAME_INTERVAL_S:
            device_service.push_config(db, cam, {"camera_mode": mode, "frame_interval_s": FRAME_INTERVAL_S})
            device_service.notify(db, cam)


def sync_camera_mode_for_device(device_id: str):
    """Gọi khi có người bắt đầu / thôi xem camera."""
    with session_scope() as db:
        d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
        if d and d.room_code:
            sync_camera_mode(db, d.room_code)


def sync_all_rooms():
    with session_scope() as db:
        for (room_code,) in db.query(Device.room_code).filter(Device.room_code.isnot(None)).distinct():
            sync_camera_mode(db, room_code)


def apply_sync(data: dict):
    """Áp dụng trạng thái mong muốn từ trung tâm: {"rooms": [{code, name}], "exams": [{exam_id, room_code, phase,
    name, roster_hash, decided}]}. Ca không còn trong danh sách = đã kết thúc -> dọn dữ liệu cục bộ của ca."""
    exams = {int(e["exam_id"]): e for e in data.get("exams", [])}

    # Tải danh sách dự thi (kèm vector) TRƯỚC khi mở giao dịch ghi: không giữ khoá SQLite trong lúc gọi mạng
    with session_scope() as db:
        known = {s.exam_id: s.roster_hash for s in db.query(ExamState).all()}
    rosters = {}
    for exam_id, e in exams.items():
        if known.get(exam_id) != e.get("roster_hash") or not gallery.exists(exam_id):
            rosters[exam_id] = uplink.get(f"/exams/{exam_id}/roster")

    with session_scope() as db:
        db.query(Room).delete()
        db.add_all(Room(code=r["code"], name=r.get("name")) for r in data.get("rooms", []))

        for exam_id in set(known) - set(exams):
            for model in (ExamState, CheckinHit, CheckinDone, SuspicionState):
                db.query(model).filter(model.exam_id == exam_id).delete()
            gallery.remove(exam_id)
            log.info("Ca thi %s đã kết thúc", exam_id)

        for exam_id, e in exams.items():
            s = db.get(ExamState, exam_id)
            if s is None:
                s = ExamState(exam_id=exam_id, room_code=e["room_code"], phase=e["phase"])
                db.add(s)
                log.info("Ca thi %s phòng %s: %s", exam_id, e["room_code"], e["phase"])
            s.room_code, s.phase, s.name = e["room_code"], e["phase"], e.get("name")
            s.decided = {str(k): v for k, v in (e.get("decided") or {}).items()}
            roster = rosters.get(exam_id)
            if roster is not None:
                gallery.save(exam_id, roster["students"])
                s.students = {str(st["student_id"]): [st["student_code"], st["full_name"]]
                              for st in roster["students"]}
                s.roster_hash = roster["roster_hash"]
        db.flush()
        for (room_code,) in db.query(Device.room_code).filter(Device.room_code.isnot(None)).distinct():
            sync_camera_mode(db, room_code)


uplink.sync_handler = apply_sync
