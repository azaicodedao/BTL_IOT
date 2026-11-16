"""Cấu hình server trung tâm. Ghi đè bằng biến môi trường hoặc file .env ở gốc dự án."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CENTRAL_DIR = Path(__file__).resolve().parents[2]          # backend/central/
ROOT = CENTRAL_DIR.parents[1]                              # thư mục gốc repository


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CENTRAL_", env_file=ROOT / ".env", extra="ignore")

    # --- CSDL (ADR-04) ---
    database_url: str = "postgresql+psycopg2://classroom:classroom@localhost:5432/classroom"
    # URL Qdrant (http://...) hoặc đường dẫn thư mục / ":memory:" để chạy Qdrant nhúng (thử nghiệm, kiểm thử)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "face_embeddings"

    # --- Server biên được phép kết nối: "edge-01:khoá1,edge-02:khoá2" (khoá = EDGE_CENTRAL_TOKEN của biên đó) ---
    edge_tokens: str = "edge-01:edge-secret"
    edge_rpc_timeout_s: float = 8.0
    ticket_ttl_s: int = 300                         # vé xem ảnh / camera trực tiếp tại biên

    # --- Bảo mật ---
    jwt_secret: str = "doi-chuoi-nay-khi-trien-khai-that"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    # --- Nghiệp vụ ---
    alarm_auto_stop_s: int = 120                    # còi liên tục tự tắt sau 2 phút
    face_sim_threshold: float = 0.45                # nhận dạng ngoài danh sách dự thi (biên gửi vector lên hỏi)
    face_margin: float = 0.05

    # --- Tài khoản quản trị tạo lần đầu ---
    admin_email: str = "admin@classroom.local"
    admin_password: str = "Admin@123"

    def edge_token_map(self) -> dict[str, str]:
        pairs = (item.split(":", 1) for item in self.edge_tokens.split(",") if ":" in item)
        return {edge_id.strip(): token.strip() for edge_id, token in pairs}


settings = Settings()
