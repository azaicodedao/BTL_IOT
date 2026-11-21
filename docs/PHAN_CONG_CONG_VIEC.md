# Phân công công việc nhóm 4 người

## 1. Nguyên tắc phân chia

Dự án được chia theo bốn luồng chức năng dọc. Mỗi thành viên đều có phần backend, frontend, kiểm thử và tài liệu. Hai thành viên trực tiếp phụ trách phần nhúng:

- **Người 1:** AI, camera và firmware ESP32-CAM.
- **Người 2:** quản lý thiết bị, MQTT và firmware ESP8266 LCD/còi.
- **Người 3:** tài khoản, phân quyền và dữ liệu học vụ.
- **Người 4:** telemetry, cảnh báo và vận hành tập trung.

| Thành viên | Module chính                          | Firmware/phần cứng                     | Backend                                                  | Frontend                   |
| ---------- | ------------------------------------- | -------------------------------------- | -------------------------------------------------------- | -------------------------- |
| Người 1    | AI, camera, điểm danh và giám sát thi | ESP32-CAM + DHT11                      | Camera, frame, face worker, exam worker, ca thi          | Exams, ExamDetail          |
| Người 2    | Thiết bị, MQTT, LCD và còi            | ESP8266 + LCD + buzzer, SmartProvision | Provision, vòng đời thiết bị, MQTT, command              | Edge Console               |
| Người 3    | Tài khoản và dữ liệu học vụ           | Hỗ trợ dữ liệu đăng ký khuôn mặt       | Auth, user, phòng, lớp, sinh viên, face store            | Login, Admin, People       |
| Người 4    | Telemetry, cảnh báo và dashboard      | Kiểm thử tích hợp cảm biến             | Telemetry, alert, outbox, WebSocket, thiết bị phản chiếu | Dashboard, Devices, Alerts |

## 2. Người 1 – AI, kết nối camera và ca thi

Đây là thành viên đã chọn phần **AI + kết nối CAM**. Người 1 sở hữu toàn bộ luồng từ ESP32-CAM gửi ảnh đến Edge, xử lý AI và hiển thị kết quả trên giao diện ca thi.

### Firmware và phần cứng

```text
firmware/node_a_cam/
hardware/wiring/esp32_cam_dht11.md
hardware/images/prototype/       # ảnh prototype camera
```

Nhiệm vụ:

- Firmware ESP32-CAM AI-Thinker và DHT11.
- Chụp và upload JPEG lên Edge.
- Chế độ camera `idle`, `preview`, `checkin`, `exam`.
- Hàng đợi telemetry cục bộ của camera.
- Heartbeat và tự kết nối lại.
- Kiểm thử nút Setup GPIO12, camera OV2640 và cảm biến DHT11.
- Ghi lại sơ đồ nối dây và kết quả thử nghiệm camera thật.

`SmartProvision.h` dùng chung do Người 2 quản lý. Nếu Người 1 cần thay đổi quy trình provision thì phối hợp với Người 2, không sửa độc lập hai bản sao.

### Backend Central

```text
backend/central/app/routers/sessions.py
backend/central/app/services/session_service.py
backend/central/app/services/edge_link.py
```

Nhiệm vụ:

- Mở, bắt đầu và đóng ca thi.
- Quản lý giai đoạn check-in và làm bài.
- Đồng bộ roster/vector xuống Edge.
- Điểm danh tự động và thủ công.
- Lưu lượt bị từ chối và sự kiện nghi vấn.
- Cấp ticket xem camera và ảnh bằng chứng.
- Gửi yêu cầu điều khiển liên quan ca thi xuống Edge.

### Backend Edge và AI

```text
backend/edge/app/routers/internal.py
backend/edge/app/services/camera_sources.py
backend/edge/app/services/stream_service.py
backend/edge/app/services/exam_state.py
backend/edge/app/services/gallery.py
backend/edge/app/services/face_model.py
backend/edge/app/workers/common.py
backend/edge/app/workers/face_worker.py
backend/edge/app/workers/exam_worker.py
backend/edge/app/workers/phone_bridge.py
backend/edge/models/yolo11n.pt
```

