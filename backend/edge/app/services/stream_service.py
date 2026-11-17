"""
Camera realtime cho giao diện.

ESP32-CAM POST ~5 khung/giây -> giữ khung mới nhất trong RAM -> phát lại cho trình duyệt dạng MJPEG
(multipart/x-mixed-replace, hiển thị bằng <img>). Nhiều người xem cùng lúc không tăng tải cho camera.

Có người xem mà phòng chưa có ca thi -> camera chuyển "preview" (chỉ phát hình, không lưu, không AI);
không còn ai xem PREVIEW_IDLE_S giây -> camera về "idle".
"""

import asyncio
import logging
from datetime import datetime

from starlette.concurrency import run_in_threadpool

log = logging.getLogger("stream")

PREVIEW_IDLE_S = 15
KEEPALIVE_S = 5             # camera không gửi khung mới -> gửi lại khung cũ để giữ kết nối trình duyệt

_latest: dict[str, tuple[datetime, bytes]] = {}
_events: dict[str, asyncio.Event] = {}
_viewers: dict[str, int] = {}


def latest(device_id: str):
    return _latest.get(device_id)


def viewers(device_id: str) -> int:
    return _viewers.get(device_id, 0)


def push(device_id: str, ts: datetime, data: bytes):
    """Gọi trong event loop mỗi khi camera gửi khung mới."""
    _latest[device_id] = (ts, data)
    ev = _events.pop(device_id, None)
    if ev:
        ev.set()


async def _wait_new(device_id: str, timeout: float) -> bool:
    ev = _events.setdefault(device_id, asyncio.Event())
    try:
        await asyncio.wait_for(ev.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


BOUNDARY = "frame"


async def mjpeg(device_id: str, on_join, on_leave):
    """Generator MJPEG cho StreamingResponse. on_join/on_leave: hàm đồng bộ (chạy trong threadpool)."""
    _viewers[device_id] = _viewers.get(device_id, 0) + 1
    await run_in_threadpool(on_join)
    try:
        last_sent = None
        while True:
            item = _latest.get(device_id)
            if item and item[0] != last_sent:
                last_sent = item[0]
                yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(item[1])}\r\n\r\n"
                       .encode() + item[1] + b"\r\n")
            if not await _wait_new(device_id, KEEPALIVE_S):
                last_sent = None                       # lâu không có khung mới: gửi lại khung cũ để giữ kết nối
    finally:
        _viewers[device_id] = max(0, _viewers.get(device_id, 1) - 1)
        if _viewers[device_id] == 0:
            asyncio.get_running_loop().call_later(
                PREVIEW_IDLE_S, lambda: _viewers.get(device_id, 0) == 0 and asyncio.ensure_future(
                    run_in_threadpool(on_leave)))
