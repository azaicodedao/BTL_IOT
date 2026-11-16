# Firmware thiết bị — nạp 1 lần, tự đăng ký

Firmware **không chứa** tên Wi-Fi, địa chỉ server hay khoá. Mọi thiết bị cùng loại nạp **cùng một bản**; sau đó
Wi-Fi nhập qua trang Setup, còn `device_id`, phòng, khoá MQTT, token gửi ảnh do **server biên** cấp tự động.
Đổi Wi-Fi, đổi phòng, cấp lại khoá, đổi IP server biên đều **không cần nạp lại**.

```
firmware/
├── common/SmartProvision.h   # BẢN GỐC: Wi-Fi Setup + mDNS + tự đăng ký + lưu flash (ESP32 & ESP8266)
├── sync_common.py            # chép bản gốc vào 2 thư mục sketch (Arduino bắt buộc file nằm cạnh .ino)
├── node_a_cam/               # ESP32-CAM AI-Thinker + DHT11  (camera + nhiệt độ/độ ẩm)
└── node_b_lcd/               # ESP8266 NodeMCU + LCD1602 I2C + còi
```

Sửa `SmartProvision.h` thì chỉ sửa trong `common/` rồi chạy `python firmware/sync_common.py`
(`--check` để kiểm tra hai bản sao có lệch không).

## 1. Chuẩn bị (Arduino IDE 2.x hoặc arduino-cli)

| | Node A – camera | Node B – chấp hành |
|---|---|---|
| Board | **AI Thinker ESP32-CAM** (`esp32:esp32:esp32cam`, core 3.x) | **NodeMCU 1.0 (ESP-12E)** (`esp8266:esp8266:nodemcuv2`, core 3.1.x) |
| Thư viện | MQTT (256dpi), ArduinoJson 7, DHT sensor library, Adafruit Unified Sensor | MQTT (256dpi), ArduinoJson 7, LiquidCrystal I2C |
| Flash layout | mặc định | mặc định **4MB (FS:2MB)** — cần LittleFS để lưu cấu hình |

Sơ đồ chân:

| Node A (ESP32-CAM) | | Node B (ESP8266) | |
|---|---|---|---|
| DHT11 DATA | GPIO13 | LCD SDA | D2 (GPIO4) |
| DHT11 VCC / GND | 5V / GND | LCD SCL | D1 (GPIO5) |
| Nút Setup | GPIO12 ↔ GND | LCD VCC / GND | VBUS 5V / GND |
| LED trạng thái | LED đỏ trên board (GPIO33) | Còi (+) / (−) | D5 (GPIO14) / GND |
| Nguồn | 5V ≥ 500 mA | Nút Setup | nút **FLASH** có sẵn (GPIO0) |
| | | LED trạng thái | LED xanh trên module (GPIO2) |

> ESP32-CAM: GPIO12 là chân strapping (điện áp flash). Chỉ nối nút nhấn **xuống GND**, không gắn điện trở kéo lên 3.3V
> bên ngoài. Muốn dùng chân khác: đổi `#define PROV_BUTTON_PIN` đầu sketch.

## 2. Nạp firmware (chỉ 1 lần cho mỗi thiết bị)

```
arduino-cli compile --fqbn esp32:esp32:esp32cam      firmware/node_a_cam
arduino-cli upload  --fqbn esp32:esp32:esp32cam      -p COMx firmware/node_a_cam
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/node_b_lcd
arduino-cli upload  --fqbn esp8266:esp8266:nodemcuv2 -p COMy firmware/node_b_lcd
```
ESP32-CAM: nối **IO0 → GND**, nhấn RESET rồi Upload; xong bỏ IO0 khỏi GND, RESET lại.
Không có `secrets.h`, không cần sửa dòng nào trước khi nạp.

## 3. Quy trình lắp đặt cho nhân viên

1. Cắm nguồn. LED **nháy chậm** = thiết bị đang chờ cấu hình Wi-Fi.
2. Dùng điện thoại/laptop kết nối Wi-Fi `SMART-CAM-AABBCC` (camera) hoặc `SMART-ACTION-AABBCC` (LCD + còi);
   `AABBCC` là 6 ký tự cuối MAC. Mật khẩu mạng Setup: `smartsetup`.
3. Trang **CẤU HÌNH WI-FI** tự mở; nếu không, vào `http://192.168.4.1`. Chọn Wi-Fi nơi lắp.
4. Nhập mật khẩu Wi-Fi → **Kiểm tra kết nối** (tuỳ chọn) → **Lưu và kết nối**.
5. Chờ LED **sáng liên tục** (đã vào mạng). Mạng Setup tự tắt.
6. Mở bảng điều khiển server biên `http://<ip-server-biên>:8000/`.
7. Mục *Thiết bị chờ đăng ký*: đối chiếu **mã ghép 4 số** (node LCD hiện trên màn hình; camera in ra Serial) → **Đăng ký**.
8. **Gán phòng**. Thiết bị tự nhận khoá và kết nối MQTT trong vài giây.
9. Kiểm tra thiết bị chuyển *Active / online*.

Nhân viên chỉ nhập 2 thứ: SSID và mật khẩu Wi-Fi. Trang Setup không hiển thị và không cho nhập địa chỉ server,
MQTT, device id hay khoá.

