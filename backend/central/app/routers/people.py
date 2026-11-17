"""Phòng học, lớp, sinh viên và dữ liệu khuôn mặt (mục 11.2).

Ảnh khuôn mặt KHÔNG gửi lên trung tâm: dashboard xin vé rồi tải ảnh thẳng tới server biên; biên tính vector
và chỉ gửi vector lên (POST /api/v1/edge/faces). Ảnh cắt để xem lại cũng nằm ở biên.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.central.app.core.db import get_db
from backend.central.app.core.deps import admin_only, any_user, client_ip, operator_up, room_ids
from backend.central.app.models import Class, Edge, Room, Student
from backend.central.app.services import edge_link, face_store
from backend.central.app.services.audit import audit
from backend.shared.db import utcnow

router = APIRouter(prefix="/api/v1", tags=["rooms, classes, students"])


# ================================================================ phòng

class RoomIn(BaseModel):
    code: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,20}$")
    name: str
    building: Optional[str] = None


def room_dict(r: Room) -> dict:
    return {"id": r.id, "code": r.code, "name": r.name, "building": r.building}


@router.get("/rooms")
def list_rooms(db=Depends(get_db), user=Depends(any_user)):
    q = db.query(Room).order_by(Room.code)
    allowed = room_ids(user)
    if allowed is not None:
        q = q.filter(Room.id.in_(allowed or [-1]))
    return [room_dict(r) for r in q]


@router.post("/rooms", status_code=201)
def create_room(body: RoomIn, request: Request, db=Depends(get_db), admin=Depends(admin_only)):
    if db.query(Room).filter(Room.code == body.code).first():
        raise HTTPException(409, "Mã phòng đã tồn tại")
    r = Room(code=body.code, name=body.name, building=body.building, owner_id=admin.id)
    db.add(r)
    db.flush()
    audit(db, admin, "room.create", body.code, client_ip(request))
    db.commit()
    return room_dict(r)


# ================================================================ lớp

class ClassIn(BaseModel):
    code: str = Field(..., max_length=30)
    name: str


class ClassStudentsIn(BaseModel):
    student_ids: list[int]


def class_dict(c: Class, full: bool = False) -> dict:
    out = {"id": c.id, "code": c.code, "name": c.name, "student_count": len(c.students)}
    if full:
        out["students"] = [student_dict(s) for s in sorted(c.students, key=lambda s: s.student_code)]
    return out


@router.get("/classes")
def list_classes(db=Depends(get_db), _=Depends(any_user)):
    return [class_dict(c) for c in db.query(Class).order_by(Class.code)]


@router.get("/classes/{class_id}")
def get_class(class_id: int, db=Depends(get_db), _=Depends(any_user)):
    c = db.get(Class, class_id) or _404("lớp")
    return class_dict(c, full=True)


@router.post("/classes", status_code=201)
def create_class(body: ClassIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    if db.query(Class).filter(Class.code == body.code).first():
        raise HTTPException(409, "Mã lớp đã tồn tại")
    c = Class(code=body.code, name=body.name)
    db.add(c)
    db.flush()
    audit(db, user, "class.create", body.code, client_ip(request))
    db.commit()
    return class_dict(c)


@router.put("/classes/{class_id}/students")
def set_class_students(class_id: int, body: ClassStudentsIn, request: Request, db=Depends(get_db),
                       user=Depends(operator_up)):
    c = db.get(Class, class_id) or _404("lớp")
    c.students = db.query(Student).filter(Student.id.in_(body.student_ids)).all()
    audit(db, user, "class.set_students", c.code, client_ip(request), count=len(c.students))
    db.commit()
    return class_dict(c, full=True)


# ================================================================ sinh viên

class StudentIn(BaseModel):
    student_code: str = Field(..., max_length=30)
    full_name: str
    consent: bool = False                     # đã ký đồng ý thu thập khuôn mặt
    class_ids: list[int] = []


class StudentPatch(BaseModel):
    full_name: Optional[str] = None
    active: Optional[bool] = None
    consent: Optional[bool] = None


def student_dict(s: Student) -> dict:
    return {"id": s.id, "student_code": s.student_code, "full_name": s.full_name, "active": s.active,
            "consent_at": s.consent_at, "face_samples": s.face_samples or 0,
            "classes": [{"id": c.id, "code": c.code} for c in sorted(s.classes, key=lambda c: c.code)]}


class StudentClassesIn(BaseModel):
    class_ids: list[int]


@router.get("/students")
def list_students(q: Optional[str] = None, db=Depends(get_db), _=Depends(operator_up)):
    query = db.query(Student).order_by(Student.student_code)
    if q:
        like = f"%{q}%"
        query = query.filter(Student.student_code.ilike(like) | Student.full_name.ilike(like))
    return [student_dict(s) for s in query.limit(500)]


@router.post("/students", status_code=201)
def create_student(body: StudentIn, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    if db.query(Student).filter(Student.student_code == body.student_code).first():
        raise HTTPException(409, "MSSV đã tồn tại")
    s = Student(student_code=body.student_code, full_name=body.full_name,
                consent_at=utcnow() if body.consent else None)
    s.classes = db.query(Class).filter(Class.id.in_(body.class_ids)).all() if body.class_ids else []
    db.add(s)
    db.flush()
    audit(db, user, "student.create", s.student_code, client_ip(request), consent=body.consent,
          classes=[c.code for c in s.classes])
    db.commit()
    return student_dict(s)


@router.patch("/students/{student_id}")
def update_student(student_id: int, body: StudentPatch, request: Request, db=Depends(get_db),
                   user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    if body.full_name is not None:
        s.full_name = body.full_name
    if body.active is not None:
        s.active = body.active
    if body.consent is not None:
        s.consent_at = utcnow() if body.consent else None
    audit(db, user, "student.update", s.student_code, client_ip(request), **body.model_dump(exclude_unset=True))
    db.commit()
    return student_dict(s)


@router.put("/students/{student_id}/classes", summary="Đặt danh sách lớp của 1 sinh viên")
def set_student_classes(student_id: int, body: StudentClassesIn, request: Request, db=Depends(get_db),
                        user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    s.classes = db.query(Class).filter(Class.id.in_(body.class_ids)).all() if body.class_ids else []
    audit(db, user, "student.set_classes", s.student_code, client_ip(request), classes=[c.code for c in s.classes])
    db.commit()
    return student_dict(s)


# ---------------------------------------------------------------- khuôn mặt
# Mỗi mẫu = 1 vector trong Qdrant (ở trung tâm) + 1 ảnh cắt khuôn mặt lưu tại server biên đã đăng ký mẫu đó.

def _edges_online(db) -> list[Edge]:
    return [e for e in db.query(Edge).order_by(Edge.edge_id) if edge_link.online(e.edge_id)]


def _resync_all(db):
    """Vector đổi -> dấu vân tay danh sách dự thi đổi: nhắc mọi server biên tải lại."""
    edge_link.poke(e.edge_id for e in _edges_online(db))


def _forget_at_edges(db, student_id: int, sample_id: str | None = None):
    """Xoá ảnh cắt lưu tại biên (cố gắng hết sức; biên ngoại tuyến thì quản trị viên biên xoá sau)."""
    for e in _edges_online(db):
        try:
            edge_link.call(e.edge_id, "face_delete", {"student_id": student_id, "sample_id": sample_id})
        except HTTPException:
            pass


@router.get("/students/{student_id}/face-samples", summary="Danh sách mẫu khuôn mặt đã đăng ký")
def list_face_samples(student_id: int, db=Depends(get_db), _=Depends(operator_up)):
    db.get(Student, student_id) or _404("sinh viên")
    samples = face_store.list_samples(student_id)
    known = {e.edge_id for e in db.query(Edge) if e.public_url}
    for x in samples:
        x["has_image"] = x["edge_id"] in known
    return samples


@router.get("/students/{student_id}/face-samples/{sample_id}/image",
            summary="Vé xem ảnh khuôn mặt của 1 mẫu tại server biên (ghi audit)")
def face_sample_image(student_id: int, sample_id: str, request: Request, db=Depends(get_db),
                      user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    sample = next((x for x in face_store.list_samples(student_id) if x["id"] == sample_id), None)
    if sample is None or not sample["edge_id"]:
        raise HTTPException(404, "Không có ảnh cho mẫu này")
    out = {"url": edge_link.ticket_url(db.get(Edge, sample["edge_id"]),
                                       f"/faces/{student_id}/samples/{sample_id}/image", f"faces:{student_id}", user.id)}
    audit(db, user, "student.view_face", s.student_code, client_ip(request), sample=sample_id)
    db.commit()
    return out


@router.post("/students/{student_id}/face-ticket",
             summary="Vé đăng ký khuôn mặt: trình duyệt gửi ảnh THẲNG tới server biên, biên chỉ gửi vector lên trung tâm")
def face_ticket(student_id: int, request: Request, edge_id: Optional[str] = None, db=Depends(get_db),
                user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    if s.consent_at is None:
        raise HTTPException(409, "Sinh viên chưa đồng ý thu thập dữ liệu khuôn mặt (consent)")
    online = _edges_online(db)
    edge = next((e for e in online if e.edge_id == edge_id), None) if edge_id else (online[0] if online else None)
    if edge is None:
        raise HTTPException(503, "Chưa có server biên nào đang kết nối để xử lý ảnh khuôn mặt")
    audit(db, user, "student.face_ticket", s.student_code, client_ip(request), edge=edge.edge_id)
    db.commit()
    return {"edge_id": edge.edge_id,
            "upload_url": edge_link.ticket_url(edge, f"/faces/{s.id}/samples", f"faces:{s.id}", user.id)}


@router.post("/students/{student_id}/face-refresh",
             summary="Sau khi biên đăng ký xong: trả sinh viên mới nhất + nhắc các biên tải lại vector")
def face_refresh(student_id: int, db=Depends(get_db), _=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    _resync_all(db)
    return student_dict(s)


@router.delete("/students/{student_id}/face-samples/{sample_id}", summary="Xoá 1 mẫu khuôn mặt (ảnh mờ, sai người...)")
def delete_face_sample(student_id: int, sample_id: str, request: Request, db=Depends(get_db),
                       user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    if not (0 < len(sample_id) <= 40 and sample_id.replace("-", "").isalnum()):
        raise HTTPException(404, "Không có mẫu này")
    s.face_samples = face_store.delete_point(s.id, sample_id)
    audit(db, user, "student.face_delete_sample", s.student_code, client_ip(request), sample=sample_id)
    db.commit()
    _forget_at_edges(db, s.id, sample_id)
    _resync_all(db)
    return student_dict(s)


@router.delete("/students/{student_id}/face-data", summary="Xoá toàn bộ dữ liệu khuôn mặt (rút đồng ý)")
def delete_face_data(student_id: int, request: Request, db=Depends(get_db), user=Depends(operator_up)):
    s = db.get(Student, student_id) or _404("sinh viên")
    face_store.delete_student(s.id)
    s.face_samples, s.consent_at = 0, None
    audit(db, user, "student.face_delete", s.student_code, client_ip(request))
    db.commit()
    _forget_at_edges(db, s.id)
    _resync_all(db)
    return student_dict(s)


def _404(what):
    raise HTTPException(404, f"Không có {what}")
