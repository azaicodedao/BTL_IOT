"""Dữ liệu cục bộ của server biên (SQLite).

Biên là nơi duy nhất giữ: khoá thiết bị (bản băm), yêu cầu đăng ký, lệnh điều khiển, ẢNH và hàng đợi AI.
Người dùng, sinh viên, ca thi, cảnh báo, lịch sử telemetry nằm ở server trung tâm.
"""

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from backend.edge.app.core.db import Base
from backend.shared.db import UtcDateTime, utcnow


class Room(Base):
    """Bản sao danh sách phòng lấy từ trung tâm (mất kết nối trung tâm vẫn chọn được phòng khi gán thiết bị)."""
    __tablename__ = "rooms"

    code = Column(String(20), primary_key=True)
    name = Column(String(100))


class DeviceState:
    REGISTERED = "registered"          # đã đăng ký, chưa có khoá (chờ gán phòng / chờ thiết bị nhận khoá)
    PROVISIONED = "provisioned"        # đã cấp khoá, chưa kết nối MQTT
    ACTIVE = "active"
    OFFLINE = "offline"
    FAULT = "fault"
    MAINTENANCE = "maintenance"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(40), unique=True, nullable=False, index=True)    # = tên đăng nhập MQTT
    hardware_uid = Column(String(40), unique=True)                             # MAC / định danh phần cứng
    type = Column(String(10), nullable=False)                                  # camera | actuator
    room_code = Column(String(20))
    state = Column(String(16), nullable=False, default=DeviceState.REGISTERED)
    fw_version = Column(String(30))
    hw_version = Column(String(60))
    last_seen = Column(UtcDateTime)
    token_hash = Column(String(64))                                            # token HTTP gửi ảnh
    online = Column(Boolean, default=False)
    rssi = Column(Integer)
    config = Column(JSON, default=dict)
    created_at = Column(UtcDateTime, default=utcnow)

    credential = relationship("DeviceCredential", uselist=False, back_populates="device", lazy="joined",
                              cascade="all, delete-orphan")


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id = Column(Integer, primary_key=True)
    device_id = Column(ForeignKey("devices.id", ondelete="CASCADE"), unique=True)
    mqtt_user = Column(String(40), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    created_at = Column(UtcDateTime, default=utcnow)
    revoked_at = Column(UtcDateTime)

    device = relationship("Device", back_populates="credential")


class EnrollmentStatus:
    PENDING = "pending"            # thiết bị tự báo danh, chờ quản trị viên đăng ký
    REGISTERED = "registered"      # đã đăng ký (có device_id), chờ gán phòng
    APPROVED = "approved"          # đã gán phòng: thiết bị được phép nhận khoá
    REJECTED = "rejected"


class Enrollment(Base):
    """Yêu cầu đăng ký do thiết bị TỰ gửi (POST /api/v1/device-enrollments/announce)."""
    __tablename__ = "device_enrollment_requests"

    id = Column(Integer, primary_key=True)
    hardware_uid = Column(String(40), unique=True, nullable=False, index=True)
    factory_name = Column(String(60))
    device_type = Column(String(30))                    # chuỗi firmware gửi: esp32_cam, esp8266_actuator...
    type = Column(String(10), nullable=False)           # camera | actuator
    firmware_version = Column(String(30))
    ip = Column(String(45))
    status = Column(String(12), nullable=False, default=EnrollmentStatus.PENDING)
    claim_hash = Column(String(64))                     # băm claim token: chỉ thiết bị giữ token mới nhận được khoá
    pair_code = Column(String(6))                       # mã ghép hiện trên thiết bị, quản trị viên đối chiếu
    # Thiết bị ĐÃ đăng ký báo danh lại mà không có claim token (factory reset / giả mạo MAC):
    # giữ token mới ở trạng thái chờ tới khi quản trị viên chấp nhận.
    pending_claim_hash = Column(String(64))
    pending_pair_code = Column(String(6))
    announces = Column(Integer, default=1)
    device_id = Column(String(40))
    first_seen = Column(UtcDateTime, default=utcnow)
    last_seen = Column(UtcDateTime, default=utcnow)


class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True)
    cmd_id = Column(String(20), unique=True, nullable=False)
    device_id = Column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(20), nullable=False)        # buzzer | display
    params = Column(JSON, default=dict)
    status = Column(String(10), default="pending")     # pending | sent | acked | failed | timeout
    attempts = Column(Integer, default=0)
    issued_by = Column(String(60))                     # "user:3" (qua trung tâm) | "console" | "checkin_worker" | "auto-stop"
    ts_created = Column(UtcDateTime, default=utcnow)
    ts_sent = Column(UtcDateTime)
    ts_ack = Column(UtcDateTime)
    expires_at = Column(UtcDateTime)
    result = Column(String(60))


