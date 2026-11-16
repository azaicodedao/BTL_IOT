# Database

Hệ thống dùng ba nơi lưu trữ:

- PostgreSQL tại Central cho người dùng, phòng, lớp, sinh viên, thiết bị phản chiếu, ca thi, telemetry, cảnh báo và audit.
- Qdrant tại Central cho embedding khuôn mặt.
- SQLite tại mỗi Edge cho thiết bị, thông tin cấp quyền, ảnh, job AI và outbox gửi bù.

`migrations/migrate_v4.py` chuyển CSDL PostgreSQL của backend đơn khối cũ sang kiến trúc Central–Edge. Chạy từ thư mục gốc:

```bash
python -m database.migrations.migrate_v4 --edge-id edge-01
```

Migration không chứa dữ liệu mẫu hoặc bí mật. Không commit file SQLite runtime, volume PostgreSQL hoặc bản sao dữ liệu người dùng thật.
