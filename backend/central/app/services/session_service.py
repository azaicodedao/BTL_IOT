"""
Ca thi: kiểm tra vào phòng (checkin) -> làm bài, giám sát gian lận (in_progress) -> kết thúc (closed).

Trung tâm quyết định giai đoạn của ca thi; server biên của phòng kéo "trạng thái mong muốn" (edge_state) về và tự bật
đúng camera, chạy AI. Sau mỗi thay đổi, router gọi notify_edges() để biên đồng bộ ngay thay vì chờ chu kỳ.
Mỗi phòng tối đa 1 ca thi đang diễn ra.
"""

import hashlib

from fastapi import HTTPException

from backend.central.app.models import (CheckinAttempt, CheckinRecord, Class, Device, DeviceState as S, ExamPhase,
                                ExamSession, Room, SuspicionEvent)
from backend.central.app.services import edge_link, face_store
from backend.central.app.services.hub import hub
from backend.shared.db import utcnow

ACTIVE = (ExamPhase.CHECKIN, ExamPhase.IN_PROGRESS)
PHASE_ROLE = {ExamPhase.CHECKIN: "checkin", ExamPhase.IN_PROGRESS: "exam"}


def active_exam(db, room_id: int) -> ExamSession | None:
    return db.query(ExamSession).filter(ExamSession.room_id == room_id, ExamSession.status.in_(ACTIVE)).first()


def room_devices(db, room_id: int, type_: str) -> list[Device]:
    return db.query(Device).filter(Device.room_id == room_id, Device.type == type_,
                                   Device.state.notin_([S.DECOMMISSIONED, S.SUSPENDED])).all()


def camera_for_phase(db, room_id: int, phase_role: str) -> Device | None:
    """Camera dùng cho giai đoạn (cùng quy tắc với server biên): đang hoạt động trước, rồi đúng vai trò -> "both"."""
    cams = room_devices(db, room_id, "camera")
    if not cams:
        return None
    role_rank = {phase_role: 0, "both": 1}
    return min(cams, key=lambda c: (0 if c.online and c.state in (S.ACTIVE, S.FAULT) else 1,
                                    role_rank.get((c.config or {}).get("role") or "both", 2)))


def _camera_ready(db, room: Room, phase_role: str):
    cam = camera_for_phase(db, room.id, phase_role)
    if cam is None or cam.state not in (S.ACTIVE, S.FAULT):
        what = "kiểm tra vào phòng" if phase_role == "checkin" else "giám sát thi"
        raise HTTPException(409, f"Phòng {room.code} chưa có camera {what} đang hoạt động")
    if not edge_link.online(cam.edge_id):
        raise HTTPException(503, f"Server biên {cam.edge_id} của phòng {room.code} chưa kết nối với trung tâm")


def notify_edges(db, room_id: int):
    """Gọi SAU khi commit: nhắc các server biên có thiết bị trong phòng đồng bộ ngay."""
    edge_link.poke(e for (e,) in db.query(Device.edge_id).filter(Device.room_id == room_id,
                                                                Device.edge_id.isnot(None)).distinct())


def exam_dict(s: ExamSession, db=None) -> dict:
    out = {"id": s.id, "name": s.name, "room_id": s.room_id, "room_code": s.room.code,
           "class_id": s.class_id, "class_code": s.klass.code if s.klass else None,
           "class_name": s.klass.name if s.klass else None,
           "opened_by": s.opened_by, "started_at": s.started_at, "exam_started_at": s.exam_started_at,
           "ended_at": s.ended_at, "status": s.status,
           "roster_total": len([st for st in s.klass.students if st.active]) if s.klass else 0}
    if db is not None:
        cin, cex = camera_for_phase(db, s.room_id, "checkin"), camera_for_phase(db, s.room_id, "exam")
        out["checkin_camera"] = cin.device_id if cin else None
        out["exam_camera"] = cex.device_id if cex else None
        q = db.query(CheckinRecord).filter_by(exam_id=s.id)
        out["admitted"] = q.filter_by(status="admitted").count()
        out["absent"] = q.filter_by(status="absent").count()
        out["rejected_attempts"] = db.query(CheckinAttempt).filter_by(exam_id=s.id).count()
        out["pending_events"] = db.query(SuspicionEvent).filter_by(exam_id=s.id, review_status="pending").count()
    return out


