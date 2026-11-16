# Phân công công việc nhóm 4 người

## 1. Nguyên tắc phân chia

Dự án được chia theo **nhóm chức năng dọc**. Mỗi thành viên chịu trách nhiệm đầy đủ một luồng gồm backend, frontend, kiểm thử và tài liệu liên quan; không chia riêng người chỉ làm backend hoặc chỉ làm frontend.

| Thành viên | Module chính                      | Backend                                              | Frontend                   |
| ---------- | --------------------------------- | ---------------------------------------------------- | -------------------------- |
| Người 1    | Tài khoản và dữ liệu học vụ       | Auth, user, phòng, lớp, sinh viên, dữ liệu khuôn mặt | Login, Admin, People       |
| Người 2    | Thiết bị và Edge Console          | Provision, vòng đời thiết bị, MQTT, camera source    | Edge Console               |
| Người 3    | Telemetry, cảnh báo và điều khiển | Telemetry, rule, alert, command, stream, outbox      | Dashboard, Devices, Alerts |
| Người 4    | Ca thi, điểm danh và AI           | Exam session, check-in, nhận diện, giám sát gian lận | Exams, ExamDetail          |

## 2. Người 1 – Tài khoản, phân quyền và dữ liệu học vụ

### Backend

Phạm vi file chính:

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
- Quản lý tài khoản và phòng được cấp quyền.
- CRUD phòng, lớp và sinh viên.
- Quản lý sự đồng ý sử dụng dữ liệu khuôn mặt.
- Tạo vé đăng ký hoặc xem ảnh khuôn mặt.
- Lưu và quản lý embedding trong Qdrant.
- Ghi audit cho các thao tác người dùng.

### Frontend

Phạm vi file chính:

```text
frontend/dashboard/src/auth.jsx
frontend/dashboard/src/api.js
frontend/dashboard/src/pages/Login.jsx
frontend/dashboard/src/pages/Admin.jsx
frontend/dashboard/src/pages/People.jsx
```

Nhiệm vụ:

- Giao diện đăng nhập, đăng xuất và tự refresh token.
- Giao diện quản lý người dùng và đổi mật khẩu.
- Giao diện quản lý phòng, lớp và sinh viên.
- Giao diện đăng ký, xem và xóa mẫu khuôn mặt.
- Ẩn hoặc vô hiệu hóa thao tác theo vai trò.

### Kiểm thử

Tạo hoặc phụ trách `tests/integration/test_auth_people.py`, bao gồm:

- Đăng nhập đúng/sai và refresh token.
- Kiểm tra quyền của viewer, operator và admin.
- CRUD lớp và sinh viên.
- Consent dữ liệu khuôn mặt.
- Xác minh Central chỉ nhận vector, không nhận ảnh thật.

## 3. Người 2 – Thiết bị, MQTT và Edge Console

### Backend

Phạm vi file chính:

```text
backend/edge/app/routers/device_api.py
backend/edge/app/routers/console.py
backend/edge/app/services/enrollment_service.py
backend/edge/app/services/device_service.py
backend/edge/app/services/mqtt_bridge.py
backend/edge/app/services/mdns.py
backend/edge/app/services/camera_sources.py
backend/edge/app/services/ops.py
backend/edge/app/workers/virtual_device.py
backend/edge/app/workers/phone_bridge.py
deployment/edge/
firmware/
tests/simulators/sim_device.py
```

Nhiệm vụ:

- Thiết bị tự tìm Edge bằng mDNS và tự báo danh.
- Claim token, pair code, duyệt đăng ký và gán phòng.
- Cấp, đổi và thu hồi khóa MQTT.
- Quản lý tài khoản và ACL Mosquitto cho từng thiết bị.
- Đổi phòng, rekey, suspend, unsuspend và decommission.
- Chống giả mạo hardware UID/MAC.
- Xử lý factory reset và yêu cầu ghép lại.
- Quản lý camera HTTP, HTTPS, RTSP và camera điện thoại.
- Duy trì firmware và thiết bị mô phỏng.

### Frontend

Phụ trách toàn bộ:

```text
frontend/edge-console/index.html
frontend/edge-console/app.js
frontend/edge-console/app.css
```

