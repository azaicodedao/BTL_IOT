"""
Bộ mô phỏng thiết bị - chạy đúng quy trình của firmware thật để thử hệ thống khi chưa có phần cứng
(và để kiểm thử tải: mở nhiều tiến trình với --name khác nhau).

  python tests/simulators/sim_device.py camera   --name sim-cam-01  [--image anh.jpg | --webcam 0]
  python tests/simulators/sim_device.py actuator --name sim-lcd-01

Quy trình: tự báo danh với server biên -> in mã ghép -> chờ quản trị viên Đăng ký + Gán phòng trên bảng điều khiển biên
-> tự nhận khoá MQTT -> hoạt động:
  camera  : telemetry nhiệt độ/độ ẩm giả lập mỗi 30s, heartbeat 15s, gửi ảnh khi camera_mode khác idle (nếu có nguồn ảnh)
  actuator: nhận lệnh còi/hiển thị và trả ACK, in nội dung LCD
Khoá bị thu hồi / đổi phòng -> tự xin khoá mới. --reset để xoá trạng thái (như factory reset).
"""

import argparse
import json
import logging
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import paho.mqtt.client as mqtt  # noqa: E402
import requests  # noqa: E402

from backend.edge.app.workers.virtual_device import VirtualDevice  # noqa: E402

