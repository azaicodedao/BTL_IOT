/*
 * NODE B - CHẤP HÀNH: ESP8266 (ESP-12E / NodeMCU) + LCD 1602 I2C + còi chíp
 * ---------------------------------------------------------------
 * Firmware "nạp 1 lần": KHÔNG chứa Wi-Fi, địa chỉ server hay khoá (không còn secrets.h).
 * Wi-Fi nhập qua mạng Setup "SMART-ACTION-AABBCC"; device_id, phòng, khoá MQTT do server biên cấp tự động
 * sau khi quản trị viên duyệt trên bảng điều khiển biên (xem SmartProvision.h và firmware/README.md).
 *
 *  - LCD SDA -> D2 (GPIO4), SCL -> D1 (GPIO5), VCC -> VBUS (5V), GND -> GND
 *  - Còi: chân dài (+) -> D5 (GPIO14), chân ngắn (-) -> GND
 *  - Nút Setup = nút FLASH trên board (GPIO0): giữ 5-15s xoá Wi-Fi, giữ >= 15s factory reset
 *  - MQTT topic classroom/{room}/{device}/...
 *      subscribe: cmd (QoS1)      {"cmd_id","action":"buzzer","pattern":"once|continuous|stop","expires_at"}
 *                                 {"cmd_id","action":"display","text":"..."}
 *                 display (retain) {"room","temp_c","hum_pct","status","advice"}
 *                 config (retain)
 *      publish  : cmd/ack (QoS1) {"cmd_id","result":"ok|expired|duplicate|error","ts"}
 *                 heartbeat, status (retain + Last Will), event
 *  - Loại lệnh trùng theo cmd_id, bỏ lệnh quá expires_at (không thực thi lệnh cũ sau khi kết nối lại)
 *
 * Board: "NodeMCU 1.0 (ESP-12E Module)" (esp8266:esp8266:nodemcuv2)
 * Thư viện: MQTT (256dpi), ArduinoJson 7, LiquidCrystal I2C
 */

#define FW_VERSION "2.0.0"
#define HW_VERSION "ESP8266 ESP-12E + LCD1602 I2C + buzzer"

// ---- cấu hình SmartProvision ----
#define PROV_DEVICE_TYPE    "esp8266_actuator"
#define PROV_AP_PREFIX      "SMART-ACTION-"
#define PROV_FACTORY_PREFIX "ESP8266-"
#define PROV_BUTTON_PIN     0          // nút FLASH
#define PROV_LED_PIN        2          // LED trên module ESP-12E (D4), mức thấp = sáng
#define PROV_LED_ACTIVE_LOW true
#include "SmartProvision.h"

#include <MQTT.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <time.h>

#define PIN_SDA     4      // D2
#define PIN_SCL     5      // D1
#define PIN_BUZZER 14      // D5
#define BUZZER_ACTIVE_HIGH true
#define BUZZER_PASSIVE false   // còi thụ động (không tự kêu khi cấp nguồn) -> true để phát tần số 2.7 kHz

WiFiClient net;
MQTTClient mqtt(1024);
LiquidCrystal_I2C* lcd = nullptr;

String topicBase;                      // "classroom/<phòng>/<device>/" - lấy từ khoá được cấp
uint32_t heartbeatIntervalMs = 15000;
char bootId[5];
uint32_t msgSeq = 0;

// ---- còi ----
enum BuzzMode { BUZZ_OFF, BUZZ_ONCE, BUZZ_CONTINUOUS };
BuzzMode buzzMode = BUZZ_OFF;
uint32_t buzzStart = 0;

// ---- hiển thị ----
String roomCode;
float tempC = NAN, humPct = NAN;
String envStatus = "", advice = "";
String overlayText = "";
uint32_t overlayUntil = 0;
uint32_t lastDisplayMsg = 0;

// ---- chống lặp lệnh ----
#define RECENT_CMDS 8
String recentCmds[RECENT_CMDS];
int recentIdx = 0;

// MQTT: backoff 1s -> 30s (mục 8.3). Wi-Fi do SmartProvision lo.
uint32_t mqttBackoff = 1000, nextMqttTry = 0;

// ================================================================ tiện ích
bool timeSynced() { return time(nullptr) > 1700000000; }