Nhiệm vụ:

- Nhận và phát frame camera MJPEG.
- Quản lý camera ESP32-CAM, điện thoại, HTTP/HTTPS/RTSP.
- InsightFace: trích xuất embedding và so khớp khuôn mặt.
- Xác nhận sinh viên qua nhiều frame và chống điểm danh trùng.
- Nhận diện người ngoài danh sách dự thi.
- YOLO phát hiện hành vi nghi vấn.
- Lưu ảnh bằng chứng tại Edge, không gửi ảnh thật lên Central.
- Gọi API nội bộ báo kết quả điểm danh/nghi vấn.

### Frontend

```text
frontend/dashboard/src/pages/Exams.jsx
frontend/dashboard/src/pages/ExamDetail.jsx
```

Nhiệm vụ:

- Tạo và hiển thị danh sách ca thi.
- Hiển thị nhiều camera trong phòng.
- Theo dõi check-in và giai đoạn làm bài.
- Hiển thị sinh viên đã vào, bị từ chối hoặc vắng.
- Điểm danh thủ công.
- Hiển thị, xác nhận hoặc bác bỏ nghi vấn.
- Xem ảnh bằng chứng và dừng còi cảnh báo.

### Kiểm thử

Phụ trách `tests/integration/test_exam_ai.py` và các kịch bản:

- Camera upload frame và tạo job AI.
- Mở ca tải roster xuống Edge.
- Nhận diện đúng sinh viên, không điểm danh trùng.
- Nhận diện ngoài roster.
- Phát hiện nghi vấn bằng YOLO.
- Ảnh chỉ lưu tại Edge và cần ticket để xem.
- Đóng ca và đánh dấu vắng.

## 3. Người 2 – Thiết bị, MQTT, LCD và còi

Người 2 sở hữu nền tảng IoT chung: provision, MQTT, vòng đời thiết bị và node chấp hành ESP8266.

### Firmware và phần cứng

```text
firmware/common/SmartProvision.h
firmware/node_b_lcd/
firmware/sync_common.py
hardware/BOM.md
hardware/wiring/esp8266_lcd_buzzer.md
hardware/images/prototype/       # ảnh LCD/còi
```

Nhiệm vụ:

- Firmware ESP8266 NodeMCU, LCD 1602 I2C và buzzer.
- Nhận lệnh display/buzzer và gửi ACK.
- Chống thực thi trùng command ID.
- Heartbeat và trạng thái online/offline.
- Duy trì SmartProvision dùng chung cho cả hai board.
- Setup Wi-Fi, mDNS, claim token và tự provision lại.
- Đồng bộ `SmartProvision.h` vào hai sketch.
- Kiểm thử LCD, còi, nút FLASH và nguồn cấp.

### Backend Edge

```text
backend/edge/app/routers/device_api.py
backend/edge/app/routers/console.py
backend/edge/app/services/enrollment_service.py
backend/edge/app/services/device_service.py
backend/edge/app/services/mqtt_bridge.py
backend/edge/app/services/mdns.py
backend/edge/app/services/command_service.py
backend/edge/app/services/ops.py
backend/edge/app/workers/virtual_device.py
deployment/edge/
tests/simulators/sim_device.py
```

Nhiệm vụ:

- Thiết bị tìm Edge bằng mDNS và tự báo danh.
- Claim token, pair code, đăng ký và gán phòng.
- Cấp, đổi và thu hồi khóa MQTT.
- Tạo tài khoản/ACL Mosquitto cho từng thiết bị.
- Rekey, chuyển phòng, suspend, unsuspend và decommission.
- Factory reset, reclaim và chống giả mạo hardware UID/MAC.
- Gửi lệnh LCD/còi, xử lý ACK, retry và timeout.
- Duy trì thiết bị mô phỏng.

### Frontend

Phụ trách toàn bộ:

```text
frontend/edge-console/index.html
frontend/edge-console/app.js
frontend/edge-console/app.css
```

Nhiệm vụ:

