/*
 * SmartProvision.h - Cấu hình Wi-Fi + tự đăng ký với server biên (zero-touch), dùng chung ESP32 / ESP8266.
 * -------------------------------------------------------------------------------------------------------
 * BẢN GỐC: firmware/common/SmartProvision.h. Arduino bắt buộc file nằm trong thư mục sketch, nên file này
 * được SAO CHÉP Y HỆT sang node_a_cam/ và node_b_lcd/. Chỉ sửa bản gốc rồi chạy:  python firmware/sync_common.py
 *
 * Firmware KHÔNG chứa Wi-Fi, địa chỉ server hay khoá. Nạp 1 lần, sau đó:
 *   1. Chưa có Wi-Fi (hoặc không vào được ~30s) -> mở SoftAP "SMART-CAM-AABBCC" / "SMART-ACTION-AABBCC",
 *      trang cấu hình ở http://192.168.4.1 chỉ hỏi tên mạng + mật khẩu.
 *   2. Vào Wi-Fi -> tự tìm server biên bằng mDNS (_smart-exam._tcp), dự phòng exam-backend.local:8000.
 *   3. POST /api/v1/device-enrollments/announce -> nhận claim token (giữ trong flash) + mã ghép.
 *   4. Hỏi định kỳ GET /api/v1/device-enrollments/<MAC>. Quản trị viên duyệt + gán phòng trên bảng điều khiển biên
 *      -> nhận device_id, khoá MQTT riêng, topic, token gửi ảnh -> lưu flash -> sketch kết nối MQTT.
 *   5. MQTT hỏng 3 lần liên tiếp (khoá bị thu hồi / đổi phòng / đổi địa chỉ server) -> tự quay lại bước 2-4
 *      bằng claim token để nhận khoá mới. Không cần nạp lại firmware.
 *   6. Nút Setup: giữ 5-15s = chỉ xoá Wi-Fi (giữ danh tính); giữ >= 15s = factory reset (phải đăng ký lại).
 *
 * LED: nháy chậm = chờ cấu hình Wi-Fi | nháy nhanh = đang kết nối Wi-Fi | sáng liên tục = đã vào mạng
 *      | 2 chớp ngắn lặp lại = không tìm thấy server biên.
 *
 * Sketch PHẢI #define trước khi include:
 *   FW_VERSION, PROV_DEVICE_TYPE ("esp32_cam"), PROV_AP_PREFIX ("SMART-CAM-"), PROV_FACTORY_PREFIX ("ESP32CAM-"),
 *   PROV_BUTTON_PIN, PROV_LED_PIN, PROV_LED_ACTIVE_LOW (true/false)
 * API cho sketch: provBegin(), provLoop(), provReady(), prov (danh tính), provStage(), provPairCode(), provApName(),
 *   provMqttFailed(), provMqttOk(), provRequestReprovision(), provOnProvisioned (callback khi có khoá mới).
 *
 * Bảo mật: không in mật khẩu Wi-Fi / khoá MQTT / token ra Serial. Giới hạn: HTTP không TLS trong LAN.
 */
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <DNSServer.h>

#if defined(ESP32)
  #include <WiFi.h>
  #include <WebServer.h>
  #include <ESPmDNS.h>
  #include <HTTPClient.h>
  #include <Preferences.h>
  typedef WebServer ProvWebServer;
  static inline uint32_t provRand() { return esp_random(); }
#elif defined(ESP8266)
  #include <ESP8266WiFi.h>
  #include <ESP8266WebServer.h>
  #include <ESP8266mDNS.h>
  #include <ESP8266HTTPClient.h>
  #include <LittleFS.h>
  typedef ESP8266WebServer ProvWebServer;
  static inline uint32_t provRand() { return ESP.random(); }
#else
  #error "SmartProvision chi ho tro ESP32 / ESP8266"
#endif

#ifndef SETUP_AP_PASSWORD
  #define SETUP_AP_PASSWORD "smartsetup"          // mật khẩu mạng Setup (>= 8 ký tự) - nên đổi khi triển khai thật
