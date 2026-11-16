"""
Thiết bị ảo (camera điện thoại, bộ mô phỏng) đi qua ĐÚNG quy trình tự đăng ký như firmware thật:
announce -> chờ quản trị viên duyệt + gán phòng trên bảng điều khiển biên -> tự nhận khoá MQTT/token.

Trạng thái (claim token + khoá đã nhận) lưu ở backend/edge/data/virtual/<tên>.json - tương đương NVS/flash của ESP.
Không ai phải chép khoá bằng tay.
"""

import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from backend.edge.app.core.config import settings

log = logging.getLogger("virtual_device")


class VirtualDevice:

    def __init__(self, name: str, device_type: str, firmware: str, edge_url: str | None = None,
                 hardware_uid: str | None = None, state_dir: Path | None = None):
        self.name = re.sub(r"[^A-Za-z0-9_-]", "-", name)[:30]
        self.device_type, self.firmware = device_type, firmware
        self.edge_url = (edge_url or f"http://127.0.0.1:{settings.http_port}").rstrip("/")
        self.hardware_uid = hardware_uid or f"virt:{self.name}"
        self.path = (state_dir or settings.data_dir / "virtual") / f"{self.name}.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    # ---------------------------------------------------------------- lưu trữ
    @property
    def credentials(self) -> dict | None:
        return self.state.get("credentials")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def invalidate(self):
        """Khoá không còn dùng được (bị thu hồi / đổi phòng): bỏ khoá, GIỮ claim token để xin lại."""
        if self.state.pop("credentials", None):
            self._save()

    def factory_reset(self):
        self.state = {}
        self.path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- đăng ký
    def _auth(self) -> dict:
        token = self.state.get("claim_token")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _announce(self) -> dict:
        r = requests.post(f"{self.edge_url}/api/v1/device-enrollments/announce", headers=self._auth(), timeout=5,
                          json={"hardware_uid": self.hardware_uid, "factory_name": self.name,
                                "device_type": self.device_type, "firmware_version": self.firmware,
                                "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": secrets.token_hex(8)})
        r.raise_for_status()
        out = r.json()
        if out.get("claim_token"):
            self.state["claim_token"] = out["claim_token"]
            self._save()
        return out

    def _poll(self) -> dict:
        r = requests.get(f"{self.edge_url}/api/v1/device-enrollments/{self.hardware_uid}", headers=self._auth(),
                         timeout=10)
        if r.status_code in (401, 404):
            return self._announce()
        r.raise_for_status()
        return r.json()

    def step(self) -> dict:
        """1 lượt hỏi. Trả phản hồi của server biên; nếu đã provisioned thì khoá được lưu vào self.credentials."""
        out = self._poll() if self.state.get("claim_token") else self._announce()
        if out.get("status") == "provisioned":
            self.state["credentials"] = out
            self._save()
        return out

    def ensure_credentials(self, should_stop=lambda: False) -> dict | None:
        """Chặn tới khi nhận được khoá (hoặc should_stop() trả True). In hướng dẫn cho người vận hành."""
        last = None
        while not self.credentials and not should_stop():
            try:
                out = self.step()
            except requests.RequestException as e:
                out = {"status": f"chưa tới được server biên {self.edge_url} ({e.__class__.__name__})",
                       "poll_interval_s": 5}
            status = out.get("status")
            if status != last:
                last = status
                hint = {"pending": f"chờ duyệt trên bảng điều khiển biên - mã ghép {out.get('pair_code')}",
                        "registered": "đã đăng ký, chờ gán phòng", "suspended": "đang bị tạm khoá",
                        "rejected": "bị từ chối / đã thu hồi", "provisioned": "đã nhận khoá"}.get(status, status)
                log.info("[%s] %s", self.name, hint)
            if not self.credentials:
                time.sleep(min(float(out.get("poll_interval_s", 5)), 30))
        return self.credentials
