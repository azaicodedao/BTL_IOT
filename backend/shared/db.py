"""Tiện ích CSDL dùng chung cho server trung tâm và server biên."""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Luôn lưu UTC và luôn trả về datetime có múi giờ.

    SQLite bỏ thông tin múi giờ khi đọc lại; nếu không chuẩn hoá thì phép so sánh
    `utcnow() > cột` ném TypeError (naive so với aware).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def make_engine(url: str):
    """Engine SQLAlchemy. SQLite: bật WAL + timeout để nhiều tiến trình (API, worker AI) dùng chung 1 file."""
    if url.startswith("sqlite"):
        engine = create_engine(url, future=True, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, future=True)


def make_session_tools(engine):
    """-> (SessionLocal, get_db dependency, session_scope context manager)."""
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def session_scope():
        """Dùng ngoài request (luồng MQTT, tác vụ nền, worker): commit khi xong, rollback khi lỗi."""
        db = SessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return SessionLocal, get_db, session_scope
