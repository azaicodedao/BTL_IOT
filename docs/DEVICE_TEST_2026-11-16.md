# Kiểm tra vòng đời thiết bị ngày 16/11/2026

## Lưu tạm khi mất trung tâm

- Gia cố outbox SQLite sẵn có: chỉ xoá ID được trung tâm xác nhận sau commit; không đánh dấu xử lý thành công khi handler lỗi. Mất phản hồi giữ nguyên ID để trung tâm loại trùng lúc gửi lại. Kết nối WebSocket phục hồi đánh thức bộ gửi.
- Biên hiển thị trạng thái lưu tạm, số bản ghi, tuổi bản ghi cũ nhất, lỗi gửi, lịch thử lại và nút yêu cầu đồng bộ. Kết quả AI gửi bù trong khoảng thời gian ca được chấp nhận kể cả khi ca đã đóng, giữ quyết định thủ công.
- 36 test đạt: kiểm tra mất mạng, đọc outbox bằng tiến trình mới, phản hồi mất sau commit, thiếu ACK, lỗi handler rồi khôi phục, TTL chỉ báo tức thời, bảo vệ API và sửa vắng tự động từ kết quả điểm danh gửi muộn.
- Đã cập nhật và khởi động lại cả trung tâm lẫn biên; nguồn camera điện thoại được chạy lại. Test trên hạ tầng thật: dừng trung tâm, gửi một sự kiện thử không liên quan ca thật qua API nội bộ, xác minh bản ghi SQLite và MQTT vẫn kết nối; bật trung tâm lại, xác minh một EdgeEvent receipt, bản ghi cục bộ được xoá và outbox=0. Không tạo điểm danh/nghi vấn giả trong ca thật.

## Xem nhiều camera trong một ca thi

- Trang chi tiết ca thi hiển thị mọi camera chưa thu hồi thuộc phòng, theo lưới hai cột khi màn hình đủ rộng; gắn nhãn vai trò và camera chính của giai đoạn. Giữ luồng xem khi chuyển tab nghiệp vụ, cập nhật danh sách khi có device.status. Ca đã đóng không mở luồng trực tiếp.
- Trang tổng quan phòng cũng hiển thị các camera thay vì chỉ một camera theo giai đoạn. Quy tắc chọn camera cho AI vẫn theo giai đoạn như trước.
- Build Vite thành công. Kiểm tra hai luồng đồng thời bằng vé do trung tâm cấp: cam-19e5c4 và cam-efde5c đều trả JPEG thật từ biên. Dashboard :9000 phục vụ bản build.

## Thêm camera điện thoại qua giao diện biên

- Đã thêm form quản lý nguồn HTTP/HTTPS/RTSP, dừng/kết nối lại, sửa URL và xoá nguồn; backend lưu cấu hình và quản lý tiến trình bridge, dùng lại quy trình announce/register/assign/credentials hiện có.
- 31 test đạt; kiểm tra cú pháp JavaScript và Python đạt. Backend biên đã khởi động lại để áp dụng.
- Smoke test qua API backend đang chạy với nguồn MJPEG tạm: tạo bridge thật → xuất hiện trong hàng chờ → duyệt và gán phòng tạm PHONE_SMOKE → MQTT online → đọc được JPEG qua API xem camera của biên. Đã xoá nguồn và thiết bị tạm sau test, audit/ảnh lưu giữ theo chính sách hiện tại.
- Chưa thêm điện thoại thật vì chưa có URL video của người dùng. Worker giám sát ca thi vẫn cần chạy riêng theo hướng dẫn vận hành.

## Điểm danh khuôn mặt trên phần cứng

- CAM và LCD đã online trong phòng A101; ca 5 đang checkin. Nguyên nhân ban đầu chưa điểm danh: chưa có tiến trình face_worker.
- Đã khởi động `python -u -m backend.edge.app.workers.face_worker` ở nền (PID được ghi trong logs/face_worker.pid). Log hiện tại: logs/face_worker_20261116.out.log và logs/face_worker_20261116.err.log. Môi trường thiếu CUDA 13 DLL; InsightFace vẫn hoạt động qua CPU.
- Lúc 02:41:37 giờ địa phương, worker ghi nhận sinh viên thuộc danh sách ca 5, confidence 0.538, frame 948. Bản ghi CheckinDone đã có; các job mới xử lý trạng thái done, không lỗi.
- Lệnh buzzer once do checkin_worker phát cho LCD thật đã ACK result=ok trong lần gửi đầu tiên. Chưa xác nhận âm thanh bằng tai.

## Cập nhật sau khi kiểm tra trang Setup CAM

- Cổng đã đổi: COM10 là LCD (log xác nhận MQTT hoạt động), COM8 là ESP32 MAC `30:76:F5:EF:DE:5C`.
- Đã sửa lỗi xử lý quét: không tự lặp vô hạn khi driver báo scan_failed; trang dừng chờ sau 30 giây, báo lỗi và mở ô nhập SSID thủ công. Không thay đổi thông tin mạng hoặc ghi cứng SSID/IP.
- Đã kiểm tra JavaScript cho quét thất bại, hết thời gian chờ và hiển thị kết quả; biên dịch ESP32 thành công. Đã nạp vào CAM COM8 thành công, hash xác minh đúng. Kết quả này thay thế trạng thái chưa nạp được CAM ở lần thử trước bên dưới.
- Chưa xác minh kết quả quét thực tế qua trình duyệt của người dùng. Bản sửa xử lý lỗi và thông báo; chưa kết luận nguyên nhân driver không tìm được mạng.
- Sau khi người dùng xác nhận quét vẫn lỗi: Serial CAM xác nhận đã lưu Wi-Fi và danh tính `cam-efde5c`. Đã sửa xung đột thử kết nối STA cũ với quét: tắt auto reconnect trong Setup, huỷ lần kết nối cũ trước quét, giới hạn lần thử nền và dừng thử nền khi có người dùng Setup; bật lại auto reconnect khi rời Setup. Log chỉ in trạng thái và số mạng, không in SSID/mật khẩu. Cả hai board biên dịch đạt; đã nạp CAM COM8 và xác minh hash. Đang chờ kết quả quét từ người dùng để xác nhận trên phần cứng.

