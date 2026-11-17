/*
 * NODE A - CẢM BIẾN: ESP32-CAM (AI-Thinker) + DHT11
 * ---------------------------------------------------------------
 * Firmware "nạp 1 lần": KHÔNG chứa Wi-Fi, địa chỉ server hay khoá (không còn secrets.h).
 * Wi-Fi nhập qua mạng Setup "SMART-CAM-AABBCC"; device_id, phòng, khoá MQTT, địa chỉ + token gửi ảnh do server biên
 * cấp tự động sau khi quản trị viên duyệt trên bảng điều khiển biên (xem SmartProvision.h và firmware/README.md).
 *
 *  - DHT11 DATA -> GPIO13, VCC -> 5V, GND -> GND
 *  - Nút Setup: GPIO12 nối GND qua nút nhấn (giữ 5-15s xoá Wi-Fi, >= 15s factory reset).
 *    KHÔNG kéo GPIO12 lên 3.3V bằng điện trở ngoài (chân strapping điện áp flash) - chỉ dùng pull-up nội.
 *  - MQTT (QoS 1) topic classroom/{room}/{device}/...
 *      publish : telemetry (30s), heartbeat (15s), status (retain + Last Will), event
 *      subscribe: config (retain) -> camera_mode idle | preview | checkin | exam, chu kỳ gửi
 *  - Ảnh: HTTP POST <frames_url được cấp>, Authorization: Bearer <token>, JPEG <= 100 KB. Ảnh chỉ tới server biên.
 *  - Tự kết nối lại MQTT với backoff 1s -> 30s; telemetry khi mất mạng giữ tối đa 20 bản, gửi bù
 *
 * Board: "AI Thinker ESP32-CAM" (esp32:esp32:esp32cam). Thư viện: MQTT (256dpi), ArduinoJson 7, DHT sensor library
 */

#define FW_VERSION   "2.0.0"
#define HW_VERSION   "ESP32-CAM AI-Thinker + DHT11"

// ---- cấu hình SmartProvision ----
#define PROV_DEVICE_TYPE    "esp32_cam"
#define PROV_AP_PREFIX      "SMART-CAM-"
#define PROV_FACTORY_PREFIX "ESP32CAM-"
#define PROV_BUTTON_PIN     12         // nút Setup (đổi được nếu đấu chân khác)
#define PROV_LED_PIN        33         // LED đỏ trên board, mức thấp = sáng
#define PROV_LED_ACTIVE_LOW true
#include "SmartProvision.h"

#include <MQTT.h>
#include <DHT.h>
#include <time.h>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ---------------- chân ----------------
#define DHT_PIN        13
#define DHT_TYPE       DHT11
#define FLASH_LED_PIN   4

// Camera AI-Thinker
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

#define FRAME_MAX_BYTES (100 * 1024)
#define QUEUE_MAX 20
#define FRAME_REJECT_MAX 5             // ảnh bị 401/403 liên tiếp -> token đã bị thu hồi, xin lại khoá

// ---------------- trạng thái ----------------
WiFiClient net;          // socket MQTT
WiFiClient httpNet;      // socket riêng cho HTTP gửi ảnh (dùng chung sẽ làm đứt MQTT -> Last Will)
MQTTClient mqtt(1024);
DHT dht(DHT_PIN, DHT_TYPE);

String topicBase;                      // "classroom/<phòng>/<device>/" - lấy từ khoá được cấp
uint32_t telemetryIntervalMs = 30000;
uint32_t heartbeatIntervalMs = 15000;
uint32_t frameIntervalMs = 200;        // ~5 khung/giây: camera realtime trên giao diện
String cameraMode = "idle";            // idle | preview | checkin | exam (khác idle = phát hình)
HTTPClient http;                       // dùng lại kết nối TCP (keep-alive) giữa các khung