class Frame(Base):
    """Ảnh lấy mẫu cho AI / ảnh bằng chứng. Chỉ tồn tại ở biên, tự xoá khi hết hạn."""
    __tablename__ = "frames"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(40), nullable=False, index=True)
    ts = Column(UtcDateTime, default=utcnow)
    file_path = Column(String(255), nullable=False)    # tương đối so với storage_dir
    expires_at = Column(UtcDateTime, index=True)


class FrameJob(Base):
    __tablename__ = "frame_jobs"
    __table_args__ = (Index("ix_frame_jobs_pending", "kind", "status", "id"),)

    id = Column(Integer, primary_key=True)
    frame_id = Column(ForeignKey("frames.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(12), nullable=False)          # checkin | exam
    exam_id = Column(Integer, nullable=False)
    status = Column(String(12), default="pending")     # pending | processing | done | failed | skipped
    error = Column(Text)
    created_at = Column(UtcDateTime, default=utcnow)
    finished_at = Column(UtcDateTime)


class ExamState(Base):
    """Bản sao ca thi đang diễn ra của các phòng do biên này phục vụ (đồng bộ từ trung tâm).

    Vector khuôn mặt của danh sách dự thi nằm ở data/gallery/exam_<id>.npz (xem services/gallery.py).
    """
    __tablename__ = "exam_state"

    exam_id = Column(Integer, primary_key=True)
    room_code = Column(String(20), nullable=False, index=True)
    name = Column(String(120))
    phase = Column(String(12), nullable=False)         # checkin | in_progress
    roster_hash = Column(String(64))                   # hash danh sách + vector đã tải về
    students = Column(JSON, default=dict)              # {"<student_id>": [mã, họ tên]}
    decided = Column(JSON, default=dict)               # {"<student_id>": admitted|denied} theo trung tâm
    updated_at = Column(UtcDateTime, default=utcnow, onupdate=utcnow)


class CheckinHit(Base):
    """Bộ đếm xác nhận: chỉ cho vào khi 1 SV được nhận ra ở 2 khung liên tiếp."""
    __tablename__ = "checkin_hits"

    exam_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, primary_key=True)
    hits = Column(Integer, default=0)
    updated_at = Column(UtcDateTime, default=utcnow)


class CheckinDone(Base):
    """SV đã được AI cho vào trong ca (mỗi SV đúng 1 lần -> còi chỉ chíp 1 lần)."""
    __tablename__ = "checkin_done"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_checkin_once"),)

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, nullable=False)
    student_id = Column(Integer, nullable=False)
    confidence = Column(Float)
    frame_id = Column(Integer)
    ts = Column(UtcDateTime, default=utcnow)


class SuspicionState(Base):
    __tablename__ = "suspicion_state"

    exam_id = Column(Integer, primary_key=True)
    type = Column(String(20), primary_key=True)        # phone | head_turn
    streak = Column(Integer, default=0)
    updated_at = Column(UtcDateTime, default=utcnow)


class Outbox(Base):
    """Sự kiện chờ gửi lên trung tâm (bền vững qua mất mạng / khởi động lại)."""
    __tablename__ = "outbox"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(32), unique=True, nullable=False)
    type = Column(String(30), nullable=False)
    data = Column(JSON, nullable=False)
    ts = Column(UtcDateTime, default=utcnow)
    expires_at = Column(UtcDateTime)                   # sự kiện tức thời (checkin.seen...) quá hạn thì bỏ


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    actor = Column(String(60))                         # console | user:<id> (qua trung tâm) | system
    action = Column(String(60), nullable=False)
    target = Column(String(120))
    ip = Column(String(45))
    ts = Column(UtcDateTime, default=utcnow, index=True)
    detail = Column(JSON)