String isoNow() {
  if (!timeSynced()) return "";
  time_t now = time(nullptr);
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

// "2026-11-10T07:31:00Z" -> epoch (UTC). 0 nếu sai định dạng.
time_t parseIsoUtc(const char* s) {
  if (!s) return 0;
  struct tm t = {};
  if (sscanf(s, "%d-%d-%dT%d:%d:%d", &t.tm_year, &t.tm_mon, &t.tm_mday, &t.tm_hour, &t.tm_min, &t.tm_sec) != 6)
    return 0;
  t.tm_year -= 1900;
  t.tm_mon -= 1;
  return mktime(&t);              // TZ = UTC (configTime(0, 0, ...))
}

String t(const char* kind) { return topicBase + kind; }

bool publishJson(const char* kind, JsonDocument& doc, bool retained = false, int qos = 1) {
  String out;
  serializeJson(doc, out);
  return mqtt.connected() && mqtt.publish(t(kind), out, retained, qos);
}

void sendEvent(const char* type, const char* detail = nullptr) {
  JsonDocument doc;
  char id[24];
  snprintf(id, sizeof(id), "%s-%06lu", bootId, (unsigned long)++msgSeq);
  doc["msg_id"] = id;
  doc["device_id"] = prov.deviceId;
  doc["ts"] = isoNow();
  doc["type"] = type;
  if (detail) doc["detail"] = detail;
  publishJson("event", doc);
}

// ================================================================ còi
void buzzer(bool on) {
#if BUZZER_PASSIVE
  if (on) tone(PIN_BUZZER, 2700); else noTone(PIN_BUZZER);
#else
  digitalWrite(PIN_BUZZER, (on == BUZZER_ACTIVE_HIGH) ? HIGH : LOW);
#endif
}

void updateBuzzer() {
  uint32_t el = millis() - buzzStart;
  switch (buzzMode) {
    case BUZZ_ONCE:                       // "chíp" 1 lần: 150 ms
      buzzer(el < 150);
      if (el >= 150) buzzMode = BUZZ_OFF;
      break;
    case BUZZ_CONTINUOUS:                 // chíp liên tục: 300 ms kêu / 200 ms nghỉ đến khi có lệnh stop
      buzzer((el % 500) < 300);
      break;
    default:
      buzzer(false);
  }
}

// ================================================================ LCD
void lcdLine(uint8_t row, const String& text) {
  if (!lcd) return;
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", text.c_str());
  lcd->setCursor(0, row);
  lcd->print(buf);
}

// Chưa sẵn sàng (Setup / chờ duyệt...): hướng dẫn người lắp đặt ngay trên LCD
void renderSetupLcd() {
  switch (provStage()) {
    case PROV_ST_SETUP:      lcdLine(0, "Cai dat Wi-Fi:");    lcdLine(1, provApName().substring(0, 16)); break;
    case PROV_ST_WIFI:       lcdLine(0, "Dang vao Wi-Fi..");  lcdLine(1, ""); break;
    case PROV_ST_FIND_EDGE:  lcdLine(0, "Tim server bien");   lcdLine(1, ""); break;
    case PROV_ST_NO_EDGE:    lcdLine(0, "Khong thay");        lcdLine(1, "server bien"); break;
    case PROV_ST_PENDING:    lcdLine(0, "Cho duyet tren");    lcdLine(1, "Web. Ma: " + provPairCode()); break;
    case PROV_ST_REGISTERED: lcdLine(0, "Da dang ky");        lcdLine(1, "Cho gan phong"); break;
    case PROV_ST_SUSPENDED:  lcdLine(0, "Thiet bi dang");     lcdLine(1, "bi tam khoa"); break;
    case PROV_ST_REJECTED:   lcdLine(0, "Bi tu choi /");      lcdLine(1, "da thu hoi"); break;
    default: break;
  }
}

void renderLcd() {
  if (!provReady()) { renderSetupLcd(); return; }

  // Dòng 1: phòng + nhiệt độ + độ ẩm
  if (!isnan(tempC)) {
    char l1[24];
    snprintf(l1, sizeof(l1), "%-4.4s %4.1f%cC %3.0f%%", roomCode.c_str(), tempC, (char)0xDF, humPct);
    lcdLine(0, l1);
  } else {
    lcdLine(0, roomCode + " cho du lieu");
  }

  // Dòng 2: cảnh báo còi > tin nhắn > khuyến nghị > trạng thái kết nối
  static uint8_t scroll = 0;
  String line2;
  if (buzzMode == BUZZ_CONTINUOUS) line2 = (millis() / 500) % 2 ? "!! CANH BAO !!" : "";
  else if (overlayText.length() && millis() < overlayUntil) line2 = overlayText;
  else if (advice.length()) line2 = advice;
  else if (!mqtt.connected()) line2 = "Mat ket noi MQTT";
  else if (millis() - lastDisplayMsg > 120000 && lastDisplayMsg) line2 = "Cho cam bien...";
  else line2 = "Dieu kien tot";

  if (line2.length() > 16) {
    String s = line2 + "   " + line2;
    lcdLine(1, s.substring(scroll, scroll + 16));
    scroll = (scroll + 1) % (line2.length() + 3);
  } else {
    scroll = 0;
    lcdLine(1, line2);
  }
}

// ================================================================ lệnh
void sendAck(const char* cmdId, const char* result) {
  JsonDocument doc;
  doc["cmd_id"] = cmdId;
  doc["result"] = result;
  doc["ts"] = isoNow();
  publishJson("cmd/ack", doc, false, 1);
  Serial.printf("[CMD] %s -> %s\n", cmdId, result);
}

bool seenCmd(const String& id) {
  for (auto& c : recentCmds) if (c == id) return true;
  recentCmds[recentIdx] = id;
  recentIdx = (recentIdx + 1) % RECENT_CMDS;
  return false;
}

void handleCmd(JsonDocument& doc) {
  const char* cmdId = doc["cmd_id"] | "";
  if (!*cmdId) return;
  if (seenCmd(cmdId)) {               // QoS 1 có thể gửi lại: không thực thi lần 2 nhưng vẫn ACK
    sendAck(cmdId, "duplicate");
    return;
  }
  time_t exp = parseIsoUtc(doc["expires_at"] | (const char*)nullptr);
  if (exp && timeSynced() && time(nullptr) > exp) {
    sendAck(cmdId, "expired");
    return;
  }

  String action = doc["action"] | "";
  if (action == "buzzer") {
    String p = doc["pattern"] | "";
    if (p == "once") { buzzMode = BUZZ_ONCE; buzzStart = millis(); }
    else if (p == "continuous") { buzzMode = BUZZ_CONTINUOUS; buzzStart = millis(); }
    else if (p == "stop") { buzzMode = BUZZ_OFF; }
    else { sendAck(cmdId, "error:pattern"); return; }
    updateBuzzer();
    sendAck(cmdId, "ok");
  } else if (action == "display") {
    overlayText = String(doc["text"] | "").substring(0, 32);
    overlayUntil = millis() + 15000;
    renderLcd();
    sendAck(cmdId, "ok");
  } else {
    sendAck(cmdId, "error:action");
  }
}

void handleDisplay(JsonDocument& doc) {
  if (doc["room"].is<const char*>()) roomCode = doc["room"].as<String>();
  if (!doc["temp_c"].isNull()) tempC = doc["temp_c"].as<float>();
  if (!doc["hum_pct"].isNull()) humPct = doc["hum_pct"].as<float>();
  envStatus = doc["status"] | "";
  advice = doc["advice"] | "";
  lastDisplayMsg = millis();
  renderLcd();
}

void onMessage(String& topic, String& payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload)) return;            // gồm cả bản tin retain rỗng (server dọn topic cũ)
  if (topic.endsWith("/cmd")) handleCmd(doc);
  else if (topic.endsWith("/display")) handleDisplay(doc);
  else if (topic.endsWith("/config") && doc["heartbeat_interval_s"].is<int>())
    heartbeatIntervalMs = max(5, doc["heartbeat_interval_s"].as<int>()) * 1000UL;
}