char bootId[5];
uint32_t msgSeq = 0;
uint32_t framesSent = 0, framesFailed = 0;
int frameRejects = 0;
int sensorErrors = 0;
bool cameraOk = false;
int jpegQuality = 12;

String queue_[QUEUE_MAX];              // telemetry chờ gửi bù khi mất MQTT
int qHead = 0, qCount = 0;

// MQTT: backoff 1s -> 30s (mục 8.3). Wi-Fi do SmartProvision lo.
uint32_t mqttBackoff = 1000, nextMqttTry = 0;

// ================================================================ tiện ích
bool timeSynced() { return time(nullptr) > 1700000000; }

String isoNow() {
  if (!timeSynced()) return "";                 // server sẽ gán thời gian nhận (mục 8.2)
  time_t now = time(nullptr);
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

String nextMsgId() {
  char buf[24];
  snprintf(buf, sizeof(buf), "%s-%06lu", bootId, (unsigned long)++msgSeq);
  return String(buf);
}

String t(const char* kind) { return topicBase + kind; }

bool publishJson(const char* kind, JsonDocument& doc, bool retained = false, int qos = 1) {
  String out;
  serializeJson(doc, out);
  return mqtt.connected() && mqtt.publish(t(kind), out, retained, qos);
}

void sendEvent(const char* type, const char* detail = nullptr, int count = 0) {
  JsonDocument doc;
  doc["msg_id"] = nextMsgId();
  doc["device_id"] = prov.deviceId;
  doc["ts"] = isoNow();
  doc["type"] = type;
  if (detail) doc["detail"] = detail;
  if (count) doc["count"] = count;
  publishJson("event", doc);
}

// ================================================================ camera
bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM; c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = psramFound() ? FRAMESIZE_VGA : FRAMESIZE_QVGA;     // mục 4.3: QVGA/VGA nén JPEG
  c.jpeg_quality = jpegQuality;
  c.fb_count = psramFound() ? 2 : 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[CAM] init loi 0x%x\n", err);
    return false;
  }
  return true;
}

void sendFrame() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    framesFailed++;
    sendEvent("camera_error", "fb_get");
    return;
  }
  // Giữ dưới 100 KB: ảnh quá lớn thì giảm chất lượng cho lần sau
  if (fb->len > FRAME_MAX_BYTES) {
    esp_camera_fb_return(fb);
    jpegQuality = min(jpegQuality + 5, 40);
    esp_camera_sensor_get()->set_quality(esp_camera_sensor_get(), jpegQuality);
    Serial.printf("[CAM] anh qua lon, giam chat luong -> %d\n", jpegQuality);
    return;
  }

  http.setReuse(true);                 // giữ kết nối: ~5 khung/giây không phải bắt tay TCP lại mỗi ảnh
  http.setTimeout(3000);
  http.begin(httpNet, prov.framesUrl);
  http.addHeader("Authorization", String("Bearer ") + prov.httpToken);
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(fb->buf, fb->len);
  esp_camera_fb_return(fb);
  http.end();

  if (code == 202 || code == 200) {
    framesSent++;                        // không log mỗi khung (5 khung/giây)
    frameRejects = 0;
  } else {
    framesFailed++;
    Serial.printf("[CAM] gui anh loi -> %d\n", code);
    if (code == 401 || code == 403) {
      sendEvent("frame_rejected", String(code).c_str());
      if (++frameRejects >= FRAME_REJECT_MAX) {        // token gửi ảnh không còn hiệu lực -> xin lại khoá
        frameRejects = 0;
        provRequestReprovision();
      }
    }
  }
}

// ================================================================ telemetry
void enqueue(const String& msg) {
  int idx = (qHead + qCount) % QUEUE_MAX;
  if (qCount == QUEUE_MAX) qHead = (qHead + 1) % QUEUE_MAX;     // đầy: bỏ bản cũ nhất
  else qCount++;
  queue_[idx] = msg;
}

