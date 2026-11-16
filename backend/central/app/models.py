"""Mô hình dữ liệu của server trung tâm (PostgreSQL, ERD mục 9.1). Vector khuôn mặt nằm ở Qdrant.

Trung tâm KHÔNG lưu ảnh và không giữ khoá thiết bị: bảng devices là bản phản chiếu trạng thái do server biên đẩy lên;
ảnh bằng chứng nằm ở biên, ở đây chỉ có số tham chiếu (edge_id, frame_ref).
"""

from sqlalchemy import (JSON, BigInteger, Boolean, Column, Float, ForeignKey, Index, Integer,
                        String, Table, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from backend.central.app.core.db import Base
from backend.shared.db import UtcDateTime, utcnow

BigId = BigInteger().with_variant(Integer, "sqlite")      # SQLite chỉ tự tăng với INTEGER PRIMARY KEY


# ============================================================== người dùng

class Role:
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    ALL = (ADMIN, OPERATOR, VIEWER)


user_rooms = Table(
    "user_rooms", Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("room_id", ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(100), nullable=False)
    full_name = Column(String(120), nullable=False)
    role = Column(String(10), nullable=False, default=Role.VIEWER)
    status = Column(String(10), nullable=False, default="active")          # active | locked
    created_at = Column(UtcDateTime, default=utcnow)

    rooms = relationship("Room", secondary=user_rooms, lazy="selectin")


class RevokedToken(Base):
    """Refresh token đã đăng xuất / bị thu hồi."""
    __tablename__ = "revoked_tokens"

    jti = Column(String(40), primary_key=True)
    revoked_at = Column(UtcDateTime, default=utcnow)


# ============================================================== phòng, lớp, sinh viên

class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)                 # "A101" (dùng trong topic MQTT)
    name = Column(String(100), nullable=False)
    building = Column(String(100))
    owner_id = Column(ForeignKey("users.id"), nullable=True)

    devices = relationship("Device", back_populates="room")


class_students = Table(
    "class_students", Base.metadata,
    Column("class_id", ForeignKey("classes.id", ondelete="CASCADE"), primary_key=True),
    Column("student_id", ForeignKey("students.id", ondelete="CASCADE"), primary_key=True),
)


class Class(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False)
    name = Column(String(120), nullable=False)

    students = relationship("Student", secondary=class_students, lazy="selectin", back_populates="classes")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True)
    student_code = Column(String(30), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False)
    consent_at = Column(UtcDateTime)                       # thời điểm đồng ý thu thập khuôn mặt
    active = Column(Boolean, default=True)
    face_samples = Column(Integer, default=0)           # số vector đang có trong Qdrant

    classes = relationship("Class", secondary=class_students, lazy="selectin", back_populates="students")


# ============================================================== thiết bị

class Edge(Base):
    """Server biên đã từng kết nối (danh tính + khoá chung cấu hình ở CENTRAL_EDGE_TOKENS)."""
    __tablename__ = "edges"

    edge_id = Column(String(40), primary_key=True)
    name = Column(String(100))
    public_url = Column(String(200))            # địa chỉ trình duyệt dùng để lấy ảnh / mở bảng điều khiển biên
    version = Column(String(30))
    last_seen = Column(UtcDateTime)


class EdgeEvent(Base):
    """Sự kiện đã xử lý, để loại trùng khi biên gửi lại (at-least-once)."""
    __tablename__ = "edge_events"

    edge_id = Column(String(40), primary_key=True)
    event_id = Column(String(32), primary_key=True)
    ts = Column(UtcDateTime, default=utcnow, index=True)


class DeviceState:
    REGISTERED = "registered"
    PROVISIONED = "provisioned"
    ACTIVE = "active"
    OFFLINE = "offline"
    FAULT = "fault"
    MAINTENANCE = "maintenance"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"


class Device(Base):
    """Bản phản chiếu thiết bị do server biên quản lý (cập nhật qua sự kiện device.upsert)."""
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(40), unique=True, nullable=False, index=True)
    edge_id = Column(String(40), index=True)                                   # server biên đang quản lý
    type = Column(String(10), nullable=False)                                  # camera | actuator
    room_id = Column(ForeignKey("rooms.id"), nullable=True)
    state = Column(String(16), nullable=False, default=DeviceState.REGISTERED)
    fw_version = Column(String(30))
    hw_version = Column(String(60))
    last_seen = Column(UtcDateTime)
    online = Column(Boolean, default=False)
    rssi = Column(Integer)
    config = Column(JSON, default=dict)
    created_at = Column(UtcDateTime, default=utcnow)

    room = relationship("Room", back_populates="devices", lazy="joined")


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (UniqueConstraint("device_id", "msg_id", name="uq_telemetry_msg"),
                      Index("ix_telemetry_device_ts", "device_id", "ts"))

    id = Column(BigId, primary_key=True)
    device_id = Column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    ts = Column(UtcDateTime, nullable=False)
    msg_id = Column(String(40), nullable=False)
    temperature_c = Column(Float)
    humidity_pct = Column(Float)


