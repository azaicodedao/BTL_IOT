"""
Migration v4: backend đơn khối -> server trung tâm + server biên. Chỉ cần khi dùng tiếp CSDL PostgreSQL của bản cũ.

- devices: thêm edge_id; nới độ dài fw_version / hw_version. Thiết bị cũ (khoá nằm trong secrets.h) không còn dùng
  được -> đánh dấu "decommissioned"; khi thiết bị tự đăng ký lại ở biên với cùng mã, bản ghi được cập nhật lại.
- checkin_records / checkin_attempts / suspicion_events: thêm edge_id + frame_ref (ảnh giờ nằm ở biên);
  gỡ cột frame_id cũ (ảnh cũ không còn truy cập được qua trung tâm).
- Xoá các bảng chỉ còn ở biên: frames, frame_jobs, commands, device_credentials, checkin_candidates, suspicion_state.
- Qdrant: gắn edge_id cho các mẫu khuôn mặt cũ để dashboard biết ảnh cắt nằm ở server biên nào
  (ảnh đã được chuyển sang backend/edge/storage/faces khi cấu trúc lại thư mục).

Chạy 1 lần (an toàn khi chạy lại), ở thư mục gốc:
    python -m database.migrations.migrate_v4 [--edge-id edge-01]
"""

import argparse
import sys

from sqlalchemy import text

from backend.central.app import models  # noqa: F401  (đăng ký bảng)
from backend.central.app.core.config import settings
from backend.central.app.core.db import Base, engine

STEPS = [
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS edge_id VARCHAR(40)",
    "ALTER TABLE devices ALTER COLUMN fw_version TYPE VARCHAR(30)",
    "ALTER TABLE devices ALTER COLUMN hw_version TYPE VARCHAR(60)",
    "ALTER TABLE devices DROP COLUMN IF EXISTS token_hash",
    "UPDATE devices SET state = 'decommissioned', online = false WHERE edge_id IS NULL",
    *[f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {c}" for t in ("checkin_records", "checkin_attempts", "suspicion_events")
      for c in ("edge_id VARCHAR(40)", "frame_ref INTEGER")],
    *[f"ALTER TABLE {t} DROP COLUMN IF EXISTS frame_id" for t in ("checkin_records", "checkin_attempts",
                                                                 "suspicion_events")],
    *[f"DROP TABLE IF EXISTS {t} CASCADE" for t in ("frame_jobs", "frames", "commands", "device_credentials",
                                                    "checkin_candidates", "suspicion_state")],
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge-id", default="edge-01",
                    help="Server biên đang giữ ảnh khuôn mặt cũ (backend/edge/storage/faces)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if engine.dialect.name != "postgresql":
        raise SystemExit("Migration này chỉ dành cho CSDL PostgreSQL của bản cũ.")

    with engine.begin() as conn:
        for sql in STEPS:
            conn.execute(text(sql))
            print("✓", sql[:100])
    Base.metadata.create_all(engine)
    print("✓ tạo bảng mới (edges, edge_events)")

    from qdrant_client.http import models as qm

    from backend.central.app.services import face_store
    client = face_store.client()
    legacy = qm.Filter(must=[qm.IsEmptyCondition(is_empty=qm.PayloadField(key="edge_id"))])
    n = client.count(settings.qdrant_collection, count_filter=legacy, exact=True).count
    if n:
        client.set_payload(settings.qdrant_collection, payload={"edge_id": args.edge_id}, points=legacy, wait=True)
    print(f"✓ Qdrant: gắn edge_id={args.edge_id} cho {n} mẫu khuôn mặt cũ")


if __name__ == "__main__":
    main()