// ================================================================ kết nối
// Gọi khi SmartProvision vừa nhận khoá / topic mới (lần đầu, đổi phòng, cấp lại khoá)
void applyIdentity() {
  if (mqtt.connected()) mqtt.disconnect();
  topicBase = prov.topicBase + "/";
  roomCode = prov.room;
  tempC = humPct = NAN;
  advice = "";
  mqtt.setHost(prov.mqttHost.c_str(), prov.mqttPort);    // thư viện giữ con trỏ -> gọi lại mỗi khi chuỗi đổi
  mqttBackoff = 1000;
  nextMqttTry = 0;
}

bool connectMqtt() {
  String willTopic = t("status");
  mqtt.setWill(willTopic.c_str(), "{\"state\":\"offline\"}", true, 1);
  if (!mqtt.connect(prov.deviceId.c_str(), prov.mqttUser.c_str(), prov.mqttPass.c_str())) {
    Serial.printf("[MQTT] that bai, loi %d / rc %d\n", mqtt.lastError(), mqtt.returnCode());
    return false;
  }
  mqtt.subscribe(t("cmd"), 1);
  mqtt.subscribe(t("display"), 0);
  mqtt.subscribe(t("config"), 1);
  JsonDocument doc;
  doc["state"] = "online";
  doc["fw"] = FW_VERSION;
  doc["hw"] = HW_VERSION;
  doc["ts"] = isoNow();
  publishJson("status", doc, true, 1);
  Serial.printf("[MQTT] da ket noi %s voi ten %s\n", prov.mqttHost.c_str(), prov.deviceId.c_str());
  return true;
}