- Đăng nhập Edge Console.
- Duyệt thiết bị chờ đăng ký và gán phòng.
- Cấp lại khóa, khóa/mở khóa và thu hồi thiết bị.
- Quản lý nguồn camera phối hợp với Người 1.
- Gửi lệnh thử LCD/còi.
- Hiển thị trạng thái MQTT, mDNS, Central link và outbox.

### Kiểm thử

Phụ trách `tests/integration/test_device_lifecycle.py`, `test_camera_sources.py` và các kịch bản:

- Announce → register → assign room.
- MQTT online và ACL đúng phòng.
- Lệnh LCD/còi có ACK, retry và timeout.
- Rekey, chuyển phòng, suspend/unsuspend.
- Factory reset/reclaim, giả mạo MAC và decommission.

## 4. Người 3 – Tài khoản, phân quyền và dữ liệu học vụ

### Backend

```text
backend/central/app/routers/auth.py
backend/central/app/routers/users.py
backend/central/app/routers/people.py
backend/central/app/core/security.py
backend/central/app/core/deps.py
backend/central/app/services/audit.py
backend/central/app/services/face_store.py
```

Nhiệm vụ:

- Đăng nhập, refresh token, đăng xuất và đổi mật khẩu.
- Phân quyền `admin`, `operator`, `viewer`.
- Quản lý người dùng và phòng được cấp quyền.
- CRUD phòng, lớp và sinh viên.
- Quản lý consent dữ liệu khuôn mặt.
- Lưu embedding vào Qdrant.
- Tạo vé đăng ký/xem ảnh khuôn mặt.
- Audit thao tác người dùng.
- Thống nhất hợp đồng dữ liệu khuôn mặt với Người 1.

### Frontend

```text
frontend/dashboard/src/auth.jsx
frontend/dashboard/src/api.js
frontend/dashboard/src/pages/Login.jsx
frontend/dashboard/src/pages/Admin.jsx
frontend/dashboard/src/pages/People.jsx
```

Nhiệm vụ:

- Đăng nhập, đăng xuất và tự refresh token.
- Quản lý tài khoản và đổi mật khẩu.
- Quản lý phòng, lớp và sinh viên.
- Đăng ký, xem và xóa mẫu khuôn mặt.
- Kiểm soát giao diện theo vai trò.

### Kiểm thử

Phụ trách `tests/integration/test_auth_people.py`:

- Xác thực và refresh token.
- Quyền admin/operator/viewer.
- CRUD lớp và sinh viên.
- Consent và dữ liệu khuôn mặt.
- Central chỉ nhận embedding, không nhận ảnh thật.

## 5. Người 4 – Telemetry, cảnh báo và vận hành tập trung

### Backend Central

```text
backend/central/app/routers/devices.py
backend/central/app/routers/ops.py
backend/central/app/routers/edge.py
backend/central/app/services/alert_service.py
backend/central/app/services/edge_events.py
backend/central/app/services/hub.py
deployment/central/
database/
```

### Backend Edge

```text
backend/edge/app/services/telemetry_service.py
backend/edge/app/services/uplink.py
```

Nhiệm vụ:

- Nhận telemetry từ MQTT và kiểm tra miền giá trị.
- Đồng bộ telemetry, trạng thái thiết bị và kết quả Edge lên Central.
- Outbox bền vững khi Central mất kết nối.
- Loại trùng sự kiện khi gửi lại.
- Tạo cảnh báo theo rule và thiết bị offline.
- Quản lý trạng thái Edge kết nối.
- WebSocket realtime cho dashboard.
- Tra cứu và xuất telemetry CSV.
- Quản lý PostgreSQL, Qdrant và migration.

Người 4 xử lý event điểm danh/nghi vấn ở mức tiếp nhận và lưu trữ; thuật toán AI và nội dung event do Người 1 sở hữu.

### Frontend

```text
frontend/dashboard/src/pages/Dashboard.jsx
frontend/dashboard/src/pages/Devices.jsx
frontend/dashboard/src/pages/Alerts.jsx
frontend/dashboard/src/components/LineChart.jsx
frontend/dashboard/src/live.js
```

