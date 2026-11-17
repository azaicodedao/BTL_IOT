# ESP32-CAM AI-Thinker + DHT11

| Thiết bị | Chân ESP32-CAM |
|---|---|
| DHT11 DATA | GPIO13 |
| DHT11 VCC | 5V |
| DHT11 GND | GND |
| Nút Setup | GPIO12 nối GND khi nhấn |

GPIO12 dùng pull-up nội. Không kéo chân này lên 3.3 V bằng điện trở ngoài vì đây là chân strapping liên quan điện áp flash.

Khi nạp firmware, nối IO0 xuống GND, reset và nạp; sau khi hoàn tất phải tháo IO0 khỏi GND rồi reset để chạy.