void flushQueue() {
  while (qCount > 0 && mqtt.connected()) {
    if (!mqtt.publish(t("telemetry"), queue_[qHead], false, 1)) return;
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
  }
}

// DHT11 cần >= 1s giữa 2 lần đọc và đôi khi lỡ 1 lần do ngắt Wi-Fi/camera:
// thử tối đa 3 lần cách nhau 2.5s trong 1 chu kỳ, cả 3 lần hỏng mới báo sensor_error.
int dhtAttempt = 0;
uint32_t dhtRetryAt = 0;

void sendTelemetry(float c, float h) {
  JsonDocument doc;
  doc["msg_id"] = nextMsgId();
  doc["device_id"] = prov.deviceId;
  doc["ts"] = isoNow();
  doc["temperature_c"] = round(c * 10) / 10.0;
  doc["humidity_pct"] = round(h * 10) / 10.0;
  doc["fw"] = FW_VERSION;
  String out;
  serializeJson(doc, out);
  Serial.printf("[DHT] %.1f C  %.0f %%\n", c, h);
  if (!(mqtt.connected() && mqtt.publish(t("telemetry"), out, false, 1))) enqueue(out);
}

// return true khi chu kỳ đọc kết thúc (thành công hoặc hết lượt thử)
bool readAndSendTelemetry() {
  float h = dht.readHumidity();
  float c = dht.readTemperature();
  if (!isnan(h) && !isnan(c)) {
    if (sensorErrors > 0) sendEvent("sensor_ok", "DHT11");
    sensorErrors = 0;
    dhtAttempt = 0;
    sendTelemetry(c, h);
    return true;
  }
  if (++dhtAttempt < 3) {
    dhtRetryAt = millis() + 2500;
    return false;
  }
  dhtAttempt = 0;
  sensorErrors++;
  Serial.printf("[DHT] loi doc 3 lan (chu ky loi thu %d) - kiem tra DATA=GPIO13, VCC=5V\n", sensorErrors);
  sendEvent("sensor_error", "DHT11", sensorErrors);
  return true;
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["device_id"] = prov.deviceId;
  doc["ts"] = isoNow();
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["heap"] = ESP.getFreeHeap();
  doc["fw"] = FW_VERSION;
  doc["camera_mode"] = cameraMode;
  doc["frames_sent"] = framesSent;
  doc["frames_failed"] = framesFailed;
  doc["queued"] = qCount;
  publishJson("heartbeat", doc, false, 0);
}

// ================================================================ MQTT
void onMessage(String& topic, String& payload) {
  if (!topic.endsWith("/config")) return;
  JsonDocument doc;
  if (deserializeJson(doc, payload)) return;            // gồm cả bản tin retain rỗng (server dọn topic cũ)
  if (doc["telemetry_interval_s"].is<int>()) telemetryIntervalMs = max(5, doc["telemetry_interval_s"].as<int>()) * 1000UL;
  if (doc["heartbeat_interval_s"].is<int>()) heartbeatIntervalMs = max(5, doc["heartbeat_interval_s"].as<int>()) * 1000UL;
  if (!doc["frame_interval_s"].isNull()) frameIntervalMs = max(0.1f, doc["frame_interval_s"].as<float>()) * 1000UL;
  if (doc["camera_mode"].is<const char*>()) {
    String m = doc["camera_mode"].as<String>();
    if (m == "idle" || m == "preview" || m == "checkin" || m == "exam") cameraMode = m;
  }
  Serial.printf("[CFG] mode=%s telemetry=%lus heartbeat=%lus frame=%lums\n", cameraMode.c_str(),
                (unsigned long)(telemetryIntervalMs / 1000), (unsigned long)(heartbeatIntervalMs / 1000),
                (unsigned long)frameIntervalMs);
}

