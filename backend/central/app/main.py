"""
Server trung tâm của hệ thống phòng học thông minh (FastAPI).

Nhiệm vụ: người dùng + phân quyền, phòng/lớp/sinh viên, kho vector khuôn mặt (Qdrant), ca thi, cảnh báo, audit,
lịch sử telemetry và dashboard web. KHÔNG nói chuyện trực tiếp với thiết bị, KHÔNG nhận hay lưu ảnh:
mọi thiết bị do server biên (backend/edge/) quản lý, biên chỉ gửi embedding + metadata lên đây.

Chuẩn bị (1 lần):  cd deployment/central && docker compose up -d
Chạy (ở thư mục gốc):  python -m uvicorn backend.central.app.main:app --host 0.0.0.0 --port 9000

API docs: http://localhost:9000/docs     Dashboard: frontend/dashboard (npm run dev) hoặc bản build tại "/"
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.central.app.core.config import CENTRAL_DIR, settings
from backend.central.app.core.db import Base, engine, session_scope
from backend.central.app.core.security import hash_password
from backend.central.app.models import AlertRule, Role, User
from backend.central.app.routers import auth, devices, edge, ops, people, sessions, users
from backend.central.app.services import edge_events, edge_link
from backend.central.app.services.hub import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("central")

DEFAULT_RULES = [
    dict(metric="temperature_c", operator=">", threshold=30, duration_s=60, severity="medium",
         message="Phong nong: nen giam nhiet do dieu hoa"),
    dict(metric="temperature_c", operator="<", threshold=20, duration_s=60, severity="low",
         message="Phong lanh: nen tang nhiet do dieu hoa"),
    dict(metric="humidity_pct", operator=">", threshold=80, duration_s=120, severity="low",
         message="Do am cao: nen bat che do hut am"),
]


def bootstrap():
    Base.metadata.create_all(engine)
    with session_scope() as db:
        if not db.query(User).filter(User.role == Role.ADMIN).first():
            db.add(User(email=settings.admin_email, full_name="Quản trị viên", role=Role.ADMIN,
                        password_hash=hash_password(settings.admin_password)))
            log.warning("Đã tạo tài khoản admin %s - hãy đổi mật khẩu mặc định ngay", settings.admin_email)
        if not db.query(AlertRule).first():
            db.add_all(AlertRule(**r) for r in DEFAULT_RULES)


def prune_events():
    with session_scope() as db:
        edge_events.prune(db)


async def periodic(fn, every_s: float):
    while True:
        try:
            await run_in_threadpool(fn)
        except Exception:
            log.exception("Lỗi tác vụ định kỳ %s", fn.__name__)
        await asyncio.sleep(every_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_in_threadpool(bootstrap)
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)
    edge_link.bind_loop(loop)
    edge_link.on_change = lambda edge_id, online: hub.publish_threadsafe(
        "edge.status", {"edge_id": edge_id, "online": online})
    task = asyncio.create_task(periodic(prune_events, 3600))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Smart Classroom - Server trung tâm", version="3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (auth, users, people, devices, sessions, ops, edge):
    app.include_router(r.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"ok": True, "edges_online": sorted(e for e in settings.edge_token_map() if edge_link.online(e))}


WEB_DIST = CENTRAL_DIR.parents[1] / "frontend" / "dashboard" / "dist"
if (WEB_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")
