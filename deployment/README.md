# Deployment

- `central/docker-compose.yml`: PostgreSQL và Qdrant cho server trung tâm.
- `edge/docker-compose.yml`: Mosquitto cho server biên.
- `edge/init_mqtt.py`: tạo cấu hình Dynamic Security ban đầu cho tài khoản backend.

Chạy từ thư mục gốc repository:

```bash
cd deployment/central
docker compose up -d

cd ../edge
python init_mqtt.py
docker compose up -d
```

Mật khẩu truyền cho `init_mqtt.py` phải trùng với `EDGE_MQTT_PASSWORD` trong `.env`. Không commit thư mục `deployment/edge/mosquitto/data/`.
