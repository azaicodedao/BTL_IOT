"""
Kết nối backend <-> Mosquitto (mục 8.1).

Nhận:   classroom/{room}/{device}/telemetry | heartbeat | status | event | cmd/ack
Gửi:    classroom/{room}/{device}/cmd | config (retain) | display (retain)
Quản lý tài khoản thiết bị qua Dynamic Security: $CONTROL/dynamic-security/v1
"""

import json
import logging
import threading
import uuid

import paho.mqtt.client as mqtt

from backend.edge.app.core.config import settings

log = logging.getLogger("mqtt")

DEVICE_TOPICS = [("classroom/+/+/telemetry", 1), ("classroom/+/+/heartbeat", 0),
                 ("classroom/+/+/status", 1), ("classroom/+/+/event", 1), ("classroom/+/+/cmd/ack", 1)]
DYNSEC_TOPIC = "$CONTROL/dynamic-security/v1"


def topic(room_code: str, device_id: str, kind: str) -> str:
    return f"classroom/{room_code}/{device_id}/{kind}"


class MqttBridge:

    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"edge-{uuid.uuid4().hex[:6]}", clean_session=True)
        self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.reconnect_delay_set(1, 30)                  # backoff 1s -> 30s (mục 8.3)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.handlers = {}                                     # kind -> fn(room_code, device_id, payload: dict)
        self.connected = threading.Event()
        self._pending: dict[str, tuple[threading.Event, list]] = {}

    # ------------------------------------------------------------ vòng đời
    def start(self):
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            log.error("MQTT từ chối kết nối: %s", reason)
            return
        client.subscribe(DEVICE_TOPICS + [(DYNSEC_TOPIC + "/response", 1)])
        self.connected.set()
        log.info("Đã kết nối MQTT %s:%s", settings.mqtt_host, settings.mqtt_port)

    def _on_disconnect(self, client, userdata, flags, reason, props):
        self.connected.clear()
        log.warning("Mất kết nối MQTT (%s), đang thử lại", reason)

    # ------------------------------------------------------------ nhận
    def _on_message(self, client, userdata, msg):
        if msg.topic == DYNSEC_TOPIC + "/response":
            return self._on_dynsec_response(msg.payload)

        parts = msg.topic.split("/")
        if len(parts) < 4 or parts[0] != "classroom":
            return
        room_code, device_id, kind = parts[1], parts[2], "/".join(parts[3:])
        handler = self.handlers.get(kind)
        if handler is None:
            return
        try:
            raw = msg.payload.decode("utf-8")
            payload = json.loads(raw) if raw.startswith("{") else {"value": raw}
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("Bản tin sai định dạng trên %s, bỏ qua", msg.topic)
            return
        try:
            handler(room_code, device_id, payload)
        except Exception:
            log.exception("Lỗi xử lý %s", msg.topic)

    # ------------------------------------------------------------ gửi
    def publish(self, topic_: str, payload, qos: int = 1, retain: bool = False) -> bool:
        data = payload if isinstance(payload, (str, bytes)) else json.dumps(payload, ensure_ascii=False)
        info = self.client.publish(topic_, data, qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # ------------------------------------------------------------ Dynamic Security
    def dynsec(self, *commands: dict, timeout: float = 5.0) -> list[dict]:
        """Gửi lệnh quản trị broker và chờ phản hồi. Lỗi -> RuntimeError (trừ lỗi 'đã tồn tại / không tồn tại')."""
        if not self.connected.wait(timeout):
            raise RuntimeError("Backend chưa kết nối được MQTT broker")
        corr = uuid.uuid4().hex
        cmds = [dict(c, correlationData=f"{corr}-{i}") for i, c in enumerate(commands)]
        done, box = threading.Event(), []
        self._pending[corr] = (done, box)
        self.publish(DYNSEC_TOPIC, {"commands": cmds})
        try:
            if not done.wait(timeout):
                raise RuntimeError("Broker không phản hồi lệnh Dynamic Security")
        finally:
            self._pending.pop(corr, None)
        for r in box:
            err = r.get("error")
            if err and not any(s in err.lower() for s in ("already exists", "not found")):
                raise RuntimeError(f"{r.get('command')}: {err}")
        return box

    def _on_dynsec_response(self, payload: bytes):
        try:
            responses = json.loads(payload).get("responses", [])
        except json.JSONDecodeError:
            return
        # Mosquitto trả phản hồi của mọi lệnh trong cùng 1 bản tin
        touched = set()
        for r in responses:
            corr = (r.get("correlationData") or "").rsplit("-", 1)[0]
            if corr in self._pending:
                self._pending[corr][1].append(r)
                touched.add(corr)
        for corr in touched:
            self._pending[corr][0].set()


bridge = MqttBridge()
