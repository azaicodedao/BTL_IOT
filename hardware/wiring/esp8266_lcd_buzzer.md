# ESP8266 + LCD 1602 I2C + buzzer

| Thiết bị | Chân NodeMCU |
|---|---|
| LCD SDA | D2 / GPIO4 |
| LCD SCL | D1 / GPIO5 |
| LCD VCC | VBUS 5V |
| LCD GND | GND |
| Buzzer dương | D5 / GPIO14 |
| Buzzer âm | GND |
| Nút Setup | FLASH / GPIO0 |

Firmware mặc định dùng buzzer active-high. Nếu dùng buzzer passive, đổi `BUZZER_PASSIVE` trong sketch theo ghi chú của firmware.
