"""
Luồng C - vi khí hậu (mục 9.3.3), vòng lặp cục bộ tại biên:
ESP32-CAM publish telemetry -> kiểm tra schema, miền giá trị, loại trùng msg_id (RAM)
-> publish display (retain) cho LCD cùng phòng NGAY (không chờ trung tâm)
-> xếp sự kiện "telemetry" lên trung tâm (lưu chuỗi thời gian, đánh giá quy tắc cảnh báo).
Trung tâm trả về khuyến nghị đang có hiệu lực của phòng -> biên cập nhật dòng khuyến nghị trên LCD.
"""

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import Device, DeviceState as S
from backend.edge.app.services import device_service, uplink
from backend.shared.db import utcnow

log = logging.getLogger("telemetry")

_recent_ids: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))   # loại trùng nhanh trong RAM
_room_advice: dict[str, list[str]] = {}                                  # khuyến nghị gần nhất trung tâm trả về
_room_last: dict[str, tuple[float, float]] = {}                          # (nhiệt độ, độ ẩm) gần nhất của phòng


def _parse_ts(raw) -> datetime:
    """ISO 8601 UTC từ thiết bị; thiếu/sai (chưa đồng bộ NTP) -> dùng thời điểm nhận."""
    if raw:
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            ts = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if abs((utcnow() - ts).total_seconds()) < 86400:
                return ts
        except ValueError:
            pass
    return utcnow()


def _num(payload, *keys):
    for k in keys:
        v = payload.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _show(room_code: str):
    temp, hum = _room_last[room_code]
    advice = _room_advice.get(room_code) or []
    device_service.push_display(room_code, {"room": room_code, "temp_c": round(temp, 1), "hum_pct": round(hum, 0),
                                            "status": "warn" if advice else "ok",
                                            "advice": advice[0][:40] if advice else ""})


def on_telemetry(room_code, device_id, payload):
    temp = _num(payload, "temperature_c", "temp_c")
    hum = _num(payload, "humidity_pct", "hum_pct")
    msg_id = str(payload.get("msg_id") or "")[:40]

    if not msg_id or temp is None or hum is None:
        log.warning("[%s] telemetry thiếu trường bắt buộc: %s", device_id, payload)
        return
    if not (settings.temp_min <= temp <= settings.temp_max and settings.hum_min <= hum <= settings.hum_max):
        log.warning("[%s] telemetry ngoài miền giá trị DHT11: %.1f°C %.1f%%", device_id, temp, hum)
        return

    with session_scope() as db:
        d = db.query(Device).filter(Device.device_id == device_id).one_or_none()
        if d is None or d.room_code != room_code:
            return
        if d.state not in (S.ACTIVE, S.FAULT, S.OFFLINE):     # chỉ nhận từ thiết bị đang hoạt động
            log.info("[%s] bỏ telemetry vì trạng thái %s", device_id, d.state)
            return
        if msg_id in _recent_ids[device_id]:
            return                                             # QoS 1 gửi lại
        _recent_ids[device_id].append(msg_id)
        uplink.emit(db, "telemetry", {"device_id": device_id, "room_code": room_code, "msg_id": msg_id,
                                      "ts": _parse_ts(payload.get("ts")).isoformat(),
                                      "temperature_c": temp, "humidity_pct": hum})

    _room_last[room_code] = (temp, hum)
    _show(room_code)


def on_central_result(data: dict, result: dict):
    """Trung tâm đã lưu telemetry và trả khuyến nghị (từ quy tắc cảnh báo). Đổi khuyến nghị -> vẽ lại LCD."""
    room_code, advice = data.get("room_code"), list(result.get("advice") or [])
    if room_code in _room_last and advice != (_room_advice.get(room_code) or []):
        _room_advice[room_code] = advice
        _show(room_code)


uplink.result_handlers["telemetry"] = on_central_result
