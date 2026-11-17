"""
Liên kết server biên -> server trung tâm. Biên luôn là bên CHỦ ĐỘNG kết nối (chạy được sau NAT của phòng học):

  1. Outbox  : sự kiện (trạng thái thiết bị, telemetry, kết quả AI) ghi vào bảng outbox trong cùng giao dịch
               với thay đổi cục bộ, rồi gửi theo lô  POST {central}/api/v1/edge/events. Mất mạng -> giữ lại, gửi bù.
  2. Sync    : định kỳ GET {central}/api/v1/edge/sync lấy danh sách phòng + ca thi đang diễn ra (trạng thái mong muốn).
  3. Link    : WebSocket {central}/api/v1/edge/link để trung tâm gọi xuống (lệnh còi, cấu hình, "đồng bộ ngay").

Chỉ embedding + metadata đi lên trung tâm; ảnh không bao giờ rời server biên.
"""

import asyncio
import json
import logging
import threading
import uuid
from datetime import timedelta

import requests
from fastapi import HTTPException
from sqlalchemy import event, func
from starlette.concurrency import run_in_threadpool

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import SessionLocal, session_scope
from backend.edge.app.models import Outbox
from backend.shared.db import utcnow

log = logging.getLogger("uplink")

VERSION = "edge-1.0"
BATCH = 50

state = {"link": False, "last_sync": None, "last_flush": None, "last_error": None,
         "delivery_error": None, "retry_count": 0, "next_retry_at": None}
result_handlers: dict = {}        # loại sự kiện -> fn(data, kết quả trung tâm trả về)
rpc_handlers: dict = {}           # method -> fn(params) -> kết quả (hàm đồng bộ, chạy trong threadpool)
sync_handler = None               # fn(dữ liệu sync) do exam_state đăng ký

_wake = threading.Event()
_sync_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []


@event.listens_for(SessionLocal, "after_commit")
def _on_commit(_session):
    _wake.set()                   # có thể vừa có sự kiện mới trong outbox


def headers() -> dict:
    return {"Authorization": f"Bearer {settings.central_token}", "X-Edge-Id": settings.edge_id}


def url(path: str) -> str:
    return settings.central_url.rstrip("/") + "/api/v1/edge" + path


def emit(db, type_: str, data: dict, ttl_s: float | None = None):
    """Xếp 1 sự kiện vào outbox trong giao dịch hiện tại của `db` (được gửi sau khi commit).

    `data` phải tuần tự hoá được thành JSON. ttl_s: sự kiện chỉ có ý nghĩa tức thời, quá hạn thì bỏ.
    """
    db.add(Outbox(event_id=uuid.uuid4().hex, type=type_, data=data,
                  expires_at=utcnow() + timedelta(seconds=ttl_s) if ttl_s else None))


def backlog() -> int:
    with session_scope() as db:
        return db.query(Outbox).count()


def queue_status() -> dict:
    with session_scope() as db:
        count, oldest = db.query(func.count(Outbox.id), func.min(Outbox.ts)).one()
        return {"pending": count, "oldest_at": oldest.isoformat() if oldest else None,
                "oldest_age_s": max(0, int((utcnow() - oldest).total_seconds())) if oldest else 0,
                "storage": "SQLite", "persistent": True}


def request_flush():
    _wake.set()


# ---------------------------------------------------------------- outbox

def flush_once() -> bool:
    """Gửi 1 lô. Trả True nếu còn sự kiện chờ. Ném lỗi mạng cho vòng lặp xử lý backoff."""
    with session_scope() as db:
        db.query(Outbox).filter(Outbox.expires_at.isnot(None), Outbox.expires_at < utcnow()).delete()
        rows = db.query(Outbox).order_by(Outbox.id).limit(BATCH).all()
        batch = [{"id": r.event_id, "type": r.type, "ts": r.ts.isoformat(), "data": r.data} for r in rows]
    if not batch:
        state.update(delivery_error=None, retry_count=0, next_retry_at=None)
        return False
    r = requests.post(url("/events"), json={"events": batch}, headers=headers(), timeout=10)
    r.raise_for_status()
    response = r.json()
    results = response.get("results", {})
    acknowledged = response.get("acknowledged")
    if not isinstance(acknowledged, list) or not all(isinstance(e, str) for e in acknowledged) or not isinstance(results, dict):
        raise requests.RequestException("Trung tâm chưa xác nhận sự kiện; dữ liệu vẫn được giữ tại biên")
    accepted = set(acknowledged) & {e["id"] for e in batch}
    with session_scope() as db:
        if accepted:
            db.query(Outbox).filter(Outbox.event_id.in_(accepted)).delete(synchronize_session=False)
    if accepted:
        state["last_flush"] = utcnow().isoformat()
    for e in batch:
        handler = result_handlers.get(e["type"])
        if handler and e["id"] in accepted and e["id"] in results:
            try:
                handler(e["data"], results[e["id"]])
            except Exception:
                log.exception("Lỗi xử lý phản hồi sự kiện %s", e["type"])
    if len(accepted) != len(batch):
        raise requests.RequestException(f"Còn {len(batch) - len(accepted)} sự kiện chưa được trung tâm xác nhận; sẽ gửi lại")
    state.update(delivery_error=None, retry_count=0, next_retry_at=None)
    return len(batch) == BATCH