#endif
#define PROV_MDNS_SERVICE     "smart-exam"        // _smart-exam._tcp.local
#define PROV_FALLBACK_HOST    "exam-backend.local"
#define PROV_FALLBACK_PORT    8000
#define PROV_WIFI_TIMEOUT_MS  30000UL             // không vào được Wi-Fi quá lâu -> mở chế độ Setup
#define PROV_TEST_TIMEOUT_MS  15000UL             // thời gian thử 1 mạng trong trang cấu hình
#define PROV_BG_RETRY_MS      60000UL             // đang Setup nhưng đã có Wi-Fi lưu: thử lại nền (router vừa bật lại)
#define PROV_MQTT_FAILS_MAX   3
#define PROV_HOLD_WIFI_MS     5000UL
#define PROV_HOLD_FACTORY_MS  15000UL

// ================================================================ dữ liệu
struct ProvIdentity {
  String claim;                 // claim token: chứng minh "tôi là thiết bị đã báo danh"
  String deviceId, room;
  String mqttHost, mqttUser, mqttPass, topicBase;   // topicBase không có "/" cuối
  uint16_t mqttPort = 1883;
  String framesUrl, httpToken;  // chỉ camera
  String edgeHost;              // server biên gần nhất đã liên lạc được (dự phòng khi mDNS im lặng)
  uint16_t edgePort = PROV_FALLBACK_PORT;
  bool provisioned = false;
};

enum ProvStage { PROV_ST_SETUP, PROV_ST_WIFI, PROV_ST_FIND_EDGE, PROV_ST_NO_EDGE, PROV_ST_PENDING,
                 PROV_ST_REGISTERED, PROV_ST_SUSPENDED, PROV_ST_REJECTED, PROV_ST_READY };

ProvIdentity prov;
void (*provOnProvisioned)() = nullptr;            // sketch gán: gọi khi vừa nhận khoá / topic mới

static String provWifiSsid, provWifiPass;
static String provMac, provMacTail, provAp, provPair;
static bool provSetupMode = false, provMdnsUp = false, provNeedReprov = false, provRoutes = false;
static ProvStage provEnroll = PROV_ST_FIND_EDGE;
static uint8_t provMqttFails = 0;
static uint32_t provWifiLostAt = 0, provNextEnrollAt = 0, provPollMs = 5000, provBgAt = 0;
static IPAddress provEdgeIp;
static uint16_t provEdgePort = PROV_FALLBACK_PORT;

static ProvWebServer provServer(80);
static DNSServer provDns;
static enum { PT_IDLE, PT_TRYING, PT_OK, PT_FAIL } provTry = PT_IDLE;
static bool provTrySave = false, provBgTrying = false;
static String provTrySsid, provTryPass;
static uint32_t provTryAt = 0, provExitAt = 0;

// ================================================================ lưu trữ bền (mỗi nhóm = 1 chuỗi JSON)
// group: "smwifi" (SSID/mật khẩu) | "smident" (danh tính + khoá). Tách nhóm để "xoá Wi-Fi" không mất danh tính.
#if defined(ESP32)
static bool provStoreRead(const char* group, JsonDocument& doc) {
  Preferences p;
  if (!p.begin(group, true)) return false;
  String s = p.getString("json", "");
  p.end();
  return s.length() && !deserializeJson(doc, s);
}
static void provStoreWrite(const char* group, JsonDocument& doc) {
  String s;
  serializeJson(doc, s);
  Preferences p;
  p.begin(group, false);
  p.putString("json", s);
  p.end();
}
static void provStoreClear(const char* group) {
  Preferences p;
  p.begin(group, false);
  p.clear();
  p.end();
}
static void provStoreBegin() {}
#else
static String provStorePath(const char* group) { return String("/") + group + ".json"; }
static bool provStoreRead(const char* group, JsonDocument& doc) {
  File f = LittleFS.open(provStorePath(group), "r");
  if (!f) return false;
  bool ok = !deserializeJson(doc, f);
  f.close();
  return ok;
}
static void provStoreWrite(const char* group, JsonDocument& doc) {
  File f = LittleFS.open(provStorePath(group), "w");
  if (!f) return;
  serializeJson(doc, f);
  f.close();
}
static void provStoreClear(const char* group) { LittleFS.remove(provStorePath(group)); }
static void provStoreBegin() {
  if (!LittleFS.begin()) { LittleFS.format(); LittleFS.begin(); }
}
#endif