## Kết quả đã xác minh

- `python -m pytest tests -q -p no:cacheprovider`: 28 passed, 1 cảnh báo python_multipart.
- `python firmware/sync_common.py --check`: cả hai bản SmartProvision.h khớp bản gốc.
- Backend :8000 và :9000 trả HTTP 200 cho OpenAPI. API trạng thái biên báo MQTT connected, liên kết trung tâm hoạt động, outbox 0 và mDNS đã quảng bá địa chỉ LAN hiện tại.
- Client Zeroconf riêng đã tìm được `exam-backend.local.:8000` ở địa chỉ `192.168.1.11` qua `_smart-exam._tcp.local.`; địa chỉ này là kết quả dò mạng tại thời điểm test, không được ghi vào firmware.
- Đã chạy thiết bị giả qua HTTP và Mosquitto thật, trong hai phòng tạm có tiền tố LIFE_/MOVE_: báo danh → đăng ký → gán phòng → MQTT online → lệnh display có ACK → cấp lại khoá → đổi phòng → tạm khoá/mở khoá → giả lập factory reset và duyệt ghép lại → thu hồi. Tất cả đạt.
- Đã kiểm tra broker từ chối mật khẩu cũ sau cấp lại khoá, đổi phòng và thu hồi; từ chối đăng nhập khi tạm khoá. Claim token cũ bị HTTP 401 sau khi chấp nhận ghép lại.
- Thiết bị giả đã được xoá khỏi biên sau test. Audit và bản phản chiếu trạng thái thu hồi ở trung tâm có thể còn để lưu lịch sử. Không thao tác thiết bị hoặc ca thi A101.
- ESP8266 COM8: biên dịch và nạp thành công, hash flash được xác minh. Serial xác nhận firmware 2.0.0, LCD địa chỉ 0x27 và mở AP `SMART-ACTION-F02B2E`, đang chờ cấu hình Wi-Fi. Chưa kiểm tra LCD hiển thị/còi bằng quan sát vật lý.
- ESP32-CAM: biên dịch thành công (flash 36%, RAM 19%). Nạp COM9 thất bại khi bắt tay: `No serial data received`; chưa ghi firmware mới vào board. Cần nối IO0 xuống GND, nhấn RESET rồi thử nạp lại; sau khi nạp phải bỏ nối IO0–GND và RESET để chạy.

## Đổi mạng và mật khẩu thiết bị

Firmware mới không chứa SSID, mật khẩu Wi-Fi hay IP LAN server cố định. Thiết bị tìm server qua mDNS, dự phòng hostname `exam-backend.local`. Địa chỉ `192.168.4.1` chỉ là địa chỉ trang Setup trên mạng AP của thiết bị.

1. Khi board đang chạy, giữ nút Setup từ 5 giây đến dưới 15 giây rồi thả: xoá cấu hình Wi-Fi, giữ danh tính và khoá thiết bị.
2. Kết nối AP `SMART-ACTION-...` hoặc `SMART-CAM-...`, mật khẩu `smartsetup`; mở `http://192.168.4.1`, nhập mạng Wi-Fi 2.4 GHz và mật khẩu mới.
3. Giữ nút từ 15 giây trở lên rồi thả để factory reset: xoá Wi-Fi và danh tính. Cấu hình lại mạng, sau đó quản trị viên đối chiếu mã ghép và chấp nhận yêu cầu ghép lại trên bảng điều khiển biên.

ESP8266 dùng nút FLASH (GPIO0), chỉ nhấn sau khi khởi động. ESP32-CAM cần nút nối GPIO12 xuống GND; nút RST chỉ khởi động lại, không xoá cấu hình.

Mật khẩu MQTT do server cấp: dùng chức năng **Cấp lại khoá**, thiết bị tự nhận mật khẩu mới qua claim token. Không chép khoá vào firmware.

Mật khẩu AP Setup hiện là hằng số `SETUP_AP_PASSWORD` (`smartsetup`): reset không đổi mật khẩu này. Muốn thay phải sửa firmware và nạp lại; chưa có chức năng đổi mật khẩu AP lúc chạy.

## Mật khẩu quản trị

- Bảng điều khiển biên: đặt `EDGE_ADMIN_PASSWORD` trong `.env` ở gốc dự án, khởi động lại backend biên. Đổi thêm `EDGE_JWT_SECRET` nếu cần vô hiệu hoá phiên đăng nhập cũ.
- Tài khoản trung tâm còn nhớ mật khẩu: `PATCH /api/v1/users/me/password`, với `old_password` và `new_password` (ít nhất 8 ký tự), dùng Bearer token.
- Quên mật khẩu tài khoản trung tâm: quản trị viên khác có thể dùng `PATCH /api/v1/users/{user_id}` với trường `password`. Chưa thấy luồng tự khôi phục qua email/OTP trong các router hiện tại.

## Giới hạn

Test thiết bị giả xác minh API và broker thật, chưa chứng minh firmware tự phục hồi trên phần cứng khi đổi Wi-Fi/IP hoặc giữ nút reset. Cần cấu hình Wi-Fi qua Setup và thao tác nút trên board để hoàn tất kiểm tra vật lý. Không sử dụng firmware cũ trong THIET_BI có secrets.h.