void maintainMqtt() {
  if (!provReady() || mqtt.connected()) return;
  uint32_t now = millis();
  if ((int32_t)(now - nextMqttTry) < 0) return;
  if (connectMqtt()) {
    mqttBackoff = 1000;
    provMqttOk();
    sendEvent("connected");
  } else {
    provMqttFailed();                 // 3 lần liên tiếp -> SmartProvision xin lại khoá từ server biên
    nextMqttTry = now + mqttBackoff;
    mqttBackoff = min(mqttBackoff * 2, (uint32_t)30000);
  }
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["device_id"] = prov.deviceId;
  doc["ts"] = isoNow();
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["heap"] = ESP.getFreeHeap();
  doc["fw"] = FW_VERSION;
  doc["buzzer"] = buzzMode == BUZZ_CONTINUOUS ? "continuous" : "off";
  doc["lcd"] = lcd != nullptr;
  publishJson("heartbeat", doc, false, 0);
}

uint8_t findLcd() {
  for (uint8_t a : {0x27, 0x3F, 0x20, 0x38}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) return a;
  }
  return 0;
}

// ================================================================ setup / loop
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== NODE B: ESP8266 + LCD1602 + buzzer (" FW_VERSION ") ===");
  pinMode(PIN_BUZZER, OUTPUT);
  buzzer(false);

  Wire.begin(PIN_SDA, PIN_SCL);
  uint8_t addr = findLcd();
  if (addr) {
    lcd = new LiquidCrystal_I2C(addr, 16, 2);
    lcd->init();
    lcd->backlight();
    Serial.printf("[LCD] dia chi 0x%02X\n", addr);
  } else {
    Serial.println("[LCD] KHONG tim thay LCD I2C - kiem tra SDA=D2, SCL=D1, VCC=VBUS");
  }
  lcdLine(0, "Phong hoc IoT");
  lcdLine(1, "FW " FW_VERSION);

  snprintf(bootId, sizeof(bootId), "%04x", (unsigned)(ESP.random() & 0xFFFF));

  mqtt.begin(net);
  mqtt.setKeepAlive(30);
  mqtt.setCleanSession(false);
  mqtt.onMessage(onMessage);

  provOnProvisioned = applyIdentity;
  provBegin();                                   // nạp Wi-Fi + danh tính từ flash, mở Setup nếu cần
  if (prov.provisioned) applyIdentity();
  configTime(0, 0, "pool.ntp.org", "time.google.com");

  // Chíp 1 lần khi khởi động để kiểm tra còi
  buzzMode = BUZZ_ONCE;
  buzzStart = millis();
}

void loop() {
  provLoop();
  maintainMqtt();
  mqtt.loop();
  updateBuzzer();

  uint32_t now = millis();
  static uint32_t lastLcd = 0, lastHeartbeat = 0;
  if (now - lastLcd >= 400) {
    lastLcd = now;
    renderLcd();
  }
  if (mqtt.connected() && now - lastHeartbeat >= heartbeatIntervalMs) {
    lastHeartbeat = now;
    sendHeartbeat();
  }
  delay(5);
}