# ============================================================== ca thi
# Một ca thi = 1 phòng + 1 danh sách dự thi (lớp), đi qua các giai đoạn:
#   checkin     : camera ở cửa, AI (tại biên) đối chiếu khuôn mặt với danh sách dự thi -> cho vào phòng
#   in_progress : camera giám sát gian lận trong phòng
#   closed      : kết thúc; SV chưa vào phòng -> vắng thi

class ExamPhase:
    CHECKIN = "checkin"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id = Column(Integer, primary_key=True)
    room_id = Column(ForeignKey("rooms.id"), nullable=False)
    class_id = Column(ForeignKey("classes.id"), nullable=True)          # danh sách dự thi
    name = Column(String(120))
    opened_by = Column(ForeignKey("users.id"))
    started_at = Column(UtcDateTime, default=utcnow)            # mở ca = bắt đầu kiểm tra vào phòng
    exam_started_at = Column(UtcDateTime)                    # bắt đầu làm bài (giám sát gian lận)
    ended_at = Column(UtcDateTime)
    status = Column(String(12), default=ExamPhase.CHECKIN)               # checkin | in_progress | closed

    room = relationship("Room", lazy="joined")
    klass = relationship("Class", lazy="joined")


class CheckinRecord(Base):
    """Kết quả kiểm tra vào phòng của 1 SV trong danh sách dự thi (mỗi SV đúng 1 bản ghi / ca)."""
    __tablename__ = "checkin_records"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_checkin_once"),)

    id = Column(Integer, primary_key=True)
    exam_id = Column(ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(ForeignKey("students.id"), nullable=False)
    ts = Column(UtcDateTime, default=utcnow)
    confidence = Column(Float)
    method = Column(String(8), default="auto")               # auto (AI) | manual (giám thị)
    status = Column(String(10), default="admitted")          # admitted | denied | absent
    edge_id = Column(String(40))                             # ảnh nằm ở server biên này
    frame_ref = Column(Integer)                              # số tham chiếu ảnh tại biên (không phải ảnh)
    note = Column(Text)

    student = relationship("Student", lazy="joined")


class CheckinAttempt(Base):
    """Lượt bị từ chối ở cửa: người lạ, hoặc SV không có trong danh sách dự thi (kèm ảnh để giám thị xem)."""
    __tablename__ = "checkin_attempts"

    id = Column(Integer, primary_key=True)
    exam_id = Column(ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_id = Column(String(40))                             # ảnh nằm ở server biên này
    frame_ref = Column(Integer)                              # số tham chiếu ảnh tại biên (không phải ảnh)
    result = Column(String(16), nullable=False)              # not_in_roster | unknown
    student_id = Column(ForeignKey("students.id", ondelete="SET NULL"))
    confidence = Column(Float)
    ts = Column(UtcDateTime, default=utcnow)

    student = relationship("Student", lazy="joined")


class SuspicionEvent(Base):
    __tablename__ = "suspicion_events"

    id = Column(Integer, primary_key=True)
    exam_id = Column(ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_id = Column(String(40))                             # ảnh nằm ở server biên này
    frame_ref = Column(Integer)                              # số tham chiếu ảnh tại biên (không phải ảnh)
    type = Column(String(20), nullable=False)
    score = Column(Float, nullable=False)
    student_code = Column(String(30))                        # nếu nhận ra được khuôn mặt
    detail = Column(JSON)                                    # bbox, góc yaw...
    review_status = Column(String(10), default="pending")    # pending | confirmed | dismissed
    reviewed_by = Column(ForeignKey("users.id"))
    review_note = Column(Text)
    ts = Column(UtcDateTime, default=utcnow)


# ============================================================== cảnh báo, lệnh, audit

class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True)
    metric = Column(String(20), nullable=False)       # temperature_c | humidity_pct | device_offline | suspicion
    operator = Column(String(2), default=">")         # > < >= <=
    threshold = Column(Float)
    duration_s = Column(Integer, default=0)           # vượt ngưỡng liên tục bao lâu mới cảnh báo
    severity = Column(String(8), default="medium")    # low | medium | high
    enabled = Column(Boolean, default=True)
    message = Column(String(200))


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    rule_id = Column(ForeignKey("alert_rules.id"))
    device_id = Column(ForeignKey("devices.id", ondelete="SET NULL"))
    room_id = Column(ForeignKey("rooms.id"))
    event_id = Column(ForeignKey("suspicion_events.id", ondelete="SET NULL"))
    kind = Column(String(20), nullable=False)          # temperature_c | humidity_pct | device_offline | suspicion
    severity = Column(String(8), nullable=False)
    message = Column(String(300), nullable=False)
    state = Column(String(10), default="open")         # open | acked | resolved
    acked_by = Column(ForeignKey("users.id"))
    ts = Column(UtcDateTime, default=utcnow, index=True)
    resolved_at = Column(UtcDateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BigId, primary_key=True)
    user_id = Column(ForeignKey("users.id", ondelete="SET NULL"))
    action = Column(String(60), nullable=False)
    target = Column(String(120))
    ip = Column(String(45))
    ts = Column(UtcDateTime, default=utcnow, index=True)
    detail = Column(JSON)