def _broadcast(db, s: ExamSession):
    hub.publish_threadsafe("session.updated", exam_dict(s, db), room_id=s.room_id)


def open_exam(db, user, room: Room, klass: Class, name: str | None) -> ExamSession:
    if active_exam(db, room.id):
        raise HTTPException(409, f"Phòng {room.code} đang có ca thi chưa kết thúc")
    _camera_ready(db, room, "checkin")
    s = ExamSession(room_id=room.id, class_id=klass.id, name=name, opened_by=user.id, status=ExamPhase.CHECKIN)
    db.add(s)
    db.flush()
    db.refresh(s)
    _broadcast(db, s)
    return s


def start_exam(db, s: ExamSession):
    """Hết giờ kiểm tra vào phòng -> bắt đầu làm bài, camera chuyển sang giám sát gian lận."""
    if s.status != ExamPhase.CHECKIN:
        raise HTTPException(409, "Ca thi không ở giai đoạn kiểm tra vào phòng")
    _camera_ready(db, s.room, "exam")
    s.status, s.exam_started_at = ExamPhase.IN_PROGRESS, utcnow()
    db.flush()
    _broadcast(db, s)


def close_exam(db, s: ExamSession) -> dict:
    if s.status == ExamPhase.CLOSED:
        raise HTTPException(409, "Ca thi đã kết thúc")
    s.status, s.ended_at = ExamPhase.CLOSED, utcnow()
    done = {r.student_id for r in db.query(CheckinRecord.student_id).filter_by(exam_id=s.id)}
    absent = 0
    for st in (s.klass.students if s.klass else []):
        if st.active and st.id not in done:
            db.add(CheckinRecord(exam_id=s.id, student_id=st.id, status="absent", method="auto"))
            absent += 1
    db.flush()
    out = exam_dict(s, db)
    out["absent_marked"] = absent
    _broadcast(db, s)
    return out


# ======================================================================== trạng thái phát xuống server biên

def recognisable(s: ExamSession) -> list:
    """SV trong danh sách dự thi mà AI được phép nhận diện: đang hoạt động, đã đồng ý, có mẫu khuôn mặt."""
    return [st for st in (s.klass.students if s.klass else [])
            if st.active and st.consent_at is not None and (st.face_samples or 0) > 0]


def roster_hash(s: ExamSession) -> str:
    students = sorted(recognisable(s), key=lambda st: st.id)
    names = "|".join(f"{st.id}:{st.student_code}:{st.full_name}" for st in students)
    return hashlib.sha256((names + "#" + face_store.signature([st.id for st in students])).encode()).hexdigest()


def edge_state(db, edge_id: str) -> list[dict]:
    """Các ca thi đang diễn ra ở những phòng mà server biên này có thiết bị."""
    room_ids = [r for (r,) in db.query(Device.room_id).filter(
        Device.edge_id == edge_id, Device.room_id.isnot(None), Device.state != S.DECOMMISSIONED).distinct()]
    if not room_ids:
        return []
    out = []
    for s in db.query(ExamSession).filter(ExamSession.room_id.in_(room_ids), ExamSession.status.in_(ACTIVE)):
        decided = {str(r.student_id): r.status for r in db.query(CheckinRecord).filter_by(exam_id=s.id)}
        out.append({"exam_id": s.id, "room_code": s.room.code, "phase": s.status, "name": s.name,
                    "roster_hash": roster_hash(s), "decided": decided})
    return out


def roster_payload(s: ExamSession) -> dict:
    """Danh sách dự thi kèm vector khuôn mặt cho server biên (chỉ vector, không có ảnh)."""
    students = recognisable(s)
    vectors = face_store.vectors_by_student([st.id for st in students])
    return {"exam_id": s.id, "roster_hash": roster_hash(s),
            "students": [{"student_id": st.id, "student_code": st.student_code, "full_name": st.full_name,
                          "vectors": vectors.get(st.id, [])} for st in students]}
