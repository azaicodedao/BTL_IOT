"""
phone_bridge - Dùng điện thoại Android (app "IP Webcam") hoặc webcam làm camera của phòng thi.

Bridge chạy trên server biên và đóng vai 1 thiết bị IoT đầy đủ:
  Đăng ký: tự báo danh với server biên như firmware thật (xem virtual_device.py). Lần đầu chạy, mở bảng điều khiển
           biên -> "Thiết bị chờ đăng ký" -> Đăng ký -> Gán phòng. Bridge tự nhận khoá, không chép tay gì cả.
  Video  : kéo luồng http://<ip>:8080/video (MJPEG) hoặc rtsp://... -> resize -> JPEG
           -> POST tới server biên (token riêng). Ảnh không rời server biên.
  MQTT   : status online/offline (retain + Last Will), heartbeat 15s, event sensor_error/sensor_ok khi mất / có lại
           video, nhận config (retain) -> chỉ gửi hình khi camera_mode khác "idle".
  Khoá bị thu hồi / đổi phòng -> MQTT từ chối -> bridge tự xin khoá mới bằng claim token.

Chạy (ở thư mục gốc):
  python -m backend.edge.app.workers.phone_bridge --name phone-cam-01 --url http://192.168.1.30:8080/video
  (thử bằng webcam laptop: --url 0)
"""

import argparse
import json
import os
import threading
import time

import cv2
import paho.mqtt.client as mqtt
import requests

from backend.edge.app.workers.common import setup_logging
from backend.edge.app.workers.virtual_device import VirtualDevice

FW = "phone-bridge-2.0"
AUTH_FAILS_BEFORE_REENROLL = 3
log = setup_logging("phone_bridge")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


class Source:
    """Đọc luồng video liên tục ở luồng riêng, chỉ giữ khung mới nhất; mất tín hiệu thì tự nối lại."""

    def __init__(self, url):
        self.url = int(url) if str(url).isdigit() else url
        self.frame, self.frame_ts, self.ok = None, 0.0, False
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            cap = cv2.VideoCapture(self.url) if isinstance(self.url, int) else cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                self.ok = False
                log.warning("Không mở được %s, thử lại sau 3s", self.url)
                time.sleep(3)
                continue
            log.info("Đã kết nối video %s", self.url)
            while True:
                ok, frame = cap.read()
                if not ok:
                    self.ok = False
                    log.warning("Mất tín hiệu video, kết nối lại")
                    break
                with self._lock:
                    self.frame, self.frame_ts, self.ok = frame, time.time(), True
            cap.release()
            time.sleep(1)

    def latest(self):
        with self._lock:
            return self.frame, self.frame_ts