def _sender_loop():
    backoff = 1.0
    while not _stop.is_set():
        _wake.wait(2.0)
        _wake.clear()
        try:
            while flush_once() and not _stop.is_set():
                pass
            backoff = 1.0
        except Exception as e:                       # mất mạng / trung tâm lỗi: giữ outbox, thử lại
            state["last_error"] = f"events: {e}"
            state.update(delivery_error=str(e), retry_count=state["retry_count"] + 1,
                         next_retry_at=(utcnow() + timedelta(seconds=backoff)).isoformat())
            _stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
            _wake.set()


# ---------------------------------------------------------------- sync

def get(path: str, **params):
    r = requests.get(url(path), params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def post(path: str, body: dict, timeout: float = 10):
    """Gọi đồng bộ lên trung tâm (đăng ký vector khuôn mặt, nhận dạng ngoài danh sách). Ném requests.RequestException."""
    r = requests.post(url(path), json=body, headers=headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def sync_once():
    data = get("/sync", public_url=settings.public_url, name=settings.name, version=VERSION)
    if sync_handler:
        sync_handler(data)
    state["last_sync"] = utcnow().isoformat()
    state["last_error"] = None


def request_sync():
    _sync_wake.set()


def _sync_loop():
    while not _stop.is_set():
        try:
            sync_once()
        except Exception as e:
            state["last_error"] = f"sync: {e}"
            log.warning("Chưa đồng bộ được với trung tâm %s: %s", settings.central_url, e)
        _sync_wake.wait(settings.sync_interval_s)
        _sync_wake.clear()


# ---------------------------------------------------------------- link (trung tâm gọi xuống)

async def _handle_rpc(ws, msg: dict):
    fn = rpc_handlers.get(msg.get("method"))
    try:
        if fn is None:
            raise HTTPException(404, f"Biên không hỗ trợ lệnh {msg.get('method')}")
        reply = {"id": msg.get("id"), "ok": True, "result": await run_in_threadpool(fn, msg.get("params") or {})}
    except HTTPException as e:
        reply = {"id": msg.get("id"), "ok": False, "status": e.status_code, "error": str(e.detail)}
    except Exception as e:
        log.exception("Lỗi xử lý lệnh từ trung tâm: %s", msg.get("method"))
        reply = {"id": msg.get("id"), "ok": False, "status": 500, "error": str(e)}
    await ws.send(json.dumps(reply, default=str, ensure_ascii=False))


async def link_loop():
    import websockets

    ws_url = settings.central_url.rstrip("/").replace("http", "ws", 1) + "/api/v1/edge/link"
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(ws_url, additional_headers=headers(), ping_interval=20,
                                          max_size=4 * 1024 * 1024) as ws:
                state["link"] = True
                backoff = 1.0
                log.info("Đã nối kênh điều khiển với trung tâm %s", settings.central_url)
                request_sync()
                request_flush()
                tasks = set()
                async for raw in ws:
                    t = asyncio.create_task(_handle_rpc(ws, json.loads(raw)))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
        except asyncio.CancelledError:
            state["link"] = False
            raise
        except Exception as e:
            state["last_error"] = f"link: {e}"
        state["link"] = False
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


def start():
    _stop.clear()
    for fn in (_sender_loop, _sync_loop):
        t = threading.Thread(target=fn, daemon=True, name=fn.__name__)
        t.start()
        _threads.append(t)


def stop():
    _stop.set()
    _wake.set()
    _sync_wake.set()