// Gọi khi SmartProvision vừa nhận khoá / topic mới (lần đầu, đổi phòng, cấp lại khoá)
void applyIdentity() {
  if (mqtt.connected()) mqtt.disconnect();
  topicBase = prov.topicBase + "/";
  cameraMode = "idle";                                   // chờ config retain của phòng mới
  frameRejects = 0;
  mqtt.setHost(prov.mqttHost.c_str(), prov.mqttPort);    // thư viện giữ con trỏ -> gọi lại mỗi khi chuỗi đổi
  mqttBackoff = 1000;
  nextMqttTry = 0;
}

bool connectMqtt() {
  String willTopic = t("status");
  mqtt.setWill(willTopic.c_str(), "{\"state\":\"offline\"}", true, 1);     // Last Will (mục 8.3)
  if (!mqtt.connect(prov.deviceId.c_str(), prov.mqttUser.c_str(), prov.mqttPass.c_str())) {
    Serial.printf("[MQTT] ket noi that bai, loi %d / rc %d\n", mqtt.lastError(), mqtt.returnCode());
    return false;
  }
  mqtt.subscribe(t("config"), 1);
  JsonDocument doc;
  doc["state"] = "online";
  doc["fw"] = FW_VERSION;
  doc["hw"] = HW_VERSION;
  doc["ts"] = isoNow();
  publishJson("status", doc, true, 1);
  Serial.printf("[MQTT] da ket noi %s voi ten %s\n", prov.mqttHost.c_str(), prov.deviceId.c_str());
  flushQueue();
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
    if (!cameraOk) sendEvent("camera_error", "init");
  } else {
    provMqttFailed();                 // 3 lần liên tiếp -> SmartProvision xin lại khoá từ server biên
    nextMqttTry = now + mqttBackoff;
    mqttBackoff = min(mqttBackoff * 2, 30000UL);
  }
}

// ================================================================ setup / loop
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);      // tránh reset do sụt áp khi chụp + phát Wi-Fi
  Serial.begin(115200);
  Serial.println("\n=== NODE A: ESP32-CAM + DHT11 (" FW_VERSION ") ===");
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  snprintf(bootId, sizeof(bootId), "%04x", (unsigned)(esp_random() & 0xFFFF));

  cameraOk = initCamera();
  dht.begin();

  mqtt.begin(net);
  mqtt.setKeepAlive(30);
  mqtt.setCleanSession(false);                             // giữ subscription QoS 1 khi kết nối lại
  mqtt.onMessage(onMessage);

  provOnProvisioned = applyIdentity;
  provBegin();                                             // nạp Wi-Fi + danh tính từ flash, mở Setup nếu cần
  WiFi.setSleep(false);
  if (prov.provisioned) applyIdentity();
  configTime(0, 0, "pool.ntp.org", "time.google.com");     // UTC (mục 8.2)
}

void loop() {
  provLoop();
  maintainMqtt();
  mqtt.loop();
  if (!provReady()) {                                      // đang Setup / chờ duyệt: chưa có danh tính để gửi gì
    delay(5);
    return;
  }

  uint32_t now = millis();
  static uint32_t lastTelemetry = 0, lastHeartbeat = 0, lastFrame = 0;
  static bool first = true;

  if (dhtAttempt > 0) {                                    // đang thử lại trong chu kỳ hiện tại
    if ((int32_t)(now - dhtRetryAt) >= 0) readAndSendTelemetry();
  } else if (first || now - lastTelemetry >= telemetryIntervalMs) {
    if (now > 3000) {                                      // DHT11 cần ~1-2s sau khi cấp nguồn
      lastTelemetry = now;
      first = false;
      readAndSendTelemetry();
    }
  }
  if (mqtt.connected() && now - lastHeartbeat >= heartbeatIntervalMs) {
    lastHeartbeat = now;
    sendHeartbeat();
    flushQueue();
  }
  if (cameraOk && cameraMode != "idle" && prov.framesUrl.length() && now - lastFrame >= frameIntervalMs) {
    lastFrame = now;
    sendFrame();
  }
  delay(10);
}