class Session:
    """1 phiên làm việc với 1 bộ khoá. Kết thúc (run trả về) khi khoá không còn hợp lệ."""

    def __init__(self, cred: dict, source: Source, fps: float, width: int, mqtt_host: str | None):
        self.cred, self.source, self.fps, self.width = cred, source, fps, width
        self.base = cred["topics"]["base"]
        self.mode = "idle"
        self.started = time.time()
        self.sent = self.failed = self.auth_fails = 0
        self.http = requests.Session()                    # keep-alive
        self.http.headers.update({"Authorization": f"Bearer {cred['http']['token']}", "Content-Type": "image/jpeg"})

        m = cred["mqtt"]
        self.mqtt_host = mqtt_host or m["host"]
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=m["client_id"], clean_session=False)
        self.mqtt.username_pw_set(m["username"], m["password"])
        self.mqtt.will_set(f"{self.base}/status", json.dumps({"state": "offline"}), qos=1, retain=True)
        self.mqtt.reconnect_delay_set(1, 30)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            self.auth_fails += 1
            log.warning("MQTT từ chối (%s) lần %d", reason, self.auth_fails)
            return
        self.auth_fails = 0
        client.subscribe(f"{self.base}/config", qos=1)
        client.publish(f"{self.base}/status", json.dumps({"state": "online", "fw": FW, "hw": "Android IP Webcam"}),
                       qos=1, retain=True)
        log.info("MQTT đã kết nối với tư cách %s", self.cred["device_id"])

    def _on_message(self, client, userdata, msg):
        try:
            cfg = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        if cfg.get("frame_interval_s"):
            self.fps = max(1.0, min(15.0, 1.0 / float(cfg["frame_interval_s"])))
        if cfg.get("camera_mode") and cfg["camera_mode"] != self.mode:
            self.mode = cfg["camera_mode"]
            log.info("Chế độ camera: %s", self.mode)

    def _event(self, type_, **kw):
        self.mqtt.publish(f"{self.base}/event", json.dumps({"msg_id": f"pb-{int(time.time() * 1000)}",
                                                           "device_id": self.cred["device_id"], "type": type_, **kw}),
                          qos=1)

    def _encode(self, frame):
        h, w = frame.shape[:2]
        if w > self.width:
            frame = cv2.resize(frame, (self.width, int(h * self.width / w)), interpolation=cv2.INTER_AREA)
        for q in (80, 65, 50):                            # giữ dưới giới hạn 100 KB của server biên
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])
            if ok and len(buf) < 95 * 1024:
                return buf.tobytes(), frame.shape
        return None, frame.shape

    def run(self):
        self.mqtt.connect_async(self.mqtt_host, int(self.cred["mqtt"]["port"]), keepalive=30)
        self.mqtt.loop_start()
        last_hb = last_sent_ts = last_err = 0.0
        stream_errors, shape = 0, None
        try:
            while self.auth_fails < AUTH_FAILS_BEFORE_REENROLL:
                now = time.time()
                frame, ts = self.source.latest()

                # mất video lâu -> báo lỗi "cảm biến" để server biên chuyển thiết bị sang Fault
                if frame is None or now - ts > 5:
                    if now - last_err >= 10 and self.mqtt.is_connected():
                        last_err = now
                        stream_errors += 1
                        self._event("sensor_error", detail="video stream", count=stream_errors)
                elif stream_errors:
                    stream_errors = 0
                    self._event("sensor_ok", detail="video stream")

                if self.mode != "idle" and frame is not None and ts != last_sent_ts and now - ts < 2:
                    data, shape = self._encode(frame)
                    if data:
                        try:
                            r = self.http.post(self.cred["http"]["frames_url"], data=data, timeout=3)
                            if r.status_code in (200, 202):
                                self.sent += 1
                            else:
                                self.failed += 1
                                if r.status_code in (401, 403):
                                    self.auth_fails += 1
                        except requests.RequestException as e:
                            self.failed += 1
                            log.warning("Gửi ảnh lỗi: %s", e)
                    last_sent_ts = ts

                if now - last_hb >= 15 and self.mqtt.is_connected():
                    last_hb = now
                    self.mqtt.publish(f"{self.base}/heartbeat", json.dumps({
                        "device_id": self.cred["device_id"], "uptime_s": int(now - self.started), "fw": FW,
                        "camera_mode": self.mode, "frames_sent": self.sent, "frames_failed": self.failed,
                        "source_ok": self.source.ok, "resolution": f"{shape[1]}x{shape[0]}" if shape else None,
                        "fps_target": round(self.fps, 1)}), qos=0)

                time.sleep(max(0.005, 1.0 / self.fps - (time.time() - now)))
        finally:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Camera điện thoại (IP Webcam) -> thiết bị IoT tự đăng ký với server biên")
    ap.add_argument("--name", default="phone-cam-01", help="Tên gợi nhớ, hiện ở danh sách thiết bị chờ đăng ký")
    ap.add_argument("--url", required=True, help="http://<ip-điện-thoại>:8080/video | rtsp://... | 0 (webcam máy)")
    ap.add_argument("--fps", type=float, default=10)
    ap.add_argument("--width", type=int, default=960, help="Thu nhỏ khung hình về chiều rộng này (px)")
    ap.add_argument("--edge", default=None, help="URL server biên (mặc định http://127.0.0.1:<cổng>)")
    ap.add_argument("--mqtt-host", default=None, help="Mặc định: địa chỉ broker do server biên cấp")
    args = ap.parse_args()

    device = VirtualDevice(args.name, "virtual_camera", FW, args.edge)
    source = Source(args.url)
    while True:
        cred = device.ensure_credentials()
        Session(cred, source, args.fps, args.width, args.mqtt_host).run()
        log.warning("Khoá không còn hợp lệ (bị thu hồi / đổi phòng) - xin khoá mới")
        device.invalidate()


if __name__ == "__main__":
    main()