static void provSaveIdentity() {
  JsonDocument d;
  d["claim"] = prov.claim;      d["dev"] = prov.deviceId;     d["room"] = prov.room;
  d["mh"] = prov.mqttHost;      d["mp"] = prov.mqttPort;      d["mu"] = prov.mqttUser;
  d["mpw"] = prov.mqttPass;     d["base"] = prov.topicBase;   d["furl"] = prov.framesUrl;
  d["htok"] = prov.httpToken;   d["edge"] = prov.edgeHost;    d["eport"] = prov.edgePort;
  d["ok"] = prov.provisioned;
  provStoreWrite("smident", d);
}

static void provLoadAll() {
  JsonDocument w;
  if (provStoreRead("smwifi", w)) {
    provWifiSsid = w["ssid"] | "";
    provWifiPass = w["pass"] | "";
  }
  JsonDocument d;
  if (provStoreRead("smident", d)) {
    prov.claim = d["claim"] | "";       prov.deviceId = d["dev"] | "";      prov.room = d["room"] | "";
    prov.mqttHost = d["mh"] | "";       prov.mqttPort = d["mp"] | 1883;     prov.mqttUser = d["mu"] | "";
    prov.mqttPass = d["mpw"] | "";      prov.topicBase = d["base"] | "";    prov.framesUrl = d["furl"] | "";
    prov.httpToken = d["htok"] | "";    prov.edgeHost = d["edge"] | "";     prov.edgePort = d["eport"] | PROV_FALLBACK_PORT;
    prov.provisioned = (d["ok"] | false) && prov.deviceId.length() && prov.mqttHost.length();
  }
}

static void provSaveWifi(const String& ssid, const String& pass) {
  JsonDocument w;
  w["ssid"] = ssid;
  w["pass"] = pass;
  provStoreWrite("smwifi", w);
  provWifiSsid = ssid;
  provWifiPass = pass;
}

// ================================================================ trạng thái cho sketch
bool provReady() {
  return !provSetupMode && WiFi.status() == WL_CONNECTED && prov.provisioned && provEnroll == PROV_ST_READY;
}
ProvStage provStage() {
  if (provSetupMode) return PROV_ST_SETUP;
  if (WiFi.status() != WL_CONNECTED) return PROV_ST_WIFI;
  return provEnroll;
}
const String& provPairCode() { return provPair; }
const String& provApName() { return provAp; }
const String& provHardwareUid() { return provMac; }

// Sketch báo kết quả kết nối MQTT. Hỏng PROV_MQTT_FAILS_MAX lần liên tiếp -> xin lại khoá qua kênh đăng ký.
void provMqttOk() { provMqttFails = 0; }
void provRequestReprovision() {
  if (!provNeedReprov) Serial.println("[PROV] hoi lai server bien de nhan khoa moi");
  provNeedReprov = true;
  provNextEnrollAt = 0;
}
void provMqttFailed() {
  if (++provMqttFails >= PROV_MQTT_FAILS_MAX) provRequestReprovision();
}

// ================================================================ LED + nút
static void provLedWrite(bool on) { digitalWrite(PROV_LED_PIN, (on != PROV_LED_ACTIVE_LOW) ? HIGH : LOW); }

static void provLed() {
  uint32_t ms = millis();
  switch (provStage()) {
    case PROV_ST_SETUP:   provLedWrite((ms % 2000) < 1000); break;                       // nháy chậm
    case PROV_ST_WIFI:    provLedWrite((ms % 300) < 150); break;                         // nháy nhanh
    case PROV_ST_NO_EDGE: { uint32_t p = ms % 2000; provLedWrite(p < 120 || (p > 300 && p < 420)); break; }  // 2 chớp
    default:              provLedWrite(true);                                            // đã vào mạng
  }
}

static void provRestart(const char* why) {
  Serial.printf("[PROV] %s -> khoi dong lai\n", why);
  delay(200);
  ESP.restart();
}

