"""
Server biên (edge) của hệ thống phòng học thông minh - đặt trong LAN của phòng học.

Nhiệm vụ: broker MQTT + vòng đời thiết bị (thiết bị tự đăng ký, không nạp lại firmware), nhận ảnh, chạy AI,
điều khiển còi/LCD tại chỗ, và chỉ gửi embedding + metadata lên server trung tâm.

Chuẩn bị (1 lần):  cd deployment/edge && python init_mqtt.py && docker compose up -d
Chạy (ở thư mục gốc):  python -m uvicorn backend.edge.app.main:app --host 0.0.0.0 --port 8000
Worker AI:             python -m backend.edge.app.workers.face_worker   |   python -m backend.edge.app.workers.exam_worker

Bảng điều khiển thiết bị: http://<ip-server-biên>:8000/      API docs: /docs
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.edge.app.core.config import EDGE_DIR, settings
from backend.edge.app.core.db import Base, engine, session_scope
from backend.edge.app.models import Frame
from backend.edge.app.routers import console, device_api, internal
from backend.edge.app.services import (command_service, device_service, exam_state, mdns, ops,  # noqa: F401 (đăng ký handler)
                               telemetry_service, uplink)
from backend.edge.app.services.mqtt_bridge import bridge
from backend.edge.app.services.camera_sources import manager as camera_sources
from backend.shared.db import utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("edge")


def cleanup_frames():
    """Xoá ảnh quá hạn lưu trữ (ảnh khuôn mặt là dữ liệu nhạy cảm)."""
    with session_scope() as db:
        old = db.query(Frame).filter(Frame.expires_at < utcnow()).limit(5000).all()
        for f in old:
            (settings.storage_dir / f.file_path).unlink(missing_ok=True)
            db.delete(f)
        if old:
            log.info("Đã xoá %d ảnh hết hạn", len(old))


async def periodic(fn, every_s: float):
    while True:
        try:
            await run_in_threadpool(fn)
        except Exception:
            log.exception("Lỗi tác vụ định kỳ %s", fn.__name__)
        await asyncio.sleep(every_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    if settings.admin_password == "edge-admin":
        log.warning("Đang dùng mật khẩu bảng điều khiển mặc định - đặt EDGE_ADMIN_PASSWORD trong .env ở gốc dự án")
    command_service.bind_loop(asyncio.get_running_loop())
    bridge.handlers.update({
        "telemetry": telemetry_service.on_telemetry,
        "heartbeat": device_service.on_heartbeat,
        "status": device_service.on_status,
        "event": device_service.on_event,
        "cmd/ack": command_service.on_ack,
    })
    bridge.start()
    uplink.start()
    await run_in_threadpool(mdns.start)
    await run_in_threadpool(camera_sources.start)
    tasks = [asyncio.create_task(periodic(device_service.check_offline, 5)),
             asyncio.create_task(periodic(cleanup_frames, 3600)),
             asyncio.create_task(uplink.link_loop())]
    yield
    await run_in_threadpool(camera_sources.stop)
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    uplink.stop()
    mdns.stop()
    bridge.stop()


app = FastAPI(title="Smart Classroom - Server biên", version="1.0", lifespan=lifespan)
# Trình duyệt của dashboard trung tâm lấy ảnh / gửi ảnh đăng ký khuôn mặt thẳng tới biên (kèm vé) -> cần CORS
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (device_api, console, internal):
    app.include_router(r.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"ok": True, "edge_id": settings.edge_id, "mqtt": bridge.connected.is_set(),
            "central_link": uplink.state["link"]}


app.mount("/", StaticFiles(directory=EDGE_DIR.parents[1] / "frontend" / "edge-console", html=True), name="console")
