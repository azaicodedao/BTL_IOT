"""Hàng đợi job ảnh dùng chung cho face_worker / exam_worker (mục 9.3), chạy trên SQLite cục bộ của biên."""

import logging
import sys
import time
from datetime import timedelta

import requests

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import Frame, FrameJob
from backend.shared.db import utcnow

API = f"http://127.0.0.1:{settings.http_port}/internal"
STALE_AFTER_S = 30          # job chờ quá 30s (worker bị chậm / vừa khởi động) -> bỏ, không xử lý ảnh cũ

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def setup_logging(name):
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s [{name}] %(message)s")
    return logging.getLogger(name)


def claim(kind: str) -> dict | None:
    """Lấy job MỚI NHẤT đang chờ và bỏ các ảnh cũ hơn của cùng ca: khi AI chậm hơn camera, luôn phân tích
    khung hình hiện tại thay vì dồn hàng.

    Nhiều worker chạy song song không lấy trùng: UPDATE có điều kiện status='pending' là phép so-sánh-và-đổi
    nguyên tử (thay cho SELECT ... FOR UPDATE SKIP LOCKED của PostgreSQL, SQLite không có).
    """
    with session_scope() as db:
        job = (db.query(FrameJob).filter(FrameJob.kind == kind, FrameJob.status == "pending")
               .order_by(FrameJob.id.desc()).first())
        if job is None:
            return None
        won = (db.query(FrameJob).filter(FrameJob.id == job.id, FrameJob.status == "pending")
               .update({"status": "processing"}, synchronize_session=False))
        if not won:
            return None                              # worker khác vừa lấy
        (db.query(FrameJob).filter(FrameJob.kind == kind, FrameJob.status == "pending",
                                   FrameJob.exam_id == job.exam_id, FrameJob.id < job.id)
         .update({"status": "skipped", "error": "superseded", "finished_at": utcnow()}, synchronize_session=False))
        frame = db.get(Frame, job.frame_id)
        return {"id": job.id, "frame_id": job.frame_id, "exam_id": job.exam_id, "created_at": job.created_at,
                "file_path": frame.file_path if frame else None, "device_id": frame.device_id if frame else None}


def finish(job_id: int, status: str = "done", error: str | None = None):
    with session_scope() as db:
        db.query(FrameJob).filter(FrameJob.id == job_id).update(
            {"status": status, "error": error, "finished_at": utcnow()}, synchronize_session=False)


def is_stale(job) -> bool:
    return utcnow() - job["created_at"] > timedelta(seconds=STALE_AFTER_S)


def call_api(path: str, body: dict) -> dict:
    """Gọi API nội bộ của server biên bằng service token của worker."""
    r = requests.post(API + path, json=body, headers={"X-Service-Token": settings.service_token}, timeout=5)
    r.raise_for_status()
    return r.json()


def notify(log, path: str, body: dict):
    try:
        call_api(path, body)
    except requests.RequestException as e:         # kết quả cục bộ đã lưu; chỉ mất thông báo tức thời
        log.warning("Không báo được server biên %s: %s", path, e)


def run_forever(kind: str, handle, log, idle_sleep: float = 0.3):
    log.info("Đang chờ job '%s' ...", kind)
    while True:
        job = claim(kind)
        if job is None:
            time.sleep(idle_sleep)
            continue
        if is_stale(job) or not job["file_path"]:
            finish(job["id"], "skipped", "stale")
            continue
        try:
            handle(job)
            finish(job["id"])
        except Exception as e:
            log.exception("Job %s lỗi", job["id"])
            finish(job["id"], "failed", str(e)[:500])
