"""API nội bộ cho worker AI chạy cùng máy (service token). Worker chỉ báo KẾT QUẢ; ảnh nằm lại ở biên."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.edge.app.core.db import get_db
from backend.edge.app.core.deps import service_only
from backend.edge.app.models import ExamState
from backend.edge.app.services import command_service, uplink
from backend.shared.db import utcnow

router = APIRouter(prefix="/internal", tags=["nội bộ (worker AI)"], dependencies=[Depends(service_only)])


class AdmittedIn(BaseModel):
    exam_id: int
    student_id: int
    confidence: float
    frame_id: Optional[int] = None


class EmitIn(BaseModel):
    type: Literal["checkin.rejected", "checkin.seen", "exam.suspicion"]
    data: dict
    ttl_s: Optional[float] = None


@router.post("/checkin-admitted", summary="SV hợp lệ được cho vào: còi chíp 1 lần NGAY tại biên + báo trung tâm")
def checkin_admitted(body: AdmittedIn, db=Depends(get_db)):
    exam = db.get(ExamState, body.exam_id)
    if exam is None:
        raise HTTPException(404, "Ca thi không còn diễn ra")
    uplink.emit(db, "checkin.admitted", {"exam_id": body.exam_id, "student_id": body.student_id,
                                         "confidence": body.confidence, "frame_ref": body.frame_id,
                                         "ts": utcnow().isoformat()})
    db.commit()
    return {"commands": command_service.buzz_room(exam.room_code, "once", "checkin_worker")}


@router.post("/emit", summary="Đẩy kết quả AI lên trung tâm (lượt bị từ chối, đang xác nhận, nghi vấn gian lận)")
def emit(body: EmitIn, db=Depends(get_db)):
    uplink.emit(db, body.type, {**body.data, "ts": utcnow().isoformat()}, ttl_s=body.ttl_s)
    db.commit()
    return {"ok": True}