Nhiệm vụ:

- Dashboard trạng thái phòng và thiết bị.
- Biểu đồ nhiệt độ, độ ẩm.
- Trạng thái online/offline và Edge link.
- Danh sách/lịch sử lệnh thiết bị.
- Cảnh báo realtime và alert rule.
- Xác nhận, xử lý cảnh báo và tải CSV telemetry.

### Kiểm thử

Phụ trách `tests/integration/test_telemetry_alerts.py` và `test_offline_queue.py`:

- Telemetry từ Edge đến Central.
- Thiết bị offline sinh cảnh báo và phục hồi.
- Central mất kết nối, outbox vẫn bền vững.
- Gửi lại sự kiện không trùng.
- Lỗi handler không làm mất dữ liệu.
- WebSocket và TTL sự kiện realtime.

## 6. Phối hợp giữa các thành viên

| Giao diện phối hợp                 | Người cung cấp | Người sử dụng                               |
| ---------------------------------- | -------------- | ------------------------------------------- |
| ESP32-CAM upload frame             | Người 1        | Edge API của Người 1, provision của Người 2 |
| SmartProvision dùng chung          | Người 2        | Firmware camera Người 1 và LCD Người 2      |
| Lệnh buzzer khi điểm danh/nghi vấn | Người 1        | Command/MQTT của Người 2                    |
| Embedding khuôn mặt                | Người 1        | Face store/Qdrant của Người 3               |
| Event điểm danh/nghi vấn           | Người 1        | Uplink và edge events của Người 4           |
| Trạng thái thiết bị/telemetry      | Người 2        | Dashboard và cảnh báo của Người 4           |

Mọi giao diện phối hợp phải có payload mẫu hoặc test trước khi hai nhánh được merge.

## 7. File dùng chung và người quản lý

```text
backend/central/app/models.py
backend/edge/app/models.py
backend/central/app/main.py
backend/edge/app/main.py
backend/shared/
frontend/dashboard/src/App.jsx
frontend/dashboard/src/ui.jsx
tests/conftest.py
README.md
.env.example
```

Quy ước:

- Người 3 quản lý `backend/central/app/models.py` và luồng xác thực frontend.
- Người 2 quản lý `backend/edge/app/models.py`, `backend/edge/app/main.py` và SmartProvision.
- Người 4 quản lý `tests/conftest.py`, `backend/central/app/main.py`, WebSocket và cấu hình triển khai Central.
- Người 1 quản lý hợp đồng frame/job AI và trang chi tiết ca thi.
- Thay đổi file dùng chung phải nằm trong commit riêng và được người quản lý file review.

## 8. Nhánh Git

```text
feature/member-1-ai-camera-exam
feature/member-2-device-mqtt-firmware
feature/member-3-auth-people
feature/member-4-telemetry-alerts
```

Mỗi thành viên chỉ stage file thuộc phạm vi của mình; không dùng `git add .` khi workspace có thay đổi của người khác.

Thứ tự tích hợp đề xuất:

1. Người 2 hoàn thiện provision, MQTT và SmartProvision dùng chung.
2. Người 3 hoàn thiện auth, phòng/lớp/sinh viên và face store.
3. Người 4 hoàn thiện telemetry, outbox, cảnh báo và dashboard.
4. Người 1 tích hợp camera, AI, ca thi và kết quả lên toàn hệ thống.
5. Cả nhóm chạy test end-to-end và demo phần cứng.

## 9. Tiêu chí bàn giao

Mỗi thành viên cần bàn giao:

- Backend/API của module hoạt động và kiểm tra quyền phù hợp.
- Frontend gọi API thật, có trạng thái tải và xử lý lỗi.
- Test cho luồng chính và trường hợp thất bại.
- Tài liệu cấu hình, kết nối hoặc payload liên quan.
- Không commit khóa, mật khẩu, Wi-Fi hoặc dữ liệu cá nhân thật.
- Có thể tự demo phần mình từ giao diện/phần cứng đến dữ liệu backend.
