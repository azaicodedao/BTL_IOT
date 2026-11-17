"""Ca thi: kiểm tra vào phòng bằng khuôn mặt -> giám sát gian lận -> kết thúc.

Ảnh bằng chứng nằm ở server biên: các endpoint .../frame chỉ kiểm tra quyền, ghi audit rồi trả URL có vé
ngắn hạn để trình duyệt lấy ảnh trực tiếp từ biên.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.central.app.core.config import settings
from backend.central.app.core.db import get_db
from backend.central.app.core.deps import any_user, check_room, client_ip, operator_up, room_ids
from backend.central.app.models import (Alert, CheckinAttempt, CheckinRecord, Class, Device, Edge, ExamPhase, ExamSession,
                                Room, Student, SuspicionEvent)
from backend.central.app.services import alert_service, edge_link, session_service
from backend.central.app.services.audit import audit
from backend.central.app.services.hub import hub
from backend.shared.db import utcnow

router = APIRouter(prefix="/api/v1/exam", tags=["exam sessions"])


def _get_exam(db, exam_id: int, user) -> ExamSession:
    s = db.get(ExamSession, exam_id)
    if s is None:
        raise HTTPException(404, "Không có ca thi")
    check_room(user, s.room_id)
    return s


def _frame_url(db, user, item) -> dict:
    """URL có vé tới ảnh lưu tại server biên. Raises 404 nếu bản ghi không kèm ảnh."""
    if item.frame_ref is None:
        raise HTTPException(404, "Bản ghi này không kèm ảnh")
    return {"url": edge_link.ticket_url(db.get(Edge, item.edge_id) if item.edge_id else None,
                                        f"/frames/{item.frame_ref}", f"frame:{item.frame_ref}", user.id)}


def record_dict(r: CheckinRecord) -> dict:
    return {"id": r.id, "exam_id": r.exam_id, "student_id": r.student_id, "student_code": r.student.student_code,
            "full_name": r.student.full_name, "ts": r.ts, "confidence": r.confidence, "method": r.method,
            "status": r.status, "note": r.note, "has_frame": r.frame_ref is not None}


def attempt_dict(a: CheckinAttempt) -> dict:
    return {"id": a.id, "exam_id": a.exam_id, "result": a.result, "confidence": a.confidence, "ts": a.ts,
            "student_id": a.student_id, "student_code": a.student.student_code if a.student else None,
            "full_name": a.student.full_name if a.student else None, "has_frame": a.frame_ref is not None}


def event_dict(e: SuspicionEvent, room_id: int | None = None) -> dict:
    return {"id": e.id, "exam_id": e.exam_id, "room_id": room_id, "type": e.type, "score": e.score,
            "student_code": e.student_code, "detail": e.detail, "review_status": e.review_status,
            "reviewed_by": e.reviewed_by, "review_note": e.review_note, "ts": e.ts,
            "has_frame": e.frame_ref is not None}


# ======================================================================== ca thi

class OpenExamIn(BaseModel):
    room_id: int
    class_id: int = Field(..., description="Danh sách dự thi")
    name: Optional[str] = Field(None, max_length=120)


class ManualCheckinIn(BaseModel):
    student_id: int
    status: Literal["admitted", "denied"]
    note: str = Field(..., min_length=3, description="Lý do (bắt buộc, ghi audit)")


class ReviewIn(BaseModel):
    decision: Literal["confirmed", "dismissed"]
    note: Optional[str] = None


@router.post("/sessions", status_code=201, summary="Mở ca thi -> bắt đầu kiểm tra vào phòng")
def open_exam(body: OpenExamIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    check_room(user, body.room_id)
    room, klass = db.get(Room, body.room_id), db.get(Class, body.class_id)
    if room is None or klass is None:
        raise HTTPException(404, "Không có phòng hoặc danh sách dự thi")
    s = session_service.open_exam(db, user, room, klass, body.name)
    audit(db, user, "exam.open", f"exam:{s.id}", client_ip(request), room=room.code, roster=klass.code)
    db.commit()
    session_service.notify_edges(db, s.room_id)
    return session_service.exam_dict(s, db)


@router.get("/sessions")
def list_exams(room_id: Optional[int] = None, status: Optional[str] = None, limit: int = 50,
               db=Depends(get_db), user=Depends(any_user)):
    q = db.query(ExamSession)
    allowed = room_ids(user)
    if allowed is not None:
        q = q.filter(ExamSession.room_id.in_(allowed or [-1]))
    if room_id:
        q = q.filter(ExamSession.room_id == room_id)
    if status == "active":
        q = q.filter(ExamSession.status.in_(session_service.ACTIVE))
    elif status:
        q = q.filter(ExamSession.status == status)
    return [session_service.exam_dict(s, db) for s in q.order_by(ExamSession.id.desc()).limit(min(limit, 200))]


@router.get("/sessions/{exam_id}", summary="Ca thi + danh sách dự thi (trạng thái vào phòng) + lượt bị từ chối + nghi vấn")
def get_exam(exam_id: int, db=Depends(get_db), user=Depends(any_user)):
    s = _get_exam(db, exam_id, user)
    records = {r.student_id: r for r in db.query(CheckinRecord).filter_by(exam_id=s.id)}
    roster = []
    for st in sorted(s.klass.students if s.klass else [], key=lambda x: x.student_code):
        r = records.pop(st.id, None)
        roster.append(record_dict(r) if r else {
            "id": None, "exam_id": s.id, "student_id": st.id, "student_code": st.student_code,
            "full_name": st.full_name, "status": "waiting", "method": None, "ts": None, "confidence": None,
            "note": None, "has_frame": False, "face_samples": st.face_samples or 0})
    roster += [record_dict(r) for r in records.values()]
    attempts = db.query(CheckinAttempt).filter_by(exam_id=s.id).order_by(CheckinAttempt.id.desc()).limit(50).all()
    events = db.query(SuspicionEvent).filter_by(exam_id=s.id).order_by(SuspicionEvent.id.desc()).all()
    return {"session": session_service.exam_dict(s, db), "roster": roster,
            "attempts": [attempt_dict(a) for a in attempts], "events": [event_dict(e, s.room_id) for e in events]}


@router.post("/sessions/{exam_id}/start", summary="Kết thúc kiểm tra vào phòng, bắt đầu làm bài (giám sát gian lận)")
def start_exam(exam_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    s = _get_exam(db, exam_id, user)
    session_service.start_exam(db, s)
    audit(db, user, "exam.start", f"exam:{s.id}", client_ip(request))
    db.commit()
    session_service.notify_edges(db, s.room_id)
    return session_service.exam_dict(s, db)


@router.post("/sessions/{exam_id}/close", summary="Kết thúc ca thi (SV chưa vào phòng -> vắng thi)")
def close_exam(exam_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    s = _get_exam(db, exam_id, user)
    out = session_service.close_exam(db, s)
    audit(db, user, "exam.close", f"exam:{s.id}", client_ip(request), absent=out["absent_marked"])
    db.commit()
    session_service.notify_edges(db, s.room_id)
    return out


@router.post("/sessions/{exam_id}/checkin", summary="Giám thị cho vào / từ chối thủ công (bắt buộc lý do)")
def manual_checkin(exam_id: int, body: ManualCheckinIn, request: Request, db=Depends(get_db),
                   user=Depends(operator_up)):
    s = _get_exam(db, exam_id, user)
    if s.status == ExamPhase.CLOSED:
        raise HTTPException(409, "Ca thi đã kết thúc")
    st = db.get(Student, body.student_id)
    if st is None:
        raise HTTPException(404, "Không có sinh viên")
    r = db.query(CheckinRecord).filter_by(exam_id=s.id, student_id=st.id).one_or_none()
    before = r.status if r else None
    if r is None:
        r = CheckinRecord(exam_id=s.id, student_id=st.id)
        db.add(r)
    r.status, r.note, r.method, r.ts = body.status, body.note, "manual", utcnow()
    db.flush()
    audit(db, user, f"checkin.manual_{body.status}", f"exam:{s.id}", client_ip(request),
          student=st.student_code, frm=before, note=body.note)
    db.commit()
    # biên biết SV này đã có quyết định -> AI không cho vào lần nữa
    session_service.notify_edges(db, s.room_id)
    hub.publish_threadsafe("checkin.updated", record_dict(r), room_id=s.room_id, session_kind="exam", session_id=s.id)
    return record_dict(r)


@router.get("/checkin/records/{record_id}/frame", summary="Vé xem ảnh lúc vào phòng tại server biên (ghi audit)")
def record_frame(record_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    r = db.get(CheckinRecord, record_id)
    if r is None:
        raise HTTPException(404, "Không có bản ghi")
    _get_exam(db, r.exam_id, user)
    out = _frame_url(db, user, r)
    audit(db, user, "checkin.view_frame", f"record:{r.id}", client_ip(request))
    db.commit()
    return out


@router.get("/checkin/attempts/{attempt_id}/frame", summary="Vé xem ảnh lượt bị từ chối tại server biên (ghi audit)")
def attempt_frame(attempt_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    a = db.get(CheckinAttempt, attempt_id)
    if a is None:
        raise HTTPException(404, "Không có lượt này")
    _get_exam(db, a.exam_id, user)
    out = _frame_url(db, user, a)
    audit(db, user, "checkin.view_attempt", f"attempt:{a.id}", client_ip(request))
    db.commit()
    return out


# ======================================================================== nghi vấn gian lận

@router.get("/events/{event_id}/frame", summary="Vé xem ảnh bằng chứng tại server biên (kiểm tra quyền + ghi audit mỗi lượt xem)")
def event_frame(event_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    e = db.get(SuspicionEvent, event_id)
    if e is None:
        raise HTTPException(404, "Không có sự kiện")
    _get_exam(db, e.exam_id, user)
    out = _frame_url(db, user, e)
    audit(db, user, "exam.view_frame", f"event:{e.id}", client_ip(request))
    db.commit()
    return out


@router.patch("/events/{event_id}", summary="Giám thị xác nhận / bác bỏ. Xác nhận -> còi liên tục")
def review_event(event_id: int, body: ReviewIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    e = db.get(SuspicionEvent, event_id)
    if e is None:
        raise HTTPException(404, "Không có sự kiện")
    s = _get_exam(db, e.exam_id, user)
    if e.review_status != "pending":
        raise HTTPException(409, f"Sự kiện đã được xử lý ({e.review_status})")
    e.review_status, e.review_note, e.reviewed_by = body.decision, body.note, user.id
    for a in db.query(Alert).filter(Alert.event_id == e.id, Alert.state != "resolved"):
        a.state, a.acked_by, a.resolved_at = "resolved", user.id, utcnow()
        hub.publish_threadsafe("alert.updated", alert_service.alert_dict(a), room_id=a.room_id)

    audit(db, user, f"exam.event_{body.decision}", f"event:{e.id}", client_ip(request), note=body.note)
    db.commit()

    # Xác nhận -> còi liên tục ở mọi node chấp hành của phòng, do server biên thực hiện (tự tắt sau alarm_auto_stop_s).
    # Biên ngoại tuyến không làm hỏng quyết định đã lưu: chỉ báo lại là chưa bật được còi.
    cmd_ids, buzzer_error = [], None
    if body.decision == "confirmed":
        edges = {d.edge_id for d in db.query(Device).filter(Device.room_id == s.room_id, Device.type == "actuator")}
        edges.discard(None)
        if not edges:
            buzzer_error = "Phòng chưa có node còi"
        for edge_id in edges:
            try:
                cmd_ids += edge_link.call(edge_id, "room_buzzer", {
                    "room_code": s.room.code, "pattern": "continuous", "issued_by": f"user:{user.id}",
                    "auto_stop_s": settings.alarm_auto_stop_s})["commands"]
            except HTTPException as err:
                buzzer_error = str(err.detail)
    out = event_dict(e, s.room_id)
    hub.publish_threadsafe("exam.event_reviewed", out, room_id=s.room_id, session_kind="exam", session_id=s.id)
    return {**out, "commands": cmd_ids, "buzzer_error": buzzer_error}
