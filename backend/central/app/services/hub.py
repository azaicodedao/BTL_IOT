"""
WebSocket hub. Mỗi tin: {"type": "...", "room_id": int|None, "data": {...}}
  telemetry, device.status, alert.created, alert.updated, command.updated,
  attendance.recorded, attendance.updated, exam.suspicion, exam.event_reviewed,
  exam.alarm_on, exam.alarm_off, session.updated

Client chỉ nhận tin của phòng mình được gán (admin nhận tất cả), có thể lọc thêm theo phiên
(/ws/attendance/{session_id}, /ws/exam/{session_id}).
Gửi được từ luồng khác (MQTT, tác vụ nền) qua publish_threadsafe.
"""

import asyncio
import json
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(eq=False)
class Client:
    ws: WebSocket
    rooms: set[int] | None              # None = mọi phòng
    session_kind: str | None = None     # attendance | exam
    session_id: int | None = None


class Hub:

    def __init__(self):
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop):
        self.loop = loop

    async def add(self, client: Client):
        self.clients.add(client)

    def remove(self, client: Client):
        self.clients.discard(client)

    def _wants(self, c: Client, msg: dict) -> bool:
        room = msg.get("room_id")
        if c.rooms is not None and room is not None and room not in c.rooms:
            return False
        if c.session_kind:
            return msg.get("session_kind") == c.session_kind and msg.get("session_id") == c.session_id
        return True

    async def publish(self, type_: str, data, room_id: int | None = None,
                      session_kind: str | None = None, session_id: int | None = None):
        msg = {"type": type_, "room_id": room_id, "session_kind": session_kind,
               "session_id": session_id, "data": data}
        text = json.dumps(msg, ensure_ascii=False,
                          default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o))
        targets = [c for c in list(self.clients) if self._wants(c, msg)]
        results = await asyncio.gather(*(c.ws.send_text(text) for c in targets), return_exceptions=True)
        for c, r in zip(targets, results):
            if isinstance(r, Exception):
                self.remove(c)

    def publish_threadsafe(self, *args, **kwargs):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.publish(*args, **kwargs), self.loop)


hub = Hub()
