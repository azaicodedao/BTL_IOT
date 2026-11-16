"""Cấu hình server biên. Ghi đè bằng biến môi trường hoặc file .env ở gốc dự án."""

import socket
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

EDGE_DIR = Path(__file__).resolve().parents[2]          # backend/edge/
ROOT = EDGE_DIR.parents[1]                              # thư mục gốc repository


def lan_ip() -> str:
    """IP LAN hiện tại của máy (thiết bị dùng IP này để tới broker / API). Không gửi gói tin nào."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGE_", env_file=ROOT / ".env", extra="ignore")

    # --- Danh tính server biên + liên kết lên trung tâm ---
    edge_id: str = "edge-01"
    name: str = "Server biên phòng học"
    central_url: str = "http://127.0.0.1:9000"
    central_token: str = "edge-secret"              # khoá chung với trung tâm (CENTRAL_EDGE_TOKENS)
    sync_interval_s: int = 30

    # --- HTTP ---
    http_port: int = 8000                           # firmware tìm dịch vụ mDNS trên cổng này
    public_host: str = ""                           # IP/hostname mà THIẾT BỊ và trình duyệt dùng; mặc định tự dò IP LAN

    # --- CSDL cục bộ (ADR-09: SQLite, không cần quản trị) ---
    database_url: str = f"sqlite:///{(EDGE_DIR / 'data' / 'edge.db').as_posix()}"
    storage_dir: Path = EDGE_DIR / "storage"
    data_dir: Path = EDGE_DIR / "data"
    yolo_weights: Path = EDGE_DIR / "models" / "yolo11n.pt"

    # --- MQTT (ADR-01, ADR-06) ---
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = "backend"
    mqtt_password: str = "backend-secret"

    # --- Tự phát hiện trong LAN (mDNS) ---
    mdns_enabled: bool = True
    mdns_service: str = "_smart-exam._tcp.local."
    mdns_hostname: str = "exam-backend"             # -> exam-backend.local

    # --- Bảo mật ---
    admin_password: str = "edge-admin"              # mật khẩu bảng điều khiển biên (đổi khi triển khai)
    jwt_secret: str = "doi-chuoi-nay-o-server-bien"
    console_token_hours: int = 12
    service_token: str = "edge-worker-token"        # worker AI gọi API nội bộ
    max_pending_enrollments: int = 50

    # --- Ảnh: chỉ lưu tại biên, tự xoá ---
    frame_max_bytes: int = 100 * 1024
    frame_retention_days: int = 7

    # --- Độ tin cậy (mục 8.3) ---
    heartbeat_timeout_s: int = 30
    command_ack_timeout_s: float = 5.0
    command_max_retries: int = 2
    command_ttl_s: int = 60
    alarm_auto_stop_s: int = 120
    enrollment_poll_s: int = 5

    # --- Miền giá trị DHT11 ---
    temp_min: float = 0.0
    temp_max: float = 50.0
    hum_min: float = 20.0
    hum_max: float = 90.0

    @property
    def public_url(self) -> str:
        return f"http://{self.public_host}:{self.http_port}"


settings = Settings()
settings.public_host = settings.public_host or lan_ip()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.mkdir(parents=True, exist_ok=True)