Nhiệm vụ:

- Đăng nhập bảng điều khiển Edge.
- Hiển thị và duyệt thiết bị chờ đăng ký.
- Gán phòng, cấp lại khóa và chuyển trạng thái thiết bị.
- Quản lý nguồn camera.
- Gửi lệnh thử LCD/còi.
- Hiển thị trạng thái MQTT, Central link, mDNS và outbox.

### Kiểm thử

Phụ trách `tests/integration/test_camera_sources.py` và các kịch bản thiết bị trong `test_system.py`:

- Announce → register → assign room.
- Rekey và chuyển phòng.
- Suspend/unsuspend và decommission.
- Factory reset/reclaim.
- Chống giả mạo MAC.
- Camera điện thoại và camera source.

## 4. Người 3 – Telemetry, cảnh báo và điều khiển

### Backend

Phạm vi Central:

```text
backend/central/app/routers/devices.py
backend/central/app/routers/ops.py
backend/central/app/services/alert_service.py
backend/central/app/services/edge_events.py
backend/central/app/services/hub.py
```

Phạm vi Edge:

```text
backend/edge/app/services/telemetry_service.py
backend/edge/app/services/command_service.py
backend/edge/app/services/stream_service.py
backend/edge/app/services/uplink.py
```

Nhiệm vụ:

- Nhận và kiểm tra telemetry nhiệt độ, độ ẩm.
- Lưu telemetry dài hạn tại Central.
- Gửi trạng thái và khuyến nghị lên LCD.
- Tạo cảnh báo theo rule và trạng thái offline.
- Gửi lệnh còi/LCD, xử lý ACK, retry và timeout.
- Phát camera MJPEG.
- Cập nhật dashboard qua WebSocket.
- Lưu outbox khi Central mất kết nối và gửi bù không trùng.
- Xuất telemetry CSV.

### Frontend

Phạm vi file chính:

```text
frontend/dashboard/src/pages/Dashboard.jsx
frontend/dashboard/src/pages/Devices.jsx
frontend/dashboard/src/pages/Alerts.jsx
frontend/dashboard/src/components/LineChart.jsx
frontend/dashboard/src/live.js
```

Nhiệm vụ:

- Dashboard trạng thái phòng, thiết bị và camera.
- Biểu đồ nhiệt độ, độ ẩm.
- Trạng thái online/offline và lịch sử lệnh.
- Điều khiển LCD và còi.
- Danh sách, xác nhận và xử lý cảnh báo.
- Quản lý alert rule và tải CSV telemetry.
- Thông báo realtime trên dashboard.

### Kiểm thử

Phụ trách `tests/integration/test_offline_queue.py` và các kịch bản:

- Telemetry đi từ thiết bị đến Edge và Central.
- Telemetry điều khiển nội dung LCD.
- Thiết bị offline sinh cảnh báo và phục hồi.
- Lệnh có ACK, retry và timeout.
- Central mất kết nối, outbox bền vững và gửi lại không trùng.
- TTL của sự kiện realtime.

## 5. Người 4 – Ca thi, điểm danh và AI

### Backend

Phạm vi Central:

```text
backend/central/app/routers/sessions.py
backend/central/app/routers/edge.py
backend/central/app/services/session_service.py
backend/central/app/services/edge_link.py
```

Phạm vi Edge:

```text
backend/edge/app/services/exam_state.py
backend/edge/app/services/gallery.py
backend/edge/app/services/face_model.py
backend/edge/app/workers/common.py
backend/edge/app/workers/face_worker.py
backend/edge/app/workers/exam_worker.py
backend/edge/models/yolo11n.pt
```

Nhiệm vụ:

- Mở, bắt đầu và đóng ca thi.
- Đồng bộ roster và vector khuôn mặt xuống Edge.
- Chọn camera theo giai đoạn của ca thi.
- Nhận diện sinh viên trong và ngoài danh sách thi.
- Xác nhận khuôn mặt qua nhiều frame, tránh điểm danh trùng.
- Điểm danh tự động và thủ công.
- Phát hiện hành vi nghi vấn bằng YOLO.
- Xác nhận hoặc bác bỏ nghi vấn và điều khiển còi cảnh báo.
- Cấp ticket xem ảnh bằng chứng tại Edge.