## 4. Đèn LED trạng thái

| LED | Ý nghĩa |
|---|---|
| Nháy chậm (1 s sáng / 1 s tắt) | Đang ở chế độ Setup, chờ cấu hình Wi-Fi |
| Nháy nhanh | Đang kết nối Wi-Fi |
| Sáng liên tục | Đã vào mạng (đang chờ duyệt hoặc đang hoạt động) |
| 2 chớp ngắn, lặp lại mỗi 2 s | Không tìm thấy server biên |

Node LCD còn hiện trạng thái bằng chữ: `Cai dat Wi-Fi`, `Tim server bien`, `Cho duyet tren Web. Ma: 1234`,
`Da dang ky / Cho gan phong`, `tam khoa`, `Bi tu choi`.

## 5. Thiết bị tự xử lý những gì

| Tình huống | Thiết bị làm gì |
|---|---|
| Mất Wi-Fi > 30 s | Mở lại mạng Setup; nếu đã có Wi-Fi lưu thì vẫn thử lại mạng cũ mỗi 60 s (router vừa bật lại → tự vào) |
| Quản trị viên **đổi phòng / cấp lại khoá** | Broker ngắt phiên → MQTT hỏng 3 lần → hỏi server biên bằng claim token → nhận khoá + topic mới |
| **Đổi IP / thay máy** server biên | MQTT hỏng 3 lần → tìm lại bằng mDNS → nhận địa chỉ + khoá mới |
| Token gửi ảnh bị từ chối (401/403) 5 lần | Camera xin lại khoá như trên |
| Bị **tạm khoá** | Ngừng gửi, hỏi lại mỗi 30 s; được mở khoá thì nhận khoá mới và chạy tiếp |
| Bị **thu hồi / từ chối** | Hỏi lại mỗi 60 s; muốn dùng lại: xoá bản ghi trên bảng điều khiển biên rồi factory reset thiết bị |

## 6. Đổi Wi-Fi / factory reset (nút Setup)

| Giữ nút | Tác dụng |
|---|---|
| 5 – 15 giây rồi thả | **Chỉ xoá Wi-Fi.** Giữ nguyên device_id, khoá, claim token → nhập Wi-Fi mới là chạy lại với danh tính cũ |
| ≥ 15 giây rồi thả | **Factory reset**: xoá hết. Thiết bị báo danh lại; nếu trước đó đã đăng ký, bảng điều khiển biên hiện *xin ghép lại* để quản trị viên chấp nhận (đối chiếu mã ghép) |

ESP8266: đừng giữ nút FLASH **lúc cấp nguồn** (sẽ vào chế độ nạp); chỉ nhấn khi thiết bị đang chạy.

## 7. Xử lý sự cố

- **LED chớp 2 lần (không thấy server biên):** server biên phải cùng LAN và đang chạy
  (`python -m uvicorn backend.edge.app.main:app --host 0.0.0.0 --port 8000`, có gói `zeroconf`). Nếu Wi-Fi chặn multicast/mDNS
  (AP isolation, mạng trường): tạo bản ghi DNS (hoặc DHCP static host) `exam-backend.local` trỏ về IP server biên —
  firmware dùng hostname này cổng 8000 làm phương án dự phòng, sau đó nhớ IP lần liên lạc gần nhất.
- **Không thấy mạng `SMART-...`:** thiết bị đã có Wi-Fi và đang vào được mạng. Giữ nút Setup 5–15 s để nhập lại.
- **Trang Setup báo "Không thể kết nối":** sai mật khẩu, hoặc mạng 5 GHz (ESP chỉ hỗ trợ 2.4 GHz).
- **ESP32-CAM reset khi chụp:** nguồn yếu; dùng 5V ≥ 500 mA, dây ngắn.
- **Xem log:** Serial 115200. Log không in mật khẩu Wi-Fi, khoá MQTT hay token.

## 8. Giới hạn đã biết

- Kênh đăng ký và gửi ảnh dùng **HTTP không TLS**, MQTT không TLS: chỉ phù hợp trong LAN tin cậy của phòng học
  (phạm vi BTL). Khoá được truyền 1 lần trên LAN khi cấp; kẻ nghe lén cùng mạng có thể bắt được.
- Mật khẩu mạng Setup là hằng số `SETUP_AP_PASSWORD` (`smartsetup`) giống nhau cho mọi thiết bị; đổi trước khi biên dịch
  nếu triển khai thật. Trang Setup không có xác thực riêng ngoài mật khẩu AP.
- Khoá lưu trong flash không mã hoá (NVS của ESP32 / LittleFS của ESP8266). Ai lấy được thiết bị có thể đọc ra;
  khi mất thiết bị hãy *Thu hồi* trên bảng điều khiển biên.
- Trong lúc thiết bị hỏi server biên (HTTP, tối đa ~5 s mỗi lần) vòng lặp bị chặn; việc này chỉ xảy ra khi chưa có MQTT.
- Chưa thử trên phần cứng thật trong lần viết này: mã đã **biên dịch sạch** cho cả 2 board, chưa nạp và chạy thử.
- Chưa có OTA: nâng cấp firmware vẫn phải nạp qua cáp.