FW = "sim-1.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("sim")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Sim:

    def __init__(self, kind: str, cred: dict, args):
        self.kind, self.cred, self.args = kind, cred, args
        self.topics, self.device_id = cred["topics"], cred["device_id"]
        self.mode, self.seq, self.auth_fails = "idle", 0, 0
        self.telemetry_s, self.heartbeat_s, self.frame_s = 30.0, 15.0, 1.0
        self.started = time.time()
        m = cred["mqtt"]
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=m["client_id"], clean_session=False)
        self.mqtt.username_pw_set(m["username"], m["password"])
        self.mqtt.will_set(self.topics["status"], json.dumps({"state": "offline"}), qos=1, retain=True)
        self.mqtt.reconnect_delay_set(1, 30)
        self.mqtt.on_connect, self.mqtt.on_message = self._on_connect, self._on_message
        self.host = args.mqtt_host or m["host"]
        self.port = int(m["port"])

    def _pub(self, name, payload, qos=1, retain=False):
        self.mqtt.publish(self.topics[name], json.dumps(payload), qos=qos, retain=retain)

    def _on_connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            self.auth_fails += 1
            log.warning("MQTT từ chối (%s) lần %d", reason, self.auth_fails)
            return
        self.auth_fails = 0
        for name in (("config",) if self.kind == "camera" else ("cmd", "display", "config")):
            client.subscribe(self.topics[name], qos=1)
        self._pub("status", {"state": "online", "fw": FW, "hw": f"simulator {self.kind}", "ts": iso_now()}, retain=True)
        log.info("[%s] MQTT đã kết nối, phòng %s", self.device_id, self.cred["room_code"])

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload or b"{}")
        except json.JSONDecodeError:
            return
        if msg.topic == self.topics["config"]:
            self.mode = data.get("camera_mode", self.mode)
            self.telemetry_s = float(data.get("telemetry_interval_s", self.telemetry_s))
            self.heartbeat_s = float(data.get("heartbeat_interval_s", self.heartbeat_s))
            self.frame_s = max(0.2, float(data.get("frame_interval_s", self.frame_s)))
            log.info("[%s] config: %s", self.device_id, data)
        elif msg.topic == self.topics["display"]:
            log.info("[%s] LCD: %s %.1f°C %.0f%% %s", self.device_id, data.get("room"), data.get("temp_c", 0),
                     data.get("hum_pct", 0), data.get("advice") or "")
        elif msg.topic == self.topics["cmd"] and data.get("cmd_id"):
            what = data.get("pattern") or data.get("text")
            log.info("[%s] LỆNH %s %s -> ACK", self.device_id, data.get("action"), what)
            self._pub("ack", {"cmd_id": data["cmd_id"], "result": "ok", "ts": iso_now()})

    def _frame(self):
        import cv2
        if self.args.image:
            return Path(self.args.image).read_bytes()
        if self._cap is None:
            self._cap = cv2.VideoCapture(int(self.args.webcam))
        ok, img = self._cap.read()
        if not ok:
            return None
        ok, buf = cv2.imencode(".jpg", cv2.resize(img, (640, 480)), [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def run(self):
        self._cap = None
        self.mqtt.connect_async(self.host, self.port, keepalive=30)
        self.mqtt.loop_start()
        last_t = last_h = last_f = 0.0
        has_source = self.kind == "camera" and (self.args.image or self.args.webcam is not None)
        try:
            while self.auth_fails < 3:
                now = time.time()
                if self.mqtt.is_connected():
                    if now - last_h >= self.heartbeat_s:
                        last_h = now
                        self._pub("heartbeat", {"device_id": self.device_id, "ts": iso_now(), "fw": FW,
                                                "rssi": -55 + random.randint(-5, 5), "uptime_s": int(now - self.started),
                                                "camera_mode": self.mode}, qos=0)
                    if self.kind == "camera" and now - last_t >= self.telemetry_s:
                        last_t = now
                        self.seq += 1
                        temp = self.args.temp + 1.5 * math.sin(now / 120) + random.uniform(-0.2, 0.2)
                        self._pub("telemetry", {"msg_id": f"sim-{int(self.started)}-{self.seq:06d}",
                                                "device_id": self.device_id, "ts": iso_now(),
                                                "temperature_c": round(temp, 1),
                                                "humidity_pct": round(60 + 5 * math.cos(now / 200), 1), "fw": FW})
                if has_source and self.mode != "idle" and now - last_f >= self.frame_s:
                    last_f = now
                    data = self._frame()
                    if data:
                        try:
                            r = requests.post(self.cred["http"]["frames_url"], data=data, timeout=3, headers={
                                "Authorization": f"Bearer {self.cred['http']['token']}", "Content-Type": "image/jpeg"})
                            if r.status_code in (401, 403):
                                self.auth_fails += 1
                        except requests.RequestException as e:
                            log.warning("Gửi ảnh lỗi: %s", e)
                time.sleep(0.05)
        finally:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Mô phỏng thiết bị IoT tự đăng ký với server biên")
    ap.add_argument("kind", choices=["camera", "actuator"])
    ap.add_argument("--name", default=None, help="Tên thiết bị giả lập (mặc định sim-cam / sim-lcd)")
    ap.add_argument("--edge", default="http://127.0.0.1:8000", help="URL server biên")
    ap.add_argument("--mqtt-host", default=None, help="Mặc định: địa chỉ broker do server biên cấp")
    ap.add_argument("--image", default=None, help="camera: gửi lặp lại 1 ảnh JPEG (<= 100 KB)")
    ap.add_argument("--webcam", default=None, help="camera: chỉ số webcam (vd 0)")
    ap.add_argument("--temp", type=float, default=27.0, help="camera: nhiệt độ trung bình giả lập (°C)")
    ap.add_argument("--reset", action="store_true", help="Xoá trạng thái đã lưu (factory reset) rồi thoát")
    args = ap.parse_args()

    name = args.name or ("sim-cam" if args.kind == "camera" else "sim-lcd")
    device = VirtualDevice(name, "sim_camera" if args.kind == "camera" else "sim_actuator", FW, args.edge)
    if args.reset:
        device.factory_reset()
        print(f"Đã xoá trạng thái của {name}")
        return
    while True:
        cred = device.ensure_credentials()
        Sim(args.kind, cred, args).run()
        log.warning("[%s] khoá không còn hợp lệ (thu hồi / đổi phòng) - xin khoá mới", name)
        device.invalidate()


if __name__ == "__main__":
    main()
