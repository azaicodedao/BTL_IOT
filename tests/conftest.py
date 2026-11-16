"""
Môi trường kiểm thử: server trung tâm + server biên chạy TRONG tiến trình pytest, không cần Docker.

- CSDL: SQLite tạm cho cả hai; Qdrant chạy nhúng trong RAM.
- MQTT: thay bridge bằng bản ghi nhận (publish / dynsec được ghi lại, bản tin thiết bị được bơm thẳng vào handler).
- Mạng biên -> trung tâm: `requests` của uplink được nối vào TestClient của trung tâm.
- Kênh trung tâm -> biên: edge_link.call gọi thẳng handler RPC của biên.
Các luồng nền của uplink bị tắt: test tự gọi flush / sync để kết quả xác định.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="smartroom-test-"))

os.environ.update({
    "EDGE_DATABASE_URL": f"sqlite:///{(TMP / 'edge.db').as_posix()}",
    "EDGE_DATA_DIR": str(TMP / "edge-data"),
    "EDGE_STORAGE_DIR": str(TMP / "edge-storage"),
    "EDGE_MDNS_ENABLED": "false",
    "EDGE_PUBLIC_HOST": "192.168.50.10",
    "EDGE_CENTRAL_TOKEN": "test-edge-key",
    "EDGE_ADMIN_PASSWORD": "console-pw",
    "EDGE_COMMAND_ACK_TIMEOUT_S": "0.3",
    "CENTRAL_DATABASE_URL": f"sqlite:///{(TMP / 'central.db').as_posix()}",
    "CENTRAL_QDRANT_URL": ":memory:",
    "CENTRAL_EDGE_TOKENS": "edge-01:test-edge-key",
})


class FakeBroker:
    """Thay cho Mosquitto: ghi lại mọi thứ server biên gửi đi."""

    def __init__(self, bridge):
        self.bridge, self.published, self.dynsec_calls = bridge, [], []
        bridge.start = bridge.stop = lambda: None
        bridge.publish = self.publish
        bridge.dynsec = self.dynsec
        bridge.connected.set()

    def publish(self, topic, payload, qos=1, retain=False):
        self.published.append((topic, payload, retain))
        return True

    def dynsec(self, *commands, timeout=5.0):
        self.dynsec_calls.extend(commands)
        return []

    def last(self, suffix):
        """Bản tin mới nhất gửi tới topic kết thúc bằng suffix."""
        return next((p for t, p, _ in reversed(self.published) if t.endswith(suffix)), None)

    def device_says(self, room, device_id, kind, payload):
        """Thiết bị publish lên classroom/{room}/{device}/{kind}."""
        self.bridge.handlers[kind](room, device_id, payload)


class _Response:
    def __init__(self, r):
        self._r, self.status_code = r, r.status_code

    def json(self):
        return self._r.json()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class CentralOverTestClient:
    """`requests` tối giản cho uplink: chuyển lời gọi HTTP của biên vào TestClient của trung tâm."""

    RequestException, HTTPError = requests.RequestException, requests.HTTPError

    def __init__(self, client):
        self.client, self.down = client, False

    def _path(self, url):
        if self.down:
            raise requests.ConnectionError("trung tâm ngoại tuyến (giả lập)")
        return "/" + url.split("://", 1)[1].split("/", 1)[1]

    def post(self, url, json=None, headers=None, timeout=None):
        return _Response(self.client.post(self._path(url), json=json, headers=headers))

    def get(self, url, params=None, headers=None, timeout=None):
        return _Response(self.client.get(self._path(url), params=params, headers=headers))


@pytest.fixture(scope="session")
def system():
    """Dựng cả hệ thống. Trả namespace: central, edge (TestClient), broker, net, uplink, admin (header), console (header)."""
    from types import SimpleNamespace

    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from backend.central.app.main import app as central_app
    from backend.central.app.services import edge_link
    from backend.edge.app.main import app as edge_app
    from backend.edge.app.services import uplink
    from backend.edge.app.services.mqtt_bridge import bridge

    broker = FakeBroker(bridge)

    async def no_link():
        import asyncio
        await asyncio.Event().wait()

    uplink.start = uplink.stop = lambda: None
    uplink.link_loop = no_link

    with TestClient(central_app) as central, TestClient(edge_app) as edge:
        net = CentralOverTestClient(central)
        uplink.requests = net

        def call(edge_id, method, params=None, timeout=None):
            if edge_id != "edge-01":
                raise HTTPException(503, f"Server biên {edge_id} chưa kết nối với trung tâm")
            return uplink.rpc_handlers[method](params or {})

        def poke(edge_ids):
            if "edge-01" in set(edge_ids):
                uplink.sync_once()

        edge_link.call, edge_link.poke = call, poke
        edge_link.online = lambda edge_id: edge_id == "edge-01"

        token = central.post("/api/v1/auth/login", json={"email": "admin@classroom.local",
                                                         "password": "Admin@123"}).json()["access_token"]
        console = edge.post("/edge/v1/auth/login", json={"password": "console-pw"}).json()["token"]
        yield SimpleNamespace(central=central, edge=edge, broker=broker, net=net, uplink=uplink,
                              admin={"Authorization": f"Bearer {token}"},
                              console={"Authorization": f"Bearer {console}"})
