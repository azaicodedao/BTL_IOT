"""
Kênh điều khiển trung tâm -> server biên. Biên chủ động mở WebSocket (/api/v1/edge/link) nên chạy được sau NAT;
trung tâm gọi xuống theo kiểu hỏi-đáp: {"id", "method", "params"} -> {"id", "ok", "result" | "error", "status"}.

Các lệnh: sync (đồng bộ ngay), command, room_buzzer, commands, config, face_samples, face_delete
(xem backend/edge/app/services/ops.py).
Ngoài ra còn ký "vé" để trình duyệt lấy ảnh trực tiếp từ biên (ảnh không đi qua trung tâm).
"""

import asyncio
import json
import logging
import uuid

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from backend.central.app.core.config import settings
from backend.shared.security import make_ticket

log = logging.getLogger("edge_link")

_links: dict[str, WebSocket] = {}
_pending: dict[str, asyncio.Future] = {}
_loop: asyncio.AbstractEventLoop | None = None
on_change = None                 # fn(edge_id, online) do main đăng ký để báo dashboard


def bind_loop(loop):
    global _loop
    _loop = loop


def online(edge_id: str | None) -> bool:
    return edge_id in _links


async def serve(ws: WebSocket, edge_id: str):
    """Giữ kết nối của 1 server biên tới khi đứt. Kết nối mới của cùng biên thay kết nối cũ."""
    await ws.accept()
    old = _links.get(edge_id)
    _links[edge_id] = ws
    if old is not None:
        await old.close(code=4000)
    log.info("Server biên %s đã nối kênh điều khiển", edge_id)
    if on_change:
        on_change(edge_id, True)
    try:
        while True:
            reply = json.loads(await ws.receive_text())
            fut = _pending.pop(reply.get("id"), None)
            if fut and not fut.done():
                fut.set_result(reply)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if _links.get(edge_id) is ws:
            del _links[edge_id]
            log.warning("Server biên %s mất kênh điều khiển", edge_id)
            if on_change:
                on_change(edge_id, False)


async def call_async(edge_id: str | None, method: str, params: dict | None = None, timeout: float | None = None):
    ws = _links.get(edge_id)
    if ws is None:
        raise HTTPException(503, f"Server biên {edge_id or '(chưa xác định)'} chưa kết nối với trung tâm")
    msg_id = uuid.uuid4().hex
    fut = _pending[msg_id] = asyncio.get_running_loop().create_future()
    try:
        await ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}, ensure_ascii=False))
        reply = await asyncio.wait_for(fut, timeout or settings.edge_rpc_timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Server biên {edge_id} không phản hồi lệnh {method}")
    except (RuntimeError, WebSocketDisconnect):
        raise HTTPException(503, f"Server biên {edge_id} vừa mất kết nối")
    finally:
        _pending.pop(msg_id, None)
    if not reply.get("ok"):
        raise HTTPException(int(reply.get("status") or 502), reply.get("error") or "Server biên báo lỗi")
    return reply.get("result")


def call(edge_id: str | None, method: str, params: dict | None = None, timeout: float | None = None):
    """Gọi từ endpoint đồng bộ / luồng nền (KHÔNG gọi trong event loop). Raises HTTPException 503/504/lỗi của biên."""
    if _loop is None:
        raise HTTPException(503, "Kênh điều khiển chưa sẵn sàng")
    return asyncio.run_coroutine_threadsafe(call_async(edge_id, method, params, timeout), _loop).result()


def poke(edge_ids):
    """Nhắc các server biên đồng bộ ngay (không chờ, không lỗi nếu biên đang ngoại tuyến: biên tự kéo định kỳ)."""
    if _loop is None:
        return
    for edge_id in set(edge_ids):
        if edge_id in _links:
            fut = asyncio.run_coroutine_threadsafe(call_async(edge_id, "sync"), _loop)
            fut.add_done_callback(lambda f: f.exception())          # nuốt lỗi: chỉ là lời nhắc


def ticket_url(edge, path: str, scope: str, user_id: int) -> str:
    """URL có vé để trình duyệt truy cập thẳng server biên. Raises 503 nếu chưa biết địa chỉ biên."""
    token = settings.edge_token_map().get(edge.edge_id if edge else "")
    if edge is None or not edge.public_url or not token:
        raise HTTPException(503, "Chưa biết địa chỉ server biên giữ dữ liệu này")
    ticket = make_ticket(token, scope, str(user_id), settings.ticket_ttl_s)
    return f"{edge.public_url.rstrip('/')}/edge/v1{path}?ticket={ticket}"