### Frontend

Phạm vi file chính:

```text
frontend/dashboard/src/pages/Exams.jsx
frontend/dashboard/src/pages/ExamDetail.jsx
```

Nhiệm vụ:

- Tạo và hiển thị danh sách ca thi.
- Theo dõi giai đoạn check-in và làm bài.
- Hiển thị sinh viên đã vào, bị từ chối hoặc vắng.
- Điểm danh thủ công.
- Hiển thị nhiều camera trong phòng.
- Hiển thị, xác nhận hoặc bác bỏ nghi vấn.
- Xem ảnh bằng chứng và điều khiển dừng còi.

### Kiểm thử

Phụ trách các kịch bản:

- Mở ca và tải roster xuống Edge.
- Upload frame và tạo job AI.
- Nhận diện đúng sinh viên và không điểm danh trùng.
- Nhận diện người ngoài roster.
- Đồng bộ quyết định thủ công xuống Edge.
- Phát hiện và xác nhận nghi vấn.
- Ảnh chỉ tồn tại tại Edge và cần ticket để xem.
- Đóng ca và đánh dấu sinh viên vắng.

## 6. Quyền sở hữu file dùng chung

Các file sau dễ xảy ra xung đột khi nhiều người cùng sửa:

```text
backend/central/app/models.py
backend/edge/app/models.py
backend/central/app/main.py
backend/edge/app/main.py
frontend/dashboard/src/App.jsx
frontend/dashboard/src/ui.jsx
tests/conftest.py
README.md
.env.example
```

Quy ước quản lý:

- Người 1 quản lý `backend/central/app/models.py`, `frontend/dashboard/src/App.jsx` và luồng xác thực trong `api.js`.
- Người 2 quản lý `backend/edge/app/models.py` và `backend/edge/app/main.py`.
- Người 3 quản lý `tests/conftest.py`, `live.js` và hợp đồng WebSocket dashboard.
- Người 4 phụ trách tích hợp cuối, cập nhật kiến trúc và kịch bản demo toàn hệ thống.
- Khi thành viên khác cần sửa file dùng chung, tạo commit nhỏ riêng và thông báo người sở hữu trước khi merge.

## 7. Quy trình Git

Tên nhánh đề xuất:

```text
feature/member-1-auth-people
feature/member-2-device-edge-console
feature/member-3-telemetry-alerts
feature/member-4-exam-ai
```

Quy trình làm việc:

1. Mỗi thành viên làm trên nhánh riêng, không commit trực tiếp lên nhánh chính.
2. Mỗi commit chỉ nên xử lý một chức năng hoặc một lỗi rõ ràng.
3. Trước khi tạo pull request, cập nhật nhánh chính và giải quyết conflict trên nhánh cá nhân.
4. Pull request phải ghi rõ API thay đổi, màn hình liên quan và test đã chạy.
5. Ít nhất một thành viên khác review trước khi merge.
6. Sau khi merge, chạy lại toàn bộ test và build frontend.

Thứ tự tích hợp đề xuất:

1. Người 1 hoàn thiện xác thực và dữ liệu phòng/lớp/sinh viên.
2. Người 2 hoàn thiện thiết bị, MQTT và Edge Console.
3. Người 3 nối telemetry, command, cảnh báo và dashboard.
4. Người 4 nối ca thi, điểm danh và AI.
5. Cả nhóm chạy test end-to-end và thực hiện demo tổng thể.

## 8. Tiêu chí hoàn thành của mỗi thành viên

Mỗi thành viên cần bàn giao:

- Backend/API của module hoạt động và có kiểm tra quyền phù hợp.
- Frontend gọi API thật, xử lý trạng thái tải và lỗi.
- Có test cho luồng chính và trường hợp bị từ chối.
- Không đưa khóa, mật khẩu hoặc dữ liệu cá nhân thật lên Git.
- Cập nhật tài liệu nếu API, cấu hình hoặc cách chạy thay đổi.
- Có thể tự trình bày và demo phần mình phụ trách từ frontend đến dữ liệu backend.
