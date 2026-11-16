"""
Công bố server biên trong LAN bằng mDNS để thiết bị tự tìm, không ai phải nhập địa chỉ:

    Dịch vụ : _smart-exam._tcp.local    Hostname: exam-backend.local    Cổng: 8000

Cần gói `zeroconf` (pip install zeroconf). Thiếu gói hoặc mạng chặn multicast -> chỉ cảnh báo; khi đó đặt
bản ghi DNS/hosts "exam-backend.local" trỏ về máy này, firmware vẫn dùng hostname đó làm phương án dự phòng.
"""

import logging
import socket

from backend.edge.app.core.config import settings

log = logging.getLogger("mdns")
state = {"advertised": False, "detail": "chưa bật"}
_zc = None
_info = None


def start():
    global _zc, _info
    if not settings.mdns_enabled:
        state["detail"] = "tắt (EDGE_MDNS_ENABLED=false)"
        return
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        state["detail"] = "thiếu gói zeroconf (pip install zeroconf)"
        log.warning("mDNS không chạy: %s", state["detail"])
        return
    try:
        host = f"{settings.mdns_hostname}.local."
        _info = ServiceInfo(settings.mdns_service, f"{settings.edge_id}.{settings.mdns_service}",
                            addresses=[socket.inet_aton(settings.public_host)], port=settings.http_port,
                            properties={"edge_id": settings.edge_id, "path": "/api/v1/device-enrollments"},
                            server=host)
        _zc = Zeroconf()
        _zc.register_service(_info, allow_name_change=True)
        state.update(advertised=True, detail=f"{settings.mdns_service} -> {host}:{settings.http_port} "
                                             f"({settings.public_host})")
        log.info("mDNS: %s", state["detail"])
    except Exception as e:                              # cổng 5353 bị chiếm, không có card mạng...
        state["detail"] = f"lỗi: {e}"
        log.warning("mDNS không chạy: %s", e)


def stop():
    if _zc is not None:
        try:
            _zc.unregister_service(_info)
            _zc.close()
        except Exception:
            log.debug("Lỗi khi dừng mDNS", exc_info=True)