static void provButton() {
  static uint32_t downAt = 0;
  bool down = digitalRead(PROV_BUTTON_PIN) == LOW;
  if (down && !downAt) downAt = millis() | 1;
  if (!down && downAt) {
    uint32_t held = millis() - downAt;
    downAt = 0;
    if (held >= PROV_HOLD_FACTORY_MS) {                 // xoá hết: phải đăng ký lại từ đầu
      provStoreClear("smwifi");
      provStoreClear("smident");
      provRestart("factory reset");
    } else if (held >= PROV_HOLD_WIFI_MS) {             // chỉ xoá Wi-Fi, giữ device_id + khoá + claim token
      provStoreClear("smwifi");
      provRestart("xoa cau hinh Wi-Fi");
    }
  }
}

// ================================================================ chế độ Setup (SoftAP + captive portal)
static const char PROV_PAGE[] PROGMEM = R"HTML(<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Cấu hình Wi-Fi</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}
main{max-width:420px;margin:0 auto;padding:20px}h1{font-size:18px;letter-spacing:.04em}
label{display:block;font-size:13px;color:#475569;margin:14px 0 4px}
select,input{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #cbd5e1;border-radius:8px;background:#fff}
.row{display:flex;gap:8px;margin-top:18px}button{flex:1;padding:11px;font-size:15px;border-radius:8px;border:1px solid #2563eb;cursor:pointer}
#t{background:#fff;color:#2563eb}#s{background:#2563eb;color:#fff}button:disabled{opacity:.5}
#m{margin-top:16px;padding:10px;border-radius:8px;font-size:14px;min-height:20px}
.ok{background:#dcfce7;color:#166534}.er{background:#fee2e2;color:#991b1b}.in{background:#e0f2fe;color:#075985}
small{color:#64748b}</style></head><body><main><h1>CẤU HÌNH WI-FI</h1>
<label for="n">Mạng Wi-Fi</label><select id="n"><option value="">Đang quét…</option></select>
<small><a href="#" id="r">Quét lại</a> · mạng ẩn: <a href="#" id="h">nhập tên</a></small>
<input id="hn" placeholder="Tên mạng (SSID)" style="display:none;margin-top:6px" maxlength="32">
<label for="p">Mật khẩu</label><input id="p" type="password" maxlength="64" autocomplete="off">
<div class="row"><button id="t">Kiểm tra kết nối</button><button id="s">Lưu và kết nối</button></div>
<div id="m"></div></main><script>
const $=i=>document.getElementById(i),m=$('m');let busy=false;
function msg(c,t){m.className=c;m.textContent=t}
function lock(b){busy=b;$('t').disabled=$('s').disabled=b}
let scanStarted=0,scanTimer;
function scanError(t){$('n').innerHTML='<option value="">Không quét được mạng</option>';$('hn').style.display='block';msg('er',t+' Có thể nhập tên Wi-Fi 2.4 GHz bên dưới hoặc bấm Quét lại.')}
async function scan(f=false){clearTimeout(scanTimer);if(f)scanStarted=Date.now();if(!scanStarted)scanStarted=Date.now();
if(Date.now()-scanStarted>30000){scanError('Quét Wi-Fi quá thời gian chờ.');return}
try{const response=await fetch('/scan'+(f?'?refresh=1':''),{signal:AbortSignal.timeout(5000),cache:'no-store'});if(!response.ok)throw Error('HTTP');const r=await response.json();
if(r.error){scanError('Thiết bị báo lỗi quét Wi-Fi.');return}
if(r.scanning){scanTimer=setTimeout(()=>scan(false),1200);return}
$('n').innerHTML=r.networks.length?r.networks.map(x=>`<option value="${x.ssid.replace(/"/g,'&quot;')}">${x.ssid.replace(/</g,'&lt;')} (${x.rssi} dBm${x.lock?' 🔒':''})</option>`).join(''):'<option value="">Không thấy mạng nào</option>'}
catch(e){scanTimer=setTimeout(()=>scan(false),2000)}}
async function go(save){if(busy)return;const ssid=$('hn').style.display==='none'?$('n').value:$('hn').value.trim();
if(!ssid){msg('er','Hãy chọn mạng Wi-Fi');return}lock(true);msg('in','Đang kết nối…');
const b=new URLSearchParams({ssid,pass:$('p').value,save:save?1:0});
try{await fetch('/connect',{method:'POST',body:b});poll(save)}catch(e){lock(false);msg('er','Không thể kết nối')}}
async function poll(save){try{const r=await(await fetch('/state')).json();
if(r.s==='trying'){setTimeout(()=>poll(save),1000);return}lock(false);
if(r.s==='ok')msg('ok',save?'Đã lưu. Thiết bị đang chuyển sang mạng đã chọn, mạng Setup sẽ tắt.':'Kết nối thành công. Bấm "Lưu và kết nối" để hoàn tất.');
else msg('er','Không thể kết nối. Kiểm tra lại mật khẩu rồi thử lại.')}
catch(e){setTimeout(()=>poll(save),1500)}}
$('t').onclick=()=>go(false);$('s').onclick=()=>go(true);
$('r').onclick=e=>{e.preventDefault();$('n').innerHTML='<option value="">Đang quét…</option>';scan(true)};
$('h').onclick=e=>{e.preventDefault();$('hn').style.display='block'};scan(true);
</script></body></html>)HTML";

static void provHandleScan() {
  static bool requested = false;
  if (provTry == PT_TRYING || provExitAt) {
    provServer.send(200, "application/json", "{\"error\":\"connecting\"}");
    return;
  }
  if ((!requested || provServer.hasArg("refresh")) && WiFi.scanComplete() != -1) {
    requested = true;
    // ESP32 refuses a scan while STA is still trying to connect to the saved AP.
    // Stop only STA activity: keep the Setup AP and the user's HTTP connection.
    WiFi.setAutoReconnect(false);
    if (WiFi.status() != WL_CONNECTED) {
      WiFi.disconnect(false);
      delay(100);
    }
    provBgTrying = false;
    provBgAt = millis();
    WiFi.scanDelete();
    int started = WiFi.scanNetworks(true);              // quét không chặn
    Serial.printf("[SETUP] bat dau quet Wi-Fi: %d\n", started);
  }
  int n = WiFi.scanComplete();
  if (n == -2) {
    Serial.println("[SETUP] quet Wi-Fi that bai; thu Quet lai hoac nhap ten mang");
    provServer.send(200, "application/json", "{\"error\":\"scan_failed\"}");
    return;
  }
  if (n < 0) { provServer.send(200, "application/json", "{\"scanning\":true}"); return; }
  JsonDocument doc;
  JsonArray arr = doc["networks"].to<JsonArray>();
  for (int i = 0; i < n && i < 25; i++) {
    String ssid = WiFi.SSID(i);
    if (!ssid.length()) continue;
    bool dup = false;
    for (JsonObject o : arr) if (ssid == (o["ssid"] | "")) dup = true;
    if (dup) continue;
    JsonObject o = arr.add<JsonObject>();
    o["ssid"] = ssid;
    o["rssi"] = WiFi.RSSI(i);
#if defined(ESP32)
    o["lock"] = WiFi.encryptionType(i) != WIFI_AUTH_OPEN;
#else
    o["lock"] = WiFi.encryptionType(i) != ENC_TYPE_NONE;
#endif
  }
  Serial.printf("[SETUP] quet Wi-Fi xong: %d mang\n", n);
  String out;
  serializeJson(doc, out);
  provServer.send(200, "application/json", out);
}

static void provHandleConnect() {
  provTrySsid = provServer.arg("ssid");
  provTryPass = provServer.arg("pass");
  provTrySave = provServer.arg("save") == "1";
  if (!provTrySsid.length() || provTrySsid.length() > 32 || provTryPass.length() > 64) {
    provServer.send(400, "application/json", "{\"ok\":false}");
    return;
  }
  provServer.send(200, "application/json", "{\"ok\":true}");
  provBgTrying = false;
  WiFi.disconnect(false);
  WiFi.begin(provTrySsid.c_str(), provTryPass.c_str());   // AP_STA: thử mạng mới mà không tắt mạng Setup
  provTry = PT_TRYING;
  provTryAt = millis();
  Serial.println("[SETUP] thu ket noi Wi-Fi duoc chon");   // không in SSID/mật khẩu
}

static void provHandleState() {
  const char* s = provTry == PT_TRYING ? "trying" : provTry == PT_OK ? "ok" : provTry == PT_FAIL ? "fail" : "idle";
  provServer.send(200, "application/json", String("{\"s\":\"") + s + "\"}");
}

static void provEnterSetup() {
  if (provSetupMode) return;
  provSetupMode = true;
  provMdnsUp = false;
  provTry = PT_IDLE;
  provBgTrying = false;
  provBgAt = millis();
  WiFi.setAutoReconnect(false);                         // Setup owns STA retries; don't compete with scans
  if (WiFi.status() != WL_CONNECTED) WiFi.disconnect(false);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(provAp.c_str(), SETUP_AP_PASSWORD);
  delay(100);
  provDns.start(53, "*", WiFi.softAPIP());                // mọi tên miền -> trang cấu hình (captive portal)
  if (!provRoutes) {
    provRoutes = true;
    provServer.on("/", []() { provServer.send_P(200, "text/html; charset=utf-8", PROV_PAGE); });
    provServer.on("/scan", provHandleScan);
    provServer.on("/connect", HTTP_POST, provHandleConnect);
    provServer.on("/state", provHandleState);
    provServer.onNotFound([]() {
      provServer.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
      provServer.send(302, "text/plain", "");
    });
  }
  provServer.begin();
  Serial.printf("[SETUP] mo mang %s - vao http://%s de cau hinh Wi-Fi\n", provAp.c_str(),
                WiFi.softAPIP().toString().c_str());
}

static void provLeaveSetup() {
  provServer.stop();
  provDns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  provSetupMode = false;
  WiFi.setAutoReconnect(true);
  provWifiLostAt = 0;
  provNextEnrollAt = 0;
  Serial.println("[SETUP] da vao Wi-Fi, tat mang Setup");
}

static void provSetupLoop() {
  provDns.processNextRequest();
  provServer.handleClient();
  uint32_t now = millis();

  if (provTry == PT_TRYING) {
    if (WiFi.status() == WL_CONNECTED) {
      provTry = PT_OK;
      if (provTrySave) {
        provSaveWifi(provTrySsid, provTryPass);
        provExitAt = now + 3000;                          // chờ trang kịp đọc kết quả rồi mới tắt AP
      } else {
        WiFi.disconnect(false);
      }
      provTryPass = "";
    } else if (now - provTryAt > PROV_TEST_TIMEOUT_MS) {
      provTry = PT_FAIL;
      provTryPass = "";
      WiFi.disconnect(false);
    }
    return;
  }
  if (provExitAt && (int32_t)(now - provExitAt) >= 0) {
    provExitAt = 0;
    if (WiFi.status() == WL_CONNECTED) provLeaveSetup();
    return;
  }
  // Đã có Wi-Fi lưu (vào Setup vì mạng tạm mất): thử lại nền khi không ai đang dùng trang cấu hình
  if (provWifiSsid.length() && !provExitAt) {
    if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == provWifiSsid) { provLeaveSetup(); return; }
    if (provBgTrying && (WiFi.softAPgetStationNum() > 0 || now - provBgAt > PROV_TEST_TIMEOUT_MS)) {
      WiFi.disconnect(false);
      provBgTrying = false;
      provBgAt = now;
    }
    if (!provBgTrying && now - provBgAt > PROV_BG_RETRY_MS && WiFi.softAPgetStationNum() == 0
        && WiFi.scanComplete() != -1) {
      provBgAt = now;
      provBgTrying = true;
      WiFi.begin(provWifiSsid.c_str(), provWifiPass.c_str());
    }
  }
}

// ================================================================ tìm server biên + đăng ký
static bool provFindEdge() {
  int n = MDNS.queryService(PROV_MDNS_SERVICE, "tcp");
  if (n > 0) {
#if defined(ESP32)
    provEdgeIp = MDNS.address(0);
#else
    provEdgeIp = MDNS.IP(0);
#endif
    provEdgePort = MDNS.port(0);
    return true;
  }
#if defined(ESP32)
  IPAddress ip = MDNS.queryHost("exam-backend");
  if (ip != IPAddress((uint32_t)0)) { provEdgeIp = ip; provEdgePort = PROV_FALLBACK_PORT; return true; }
#endif
  if (WiFi.hostByName(PROV_FALLBACK_HOST, provEdgeIp)) { provEdgePort = PROV_FALLBACK_PORT; return true; }
  if (prov.edgeHost.length() && provEdgeIp.fromString(prov.edgeHost)) { provEdgePort = prov.edgePort; return true; }
  return false;
}

static String provEdgeUrl(const String& path) {
  return String("http://") + provEdgeIp.toString() + ":" + provEdgePort + "/api/v1/device-enrollments" + path;
}

// GET (body rỗng) hoặc POST JSON tới server biên. Trả mã HTTP (<= 0: lỗi mạng), nội dung vào `out`.
static int provHttp(const String& url, const String& body, String& out) {
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(5000);
  if (!http.begin(client, url)) return -1;
  if (prov.claim.length()) http.addHeader("Authorization", String("Bearer ") + prov.claim);
  int code;
  if (body.length()) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
  } else {
    code = http.GET();
  }
  out = code > 0 ? http.getString() : String();
  http.end();
  return code;
}

static String provIsoNow() {
  time_t now = time(nullptr);
  if (now < 1700000000) return "";
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

static int provAnnounce(String& out) {
  JsonDocument doc;
  char nonce[17];
  snprintf(nonce, sizeof(nonce), "%08lx%08lx", (unsigned long)provRand(), (unsigned long)provRand());
  doc["hardware_uid"] = provMac;
  doc["factory_name"] = String(PROV_FACTORY_PREFIX) + provMacTail;
  doc["device_type"] = PROV_DEVICE_TYPE;
  doc["firmware_version"] = FW_VERSION;
  doc["timestamp"] = provIsoNow();
  doc["nonce"] = nonce;
  String body;
  serializeJson(doc, body);
  return provHttp(provEdgeUrl("/announce"), body, out);
}

static void provApply(JsonDocument& doc) {
  bool dirty = false;
  const char* claim = doc["claim_token"] | "";
  if (*claim) { prov.claim = claim; dirty = true; }       // chỉ được trả 1 lần -> lưu ngay
  String edge = provEdgeIp.toString();
  if (edge != prov.edgeHost || provEdgePort != prov.edgePort) { prov.edgeHost = edge; prov.edgePort = provEdgePort; dirty = true; }
  provPair = doc["pair_code"] | "";
  provPollMs = max(2, doc["poll_interval_s"] | 5) * 1000UL;

  String st = doc["status"] | "";
  if (st == "provisioned") {
    prov.deviceId = doc["device_id"] | "";
    prov.room = doc["room_code"] | "";
    prov.mqttHost = doc["mqtt"]["host"] | "";
    prov.mqttPort = doc["mqtt"]["port"] | 1883;
    prov.mqttUser = doc["mqtt"]["username"] | "";
    prov.mqttPass = doc["mqtt"]["password"] | "";
    prov.topicBase = doc["topics"]["base"] | "";
    prov.framesUrl = doc["http"]["frames_url"] | "";
    prov.httpToken = doc["http"]["token"] | "";
    prov.provisioned = prov.deviceId.length() && prov.mqttHost.length() && prov.topicBase.length();
    provSaveIdentity();
    if (prov.provisioned) {
      provEnroll = PROV_ST_READY;
      provNeedReprov = false;
      provMqttFails = 0;
      provPair = "";
      Serial.printf("[PROV] da nhan khoa: %s phong %s\n", prov.deviceId.c_str(), prov.room.c_str());
      if (provOnProvisioned) provOnProvisioned();
    }
    return;
  }
  ProvStage next = st == "registered" ? PROV_ST_REGISTERED : st == "suspended" ? PROV_ST_SUSPENDED
                 : st == "rejected" ? PROV_ST_REJECTED : PROV_ST_PENDING;
  if (next != provEnroll) {
    Serial.printf("[PROV] trang thai dang ky: %s", st.c_str());
    if (provPair.length()) Serial.printf(" - MA GHEP: %s", provPair.c_str());
    Serial.println();
  }
  provEnroll = next;
  if (dirty) provSaveIdentity();
}

// 1 bước đăng ký: tìm server -> hỏi kết quả (hoặc báo danh) -> xử lý. Chặn tối đa vài giây, chỉ chạy khi chưa có MQTT.
static void provEnrollStep() {
  uint32_t now = millis();
  bool known = prov.provisioned && provNeedReprov;        // đang có khoá cũ, chỉ đi xin lại
  String out;
  int code = -1;
  if (provFindEdge()) {
    if (prov.claim.length()) code = provHttp(provEdgeUrl(String("/") + provMac), "", out);
    if (!prov.claim.length() || code == 401 || code == 404) code = provAnnounce(out);
  }
  if (code <= 0) {                                        // không tìm thấy / không tới được server biên
    if (known) { provNeedReprov = false; provMqttFails = 0; }   // có thể chỉ là broker tạm nghỉ: để sketch thử MQTT tiếp
    else provEnroll = PROV_ST_NO_EDGE;
    provNextEnrollAt = now + 10000;
    return;
  }
  JsonDocument doc;
  if (code != 200 || deserializeJson(doc, out)) {         // 503 broker chưa sẵn sàng, 429 quá nhiều yêu cầu...
    Serial.printf("[PROV] server bien tra %d\n", code);
    provNextEnrollAt = now + 10000;
    return;
  }
  provApply(doc);
  provNextEnrollAt = millis() + provPollMs;
}

// ================================================================ vòng đời
void provBegin() {
  pinMode(PROV_LED_PIN, OUTPUT);
  pinMode(PROV_BUTTON_PIN, INPUT_PULLUP);
  provStoreBegin();
  provLoadAll();

  WiFi.persistent(false);                                 // tự lưu vào vùng riêng, không để SDK ghi flash
  WiFi.mode(WIFI_STA);
#if defined(ESP32)
  // ESP32 core 3.x: WiFi.macAddress() trả 00:00:00:00:00:00 khi giao diện STA chưa khởi động xong
  // -> đọc MAC gốc từ eFuse (cũng chính là MAC của STA), luôn có sẵn.
  uint64_t efuse = ESP.getEfuseMac();
  char macBuf[18];
  snprintf(macBuf, sizeof(macBuf), "%02X:%02X:%02X:%02X:%02X:%02X", (uint8_t)efuse, (uint8_t)(efuse >> 8),
           (uint8_t)(efuse >> 16), (uint8_t)(efuse >> 24), (uint8_t)(efuse >> 32), (uint8_t)(efuse >> 40));
  provMac = macBuf;                                       // "24:6F:28:AA:BB:CC"
#else
  provMac = WiFi.macAddress();
#endif
  provMacTail = provMac.substring(9);
  provMacTail.replace(":", "");
  provAp = String(PROV_AP_PREFIX) + provMacTail;

  WiFi.setAutoReconnect(true);
  provEnroll = prov.provisioned ? PROV_ST_READY : PROV_ST_FIND_EDGE;
  Serial.printf("[PROV] %s%s | Wi-Fi %s | %s\n", PROV_FACTORY_PREFIX, provMacTail.c_str(),
                provWifiSsid.length() ? "da luu" : "chua cau hinh",
                prov.provisioned ? prov.deviceId.c_str() : "chua dang ky");

  if (!provWifiSsid.length()) {
    provEnterSetup();
  } else {
    WiFi.mode(WIFI_STA);
    String host = prov.deviceId.length() ? prov.deviceId : String(PROV_FACTORY_PREFIX) + provMacTail;
#if defined(ESP32)
    WiFi.setHostname(host.c_str());
#else
    WiFi.hostname(host);
#endif
    WiFi.begin(provWifiSsid.c_str(), provWifiPass.c_str());
    provWifiLostAt = millis() | 1;
  }
}

void provLoop() {
  provButton();
  provLed();
  if (provSetupMode) { provSetupLoop(); return; }

  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    provMdnsUp = false;
    if (!provWifiLostAt) provWifiLostAt = now | 1;
    else if (now - provWifiLostAt > PROV_WIFI_TIMEOUT_MS) provEnterSetup();
    return;
  }
  provWifiLostAt = 0;
  if (!provMdnsUp) {
    MDNS.end();
    String host = String(PROV_FACTORY_PREFIX) + provMacTail;
    host.toLowerCase();
    provMdnsUp = MDNS.begin(host.c_str());
  }
#if defined(ESP8266)
  MDNS.update();
#endif
  if (provEnroll == PROV_ST_READY && !provNeedReprov) return;
  if ((int32_t)(now - provNextEnrollAt) < 0) return;
  provEnrollStep();
}
