"""Persistent video sources managed by the edge console; enroll through the normal device API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from backend.edge.app.core.config import ROOT, settings


def validate_url(value: str) -> str:
    value = value.strip()
    try:
        url = urlsplit(value)
        valid = url.scheme in {"http", "https", "rtsp"} and bool(url.hostname) and url.port != 0
        if url.username is not None or url.password is not None:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Nhập URL video HTTP/HTTPS/RTSP hợp lệ, không kèm tài khoản hoặc mật khẩu")
    return value


class CameraSources:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.data_dir / "camera_sources.json"
        self.lock = threading.RLock()
        self.processes = {}
        self.sources = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.sources, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def _launch(self, key):
        p = self.processes.get(key)
        if p is not None and p.poll() is None:
            return
        source = self.sources[key]
        log_dir = settings.data_dir / "camera_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        with (log_dir / f"{key}.log").open("ab") as output:
            self.processes[key] = subprocess.Popen(
                [sys.executable, "-u", "-m", "backend.edge.app.workers.phone_bridge", "--name", key,
                 "--url", source["url"], "--edge", f"http://127.0.0.1:{settings.http_port}"],
                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def _stop(self, key):
        p = self.processes.pop(key, None)
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)

    def list(self):
        with self.lock:
            return [{"id": key, **source, "hardware_uid": f"virt:{key}",
                     "running": key in self.processes and self.processes[key].poll() is None}
                    for key, source in self.sources.items()]

    def create(self, name, url):
        url = validate_url(url)
        with self.lock:
            if any(s["url"] == url for s in self.sources.values()):
                raise HTTPException(409, "Nguồn video này đã được thêm")
            key = "phone-" + uuid.uuid4().hex[:12]
            self.sources[key] = {"name": name, "url": url, "enabled": True}
            try:
                self._launch(key)
                self._save()
            except Exception:
                self._stop(key)
                self.sources.pop(key, None)
                raise
            return next(s for s in self.list() if s["id"] == key)

    def update(self, key, name, url, enabled):
        url = validate_url(url)
        with self.lock:
            if key not in self.sources:
                raise HTTPException(404, "Không có nguồn camera")
            if any(k != key and s["url"] == url for k, s in self.sources.items()):
                raise HTTPException(409, "Nguồn video này đã được thêm")
            self._stop(key)
            self.sources[key] = {"name": name, "url": url, "enabled": enabled}
            self._save()
            if enabled:
                self._launch(key)
            return next(s for s in self.list() if s["id"] == key)

    def remove(self, key):
        with self.lock:
            if key not in self.sources:
                raise HTTPException(404, "Không có nguồn camera")
            self._stop(key)
            del self.sources[key]
            self._save()

    def start(self):
        with self.lock:
            for key, source in self.sources.items():
                if source["enabled"]:
                    self._launch(key)

    def stop(self):
        with self.lock:
            for key in list(self.processes):
                self._stop(key)


manager = CameraSources()
