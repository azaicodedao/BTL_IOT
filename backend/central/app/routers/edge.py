"""
API dành cho SERVER BIÊN (xác thực: X-Edge-Id + Bearer khoá chung). Biên luôn là bên gọi lên.
Chỉ embedding + metadata đi qua đây; không có endpoint nào nhận ảnh.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from pydantic import BaseModel, Field

from backend.central.app.core.config import settings
from backend.central.app.core.db import get_db
from backend.central.app.core.deps import any_user, edge_auth
from backend.central.app.models import Device, DeviceState as S, Edge, ExamSession, Room, Student
from backend.central.app.services import edge_events, edge_link, face_store, session_service
from backend.central.app.services.audit import audit
from backend.shared.db import utcnow
from backend.shared.security import same_secret

router = APIRouter(prefix="/api/v1", tags=["server biên"])


class EventsIn(BaseModel):
    events: list[dict] = Field(..., max_length=200)


class FaceSample(BaseModel):
    id: str = Field(..., pattern=r"^[0-9a-fA-F-]{32,36}$")
    vector: list[float] = Field(..., min_length=512, max_length=512)


class FacesIn(BaseModel):
    student_id: int
    samples: list[FaceSample] = Field(..., min_length=1, max_length=10)


class IdentifyIn(BaseModel):
    vector: list[float] = Field(..., min_length=512, max_length=512)


def edge_dict(e: Edge, db=None) -> dict:
    out = {"edge_id": e.edge_id, "name": e.name, "public_url": e.public_url, "version": e.version,
           "last_seen": e.last_seen, "online": edge_link.online(e.edge_id)}
    if db is not None:
        out["devices"] = db.query(Device).filter(Device.edge_id == e.edge_id, Device.state != S.DECOMMISSIONED).count()
    return out


@router.get("/edge/sync", summary="Biên kéo trạng thái mong muốn: danh sách phòng + ca thi đang diễn ra của nó")
def sync(public_url: Optional[str] = None, name: Optional[str] = None, version: Optional[str] = None,
         db=Depends(get_db), edge_id: str = Depends(edge_auth)):
    e = db.get(Edge, edge_id)
    if e is None:
        e = Edge(edge_id=edge_id)
        db.add(e)
    e.public_url, e.name, e.version, e.last_seen = public_url, name, version, utcnow()
    db.commit()
    return {"rooms": [{"code": r.code, "name": r.name} for r in db.query(Room).order_by(Room.code)],
            "exams": session_service.edge_state(db, edge_id), "server_time": utcnow().isoformat()}


@router.get("/edge/exams/{exam_id}/roster", summary="Danh sách dự thi + vector khuôn mặt cho AI tại biên")
def roster(exam_id: int, db=Depends(get_db), edge_id: str = Depends(edge_auth)):
    s = db.get(ExamSession, exam_id)
    serves = s is not None and db.query(Device).filter(Device.edge_id == edge_id, Device.room_id == s.room_id).first()
    if not serves or s.status not in session_service.ACTIVE:
        raise HTTPException(404, "Ca thi không diễn ra ở phòng do server biên này phục vụ")
    return session_service.roster_payload(s)


@router.post("/edge/events", summary="Biên đẩy sự kiện theo lô (thiết bị, telemetry, kết quả AI)")
def events(body: EventsIn, db=Depends(get_db), edge_id: str = Depends(edge_auth)):
    acknowledged = []
    results = edge_events.process(db, edge_id, body.events, acknowledged)
    return {"results": results, "acknowledged": acknowledged}


@router.post("/edge/faces", summary="Biên gửi VECTOR khuôn mặt vừa đăng ký (ảnh ở lại biên)")
def add_faces(body: FacesIn, db=Depends(get_db), edge_id: str = Depends(edge_auth)):
    s = db.get(Student, body.student_id)
    if s is None:
        raise HTTPException(404, "Không có sinh viên")
    if s.consent_at is None:
        raise HTTPException(409, "Sinh viên chưa đồng ý thu thập dữ liệu khuôn mặt (consent)")
    try:
        s.face_samples = face_store.add_vectors(s.id, s.student_code, edge_id, [(x.id, x.vector) for x in body.samples])
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, None, "student.face_enroll", s.student_code, None, added=len(body.samples), edge=edge_id)
    db.commit()
    return {"face_samples": s.face_samples}


@router.post("/edge/identify", summary="Biên hỏi bằng vector: khuôn mặt này có phải SV nào của trường không?")
def identify(body: IdentifyIn, db=Depends(get_db), _: str = Depends(edge_auth)):
    try:
        top = face_store.search(body.vector, None, limit=2)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not top:
        return {"matched": False, "score": 0.0}
    sid, code, score = top[0]
    second = top[1][2] if len(top) > 1 else 0.0
    matched = score >= settings.face_sim_threshold and score - second >= settings.face_margin
    return {"matched": matched, "student_id": sid if matched else None, "student_code": code if matched else None,
            "score": round(float(score), 4)}


@router.websocket("/edge/link")
async def link(websocket: WebSocket, x_edge_id: str = Header(""), authorization: str = Header("")):
    expected = settings.edge_token_map().get(x_edge_id)
    token = authorization.removeprefix("Bearer ").strip()
    if not expected or not token or not same_secret(token, expected):
        await websocket.close(code=4401)
        return
    await edge_link.serve(websocket, x_edge_id)


# ---------------------------------------------------------------- cho dashboard

@router.get("/edges", summary="Các server biên đã kết nối (trạng thái kênh điều khiển, địa chỉ bảng điều khiển)")
def list_edges(db=Depends(get_db), _=Depends(any_user)):
    return [edge_dict(e, db) for e in db.query(Edge).order_by(Edge.edge_id)]
