"""
Kịch bản đầu-cuối trung tâm + biên (chạy theo thứ tự trong tệp, dùng chung 1 hệ thống):

  thiết bị tự báo danh -> duyệt trên bảng điều khiển biên -> tự nhận khoá -> online -> phản chiếu lên trung tâm
  -> telemetry + khuyến nghị LCD -> mở ca thi -> biên nạp vector danh sách dự thi -> AI cho vào phòng + còi
  -> nghi vấn gian lận -> giám thị xác nhận -> còi liên tục -> vé xem ảnh tại biên -> thu hồi / ghép lại.
"""

import json
import time

import numpy as np
import pytest

ROOM = "A101"
CAM_UID, LCD_UID = "24:6F:28:AA:BB:CC", "5C:CF:7F:11:22:33"
state = {}          # dữ liệu chuyền giữa các bước của kịch bản


def announce(system, uid, device_type, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = system.edge.post("/api/v1/device-enrollments/announce", headers=headers, json={
        "hardware_uid": uid, "factory_name": f"FACTORY-{uid[-8:].replace(':', '')}", "device_type": device_type,
        "firmware_version": "2.0.0", "timestamp": "2026-11-12T08:30:00Z", "nonce": "n1"})
    assert r.status_code == 200, r.text
    return r.json()


def poll(system, uid, token):
    return system.edge.get(f"/api/v1/device-enrollments/{uid}", headers={"Authorization": f"Bearer {token}"})


def flush(system):
    """Đẩy hết outbox của biên lên trung tâm."""
    while system.uplink.flush_once():
        pass


def unit(seed):
    v = np.random.default_rng(seed).normal(size=512)
    return v / np.linalg.norm(v)


def wait_for(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ============================================================ 1. đăng ký không chạm

def test_console_requires_login(system):
    assert system.edge.get("/edge/v1/devices").status_code == 401
    assert system.edge.post("/edge/v1/auth/login", json={"password": "sai"}).status_code == 401


def test_device_announces_and_waits_for_approval(system):
    first = announce(system, CAM_UID, "esp32_cam")
    assert first["status"] == "pending" and first["claim_token"] and len(first["pair_code"]) == 4
    state["cam_token"] = first["claim_token"]

    again = announce(system, CAM_UID, "esp32_cam", first["claim_token"])       # khởi động lại: không đổi token
    assert again["status"] == "pending" and "claim_token" not in again
    assert poll(system, CAM_UID, "token-sai").status_code == 401
    assert poll(system, CAM_UID, first["claim_token"]).json()["status"] == "pending"

    pending = system.edge.get("/edge/v1/enrollments", headers=system.console).json()
    assert [(e["hardware_uid"], e["type"], e["pair_code"]) for e in pending] == [(CAM_UID, "camera", first["pair_code"])]
    state["cam_enrollment"] = pending[0]["id"]


def test_register_then_assign_room_are_separate_steps(system):
    r = system.edge.post(f"/edge/v1/enrollments/{state['cam_enrollment']}/register", headers=system.console,
                         json={"device_id": "cam-01"})
    assert r.status_code == 201 and r.json()["state"] == "registered"
    assert poll(system, CAM_UID, state["cam_token"]).json() == {"status": "registered", "device_id": "cam-01",
                                                                 "poll_interval_s": 5}
    assert not system.broker.dynsec_calls, "chưa gán phòng thì chưa được tạo tài khoản broker"

    r = system.edge.post("/edge/v1/devices/cam-01/assign", headers=system.console, json={"room_code": ROOM})
    assert r.status_code == 200 and r.json()["room_code"] == ROOM


def test_device_fetches_its_own_credentials(system):
    creds = poll(system, CAM_UID, state["cam_token"]).json()
    assert creds["status"] == "provisioned" and creds["device_id"] == "cam-01"
    assert creds["mqtt"]["host"] == "192.168.50.10" and creds["mqtt"]["username"] == "cam-01"
    assert creds["topics"]["telemetry"] == f"classroom/{ROOM}/cam-01/telemetry"
    assert creds["http"]["frames_url"] == "http://192.168.50.10:8000/api/v1/devices/cam-01/frames"
    state["cam"] = creds

    created = [c for c in system.broker.dynsec_calls if c["command"] == "createClient"]
    assert created[-1]["username"] == "cam-01" and created[-1]["password"] == creds["mqtt"]["password"]
    role = [c for c in system.broker.dynsec_calls if c["command"] == "createRole"][-1]
    assert all(a["topic"].startswith(f"classroom/{ROOM}/cam-01/") for a in role["acls"]), "ACL chỉ trong namespace riêng"

    # Khoá không nằm trong CSDL biên dưới dạng rõ, cũng không lộ qua API quản trị
    listing = json.dumps(system.edge.get("/edge/v1/devices", headers=system.console).json())
    assert creds["mqtt"]["password"] not in listing and creds["http"]["token"] not in listing


def test_actuator_enrolls_with_suggested_id(system):
    token = announce(system, LCD_UID, "esp8266_actuator")["claim_token"]
    e = next(x for x in system.edge.get("/edge/v1/enrollments", headers=system.console).json()
             if x["hardware_uid"] == LCD_UID)
    assert e["type"] == "actuator" and e["suggested_device_id"] == "lcd-112233"
    system.edge.post(f"/edge/v1/enrollments/{e['id']}/register", headers=system.console, json={})
    system.edge.post("/edge/v1/devices/lcd-112233/assign", headers=system.console, json={"room_code": ROOM})
    creds = poll(system, LCD_UID, token).json()
    assert creds["status"] == "provisioned" and creds["http"] is None
    state["lcd_token"] = token


def test_devices_come_online_and_are_mirrored_to_central(system):
    system.broker.device_says(ROOM, "cam-01", "status", {"state": "online", "fw": "2.0.0", "hw": "ESP32-CAM"})
    system.broker.device_says(ROOM, "lcd-112233", "status", {"state": "online", "fw": "2.0.0"})
    system.broker.device_says(ROOM, "lcd-112233", "heartbeat", {"rssi": -61})
    flush(system)

    devices = {d["device_id"]: d for d in system.central.get("/api/v1/devices", headers=system.admin).json()}
    assert devices["cam-01"]["state"] == "active" and devices["cam-01"]["online"] and devices["cam-01"]["edge_id"] == "edge-01"
    assert devices["lcd-112233"]["rssi"] == -61 and devices["lcd-112233"]["room_code"] == ROOM
    rooms = system.central.get("/api/v1/rooms", headers=system.admin).json()
    assert [r["code"] for r in rooms] == [ROOM], "phòng biên báo lên được tự tạo ở trung tâm"
    state["room_id"] = rooms[0]["id"]


def test_message_from_wrong_room_is_ignored(system):
    system.broker.device_says("B999", "cam-01", "status", {"state": "offline"})
    devices = {d["device_id"]: d for d in system.edge.get("/edge/v1/devices", headers=system.console).json()}
    assert devices["cam-01"]["online"]


# ============================================================ 2. telemetry + cảnh báo

def test_telemetry_drives_lcd_locally_and_reaches_central(system):
    system.central.post("/api/v1/alert-rules", headers=system.admin, json={
        "metric": "temperature_c", "operator": ">", "threshold": 33, "duration_s": 0, "severity": "high",
        "message": "Qua nong"})
    msg = {"msg_id": "a1f3-000001", "device_id": "cam-01", "ts": "", "temperature_c": 35.5, "humidity_pct": 64.0}
    system.broker.device_says(ROOM, "cam-01", "telemetry", msg)
    shown = system.broker.last("lcd-112233/display")
    assert shown["temp_c"] == 35.5 and shown["status"] == "ok", "LCD cập nhật ngay, chưa cần trung tâm"

    system.broker.device_says(ROOM, "cam-01", "telemetry", msg)                 # QoS 1 gửi lại
    system.broker.device_says(ROOM, "cam-01", "telemetry", {**msg, "msg_id": "x", "temperature_c": 99})   # ngoài miền
    flush(system)

    rows = system.central.get("/api/v1/devices/cam-01/telemetry", headers=system.admin).json()
    assert [r["temperature_c"] for r in rows] == [35.5], "loại trùng + loại giá trị ngoài miền DHT11"
    assert system.broker.last("lcd-112233/display")["advice"] == "Qua nong", "khuyến nghị từ quy tắc của trung tâm"
    alerts = system.central.get("/api/v1/alerts?state=active", headers=system.admin).json()
    assert any(a["kind"] == "temperature_c" and a["severity"] == "high" for a in alerts)


def test_events_survive_central_outage(system):
    system.net.down = True
    system.broker.device_says(ROOM, "cam-01", "telemetry", {"msg_id": "a1f3-000002", "temperature_c": 25.0,
                                                           "humidity_pct": 60.0})
    with pytest.raises(Exception):
        system.uplink.flush_once()
    assert system.uplink.backlog() >= 1
    assert system.broker.last("lcd-112233/display")["temp_c"] == 25.0, "LCD vẫn chạy khi mất trung tâm"
    system.net.down = False
    flush(system)
    assert system.uplink.backlog() == 0
    rows = system.central.get("/api/v1/devices/cam-01/telemetry", headers=system.admin).json()
    assert [r["temperature_c"] for r in rows] == [35.5, 25.0]


def test_offline_device_raises_alert_and_recovers(system):
    system.broker.device_says(ROOM, "lcd-112233", "status", {"state": "offline"})          # Last Will
    flush(system)
    alerts = system.central.get("/api/v1/alerts?state=active&kind=device_offline", headers=system.admin).json()
    assert len(alerts) == 1 and "lcd-112233" in alerts[0]["message"]
    system.broker.device_says(ROOM, "lcd-112233", "heartbeat", {"rssi": -60})
    flush(system)
    assert not system.central.get("/api/v1/alerts?state=active&kind=device_offline", headers=system.admin).json()


# ============================================================ 3. lệnh có ACK

def test_command_from_central_is_acked_by_device(system):
    r = system.central.post("/api/v1/devices/lcd-112233/commands", headers=system.admin,
                            json={"action": "buzzer", "pattern": "once"})
    assert r.status_code == 202, r.text
    cmd_id = r.json()["cmd_id"]
    assert wait_for(lambda: (system.broker.last("lcd-112233/cmd") or {}).get("cmd_id") == cmd_id)
    sent = system.broker.last("lcd-112233/cmd")
    assert sent["action"] == "buzzer" and sent["pattern"] == "once" and sent["expires_at"].endswith("Z")

    system.broker.device_says(ROOM, "lcd-112233", "cmd/ack", {"cmd_id": cmd_id, "result": "ok"})
    history = system.central.get("/api/v1/devices/lcd-112233/commands", headers=system.admin).json()
    assert history[0]["cmd_id"] == cmd_id and history[0]["status"] == "acked" and history[0]["issued_by"].startswith("user:")


def test_unacked_command_is_retried_then_times_out(system):
    cmd_id = system.edge.post("/edge/v1/devices/lcd-112233/commands", headers=system.console,
                              json={"action": "display", "text": "Xin chao"}).json()["cmd_id"]

    def status():
        return system.edge.get("/edge/v1/devices/lcd-112233/commands", headers=system.console).json()[0]

    assert wait_for(lambda: status()["status"] == "timeout", timeout=5)
    assert status()["cmd_id"] == cmd_id and status()["attempts"] == 3 and status()["result"] == "no_ack"
    assert system.central.post("/api/v1/devices/cam-01/commands", headers=system.admin,
                               json={"action": "buzzer", "pattern": "once"}).status_code == 400


# ============================================================ 4. ca thi: vector xuống biên, kết quả lên trung tâm

def test_faces_are_enrolled_as_vectors_only(system):
    c = system.central
    klass = c.post("/api/v1/classes", headers=system.admin, json={"code": "IOT-K67", "name": "IoT K67"}).json()
    ids = []
    for i, name in enumerate(["Nguyen An", "Tran Binh", "Le Chi"], start=1):
        s = c.post("/api/v1/students", headers=system.admin, json={
            "student_code": f"SV00{i}", "full_name": name, "consent": i != 3, "class_ids": [klass["id"]]}).json()
        ids.append(s["id"])
    state.update(class_id=klass["id"], students=ids)
    edge_auth = {"Authorization": "Bearer test-edge-key", "X-Edge-Id": "edge-01"}

    for seed, sid in ((1, ids[0]), (2, ids[1])):
        r = c.post("/api/v1/edge/faces", headers=edge_auth, json={
            "student_id": sid, "samples": [{"id": f"00000000-0000-0000-0000-00000000000{seed}",
                                            "vector": unit(seed).tolist()}]})
        assert r.status_code == 200 and r.json()["face_samples"] == 1
    no_consent = c.post("/api/v1/edge/faces", headers=edge_auth, json={
        "student_id": ids[2], "samples": [{"id": "00000000-0000-0000-0000-000000000003", "vector": unit(3).tolist()}]})
    assert no_consent.status_code == 409, "chưa đồng ý thì không nhận vector"
    assert c.post("/api/v1/edge/faces", headers={"X-Edge-Id": "edge-01", "Authorization": "Bearer sai"},
                  json={}).status_code == 401
    # Trung tâm không có endpoint nào nhận ảnh khuôn mặt
    assert c.post(f"/api/v1/students/{ids[0]}/face-samples", headers=system.admin).status_code == 405


def test_opening_exam_pushes_roster_vectors_to_edge(system):
    r = system.central.post("/api/v1/exam/sessions", headers=system.admin, json={
        "room_id": state["room_id"], "class_id": state["class_id"], "name": "Giua ky"})
    assert r.status_code == 201, r.text
    exam = r.json()
    assert exam["status"] == "checkin" and exam["checkin_camera"] == "cam-01"
    state["exam_id"] = exam["id"]

    status = system.edge.get("/edge/v1/status", headers=system.console).json()
    assert status["exams"] == [{"exam_id": exam["id"], "room_code": ROOM, "phase": "checkin", "name": "Giua ky",
                                "roster": 2}], "chỉ SV đã đồng ý + có mẫu mới được phát xuống biên"
    assert system.broker.last("cam-01/config")["camera_mode"] == "checkin"


def test_frame_is_stored_at_edge_and_queued_for_ai(system):
    url, token = "/api/v1/devices/cam-01/frames", state["cam"]["http"]["token"]
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 2000
    assert system.edge.post(url, content=jpeg, headers={"Authorization": "Bearer sai"}).status_code == 401
    assert system.edge.post(url, content=b"khong phai jpeg", headers={"Authorization": f"Bearer {token}"}).status_code == 415
    assert system.edge.post(url, content=b"\xff\xd8" + b"0" * 200_000,
                            headers={"Authorization": f"Bearer {token}"}).status_code == 413
    r = system.edge.post(url, content=jpeg, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202 and r.json()["kind"] == "checkin"
    state["frame_id"] = r.json()["frame_id"]
    # khung kế tiếp trong cùng giây chỉ phát realtime, không lưu
    assert system.edge.post(url, content=jpeg, headers={"Authorization": f"Bearer {token}"}).json()["status"] == "streamed"


def test_face_match_admits_student_once_and_beeps(system):
    from backend.edge.app.core.db import session_scope
    from backend.edge.app.models import ExamState
    from backend.edge.app.workers import common, face_worker

    exam_id, (an, binh, _) = state["exam_id"], state["students"]
    job = common.claim("checkin")
    assert job["frame_id"] == state["frame_id"] and common.claim("checkin") is None, "job không bị lấy 2 lần"
    with session_scope() as db:
        roster_hash = db.get(ExamState, exam_id).roster_hash

    noisy = unit(1) + 0.02 * unit(99)
    assert face_worker.match(exam_id, roster_hash, noisy)[0] == an
    assert face_worker.match(exam_id, roster_hash, unit(500))[0] is None, "người lạ không khớp ai"

    with session_scope() as db:
        assert face_worker.confirm(db, exam_id, an, job["frame_id"], 0.9, {}) == (False, 1, None), "1 khung chưa đủ"
    with session_scope() as db:
        assert face_worker.confirm(db, exam_id, an, job["frame_id"], 0.9, {}) == (True, 2, None)
    with session_scope() as db:
        assert face_worker.confirm(db, exam_id, an, job["frame_id"], 0.9, {}) == (False, 0, "admitted")

    system.broker.published.clear()
    r = system.edge.post("/internal/checkin-admitted", headers={"X-Service-Token": "edge-worker-token"},
                         json={"exam_id": exam_id, "student_id": an, "confidence": 0.9, "frame_id": job["frame_id"]})
    assert r.status_code == 200 and len(r.json()["commands"]) == 1
    assert wait_for(lambda: (system.broker.last("lcd-112233/cmd") or {}).get("pattern") == "once"), "còi chíp tại biên"
    flush(system)

    detail = system.central.get(f"/api/v1/exam/sessions/{exam_id}", headers=system.admin).json()
    record = next(x for x in detail["roster"] if x["student_id"] == an)
    assert record["status"] == "admitted" and record["method"] == "auto" and record["has_frame"]
    state["record_id"] = record["id"]
    assert next(x for x in detail["roster"] if x["student_id"] == binh)["status"] == "waiting"


def test_manual_decision_is_synced_down_to_edge(system):
    exam_id, binh = state["exam_id"], state["students"][1]
    r = system.central.post(f"/api/v1/exam/sessions/{exam_id}/checkin", headers=system.admin,
                            json={"student_id": binh, "status": "denied", "note": "Sai giay to"})
    assert r.status_code == 200
    from backend.edge.app.core.db import session_scope
    from backend.edge.app.models import ExamState
    with session_scope() as db:
        assert db.get(ExamState, exam_id).decided[str(binh)] == "denied"


def test_unknown_face_is_identified_by_vector_only(system):
    edge_auth = {"Authorization": "Bearer test-edge-key", "X-Edge-Id": "edge-01"}
    hit = system.central.post("/api/v1/edge/identify", headers=edge_auth, json={"vector": unit(2).tolist()}).json()
    assert hit["matched"] and hit["student_code"] == "SV002"
    miss = system.central.post("/api/v1/edge/identify", headers=edge_auth, json={"vector": unit(777).tolist()}).json()
    assert not miss["matched"]


def test_suspicion_goes_to_proctor_and_confirm_sounds_alarm(system):
    exam_id = state["exam_id"]
    assert system.central.post(f"/api/v1/exam/sessions/{exam_id}/start", headers=system.admin).status_code == 200
    assert system.broker.last("cam-01/config")["camera_mode"] == "exam"

    r = system.edge.post("/internal/emit", headers={"X-Service-Token": "edge-worker-token"}, json={
        "type": "exam.suspicion", "data": {"exam_id": exam_id, "type": "phone", "score": 0.81, "student_code": "SV001",
                                           "detail": {"streak": 3}, "frame_ref": state["frame_id"], "severity": "high"}})
    assert r.status_code == 200
    assert system.edge.post("/internal/emit", json={"type": "exam.suspicion", "data": {}}).status_code == 401
    flush(system)

    detail = system.central.get(f"/api/v1/exam/sessions/{exam_id}", headers=system.admin).json()
    event = detail["events"][0]
    assert event["type"] == "phone" and event["review_status"] == "pending" and event["has_frame"]
    assert any(a["kind"] == "suspicion" for a in
               system.central.get("/api/v1/alerts?state=active", headers=system.admin).json())

    system.broker.published.clear()
    r = system.central.patch(f"/api/v1/exam/events/{event['id']}", headers=system.admin,
                             json={"decision": "confirmed", "note": "Thay ro"})
    assert r.status_code == 200 and len(r.json()["commands"]) == 1 and r.json()["buzzer_error"] is None
    assert wait_for(lambda: (system.broker.last("lcd-112233/cmd") or {}).get("pattern") == "continuous")
    state["event_id"] = event["id"]


def test_images_stay_at_edge_and_need_a_ticket(system):
    r = system.central.get(f"/api/v1/exam/events/{state['event_id']}/frame", headers=system.admin)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    url = r.json()["url"]
    assert url.startswith(f"http://192.168.50.10:8000/edge/v1/frames/{state['frame_id']}?ticket=")
    path = url.split("192.168.50.10:8000", 1)[1]

    image = system.edge.get(path)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert system.edge.get(f"/edge/v1/frames/{state['frame_id']}").status_code == 401, "không vé không xem"
    other = path.replace(f"/frames/{state['frame_id']}", f"/frames/{state['frame_id'] + 1}")
    assert system.edge.get(other).status_code == 401, "vé chỉ dùng cho đúng 1 ảnh"

    ticket = system.central.get("/api/v1/devices/cam-01/stream-ticket", headers=system.admin).json()["url"]
    assert "/edge/v1/devices/cam-01/stream?ticket=" in ticket
    audit = system.central.get("/api/v1/audit-logs?action=exam.view_frame", headers=system.admin).json()
    assert audit, "mỗi lượt xem ảnh bằng chứng đều ghi audit ở trung tâm"


def test_nothing_sent_to_central_contains_an_image(system):
    """Yêu cầu riêng tư: biên chỉ gửi embedding + metadata. Soi toàn bộ sự kiện đã phát trong kịch bản."""
    from backend.edge.app.core.db import session_scope
    from backend.edge.app.models import AuditLog, Outbox
    from backend.edge.app.services import uplink

    with session_scope() as db:
        uplink.emit(db, "checkin.seen", {"exam_id": state["exam_id"], "student_id": 1}, ttl_s=5)
        db.flush()
        pending = [json.dumps(o.data) for o in db.query(Outbox)]
        assert db.query(AuditLog).filter(AuditLog.action == "frame.view").count() == 1
    for payload in pending:
        assert "/9j/" not in payload and "\\xff\\xd8" not in payload and len(payload) < 4000
    flush(system)


def test_closing_exam_marks_absent_and_clears_edge(system):
    out = system.central.post(f"/api/v1/exam/sessions/{state['exam_id']}/close", headers=system.admin).json()
    assert out["status"] == "closed" and out["absent_marked"] == 1          # Le Chi chưa vào phòng
    status = system.edge.get("/edge/v1/status", headers=system.console).json()
    assert status["exams"] == []
    assert system.broker.last("cam-01/config")["camera_mode"] == "idle"
    from backend.edge.app.services import gallery
    assert not gallery.exists(state["exam_id"]), "vector của ca thi bị xoá khỏi biên khi ca kết thúc"


# ============================================================ 5. vòng đời không cần nạp lại firmware

def test_moving_room_revokes_key_and_device_refetches(system):
    old_password = state["cam"]["mqtt"]["password"]
    system.broker.dynsec_calls.clear()
    r = system.edge.post("/edge/v1/devices/cam-01/assign", headers=system.console, json={"room_code": "B202"})
    assert r.json()["state"] == "registered" and not r.json()["has_credentials"]
    assert {"command": "deleteClient", "username": "cam-01"} in system.broker.dynsec_calls
    token = state["cam"]["http"]["token"]
    assert system.edge.post("/api/v1/devices/cam-01/frames", content=b"\xff\xd8x",
                            headers={"Authorization": f"Bearer {token}"}).status_code == 401

    creds = poll(system, CAM_UID, state["cam_token"]).json()                  # firmware tự hỏi lại sau khi MQTT từ chối
    assert creds["status"] == "provisioned" and creds["topics"]["base"] == "classroom/B202/cam-01"
    assert creds["mqtt"]["password"] != old_password
    flush(system)
    devices = {d["device_id"]: d for d in system.central.get("/api/v1/devices", headers=system.admin).json()}
    assert devices["cam-01"]["room_code"] == "B202"


def test_suspend_blocks_and_unsuspend_restores(system):
    system.edge.post("/edge/v1/devices/cam-01/transition", headers=system.console, json={"to": "suspended"})
    assert poll(system, CAM_UID, state["cam_token"]).json()["status"] == "suspended"
    r = system.edge.post("/edge/v1/devices/cam-01/transition", headers=system.console, json={"to": "active"})
    assert r.json()["state"] in ("provisioned", "offline")
    # chỉ thoát bảo trì / mở khoá mới chuyển tay sang "active"; thiết bị đang offline thì phải chờ heartbeat thật
    assert system.edge.post("/edge/v1/devices/cam-01/transition", headers=system.console,
                            json={"to": "active"}).status_code == 409


def test_spoofed_mac_cannot_steal_credentials(system):
    """Kẻ giả MAC của thiết bị đã đăng ký không có claim token -> chỉ tạo được yêu cầu ghép lại chờ duyệt."""
    fake = announce(system, LCD_UID, "esp8266_actuator")
    assert fake["status"] == "pending" and fake["claim_token"] != state["lcd_token"]
    assert poll(system, LCD_UID, fake["claim_token"]).json()["status"] == "pending"
    assert poll(system, LCD_UID, state["lcd_token"]).json()["status"] == "provisioned", "thiết bị thật không bị ảnh hưởng"

    e = next(x for x in system.edge.get("/edge/v1/enrollments", headers=system.console).json()
             if x["hardware_uid"] == LCD_UID)
    assert e["reclaim_pending"] and e["reclaim_pair_code"] == fake["pair_code"]
    system.edge.post(f"/edge/v1/enrollments/{e['id']}/reclaim?accept=false", headers=system.console)
    assert poll(system, LCD_UID, fake["claim_token"]).status_code == 401


def test_factory_reset_device_is_repaired_after_admin_accepts(system):
    fresh = announce(system, LCD_UID, "esp8266_actuator")
    e = next(x for x in system.edge.get("/edge/v1/enrollments", headers=system.console).json()
             if x["hardware_uid"] == LCD_UID)
    system.edge.post(f"/edge/v1/enrollments/{e['id']}/reclaim?accept=true", headers=system.console)
    assert poll(system, LCD_UID, state["lcd_token"]).status_code == 401, "token cũ hết hiệu lực"
    assert poll(system, LCD_UID, fresh["claim_token"]).json()["status"] == "provisioned"


def test_decommission_is_final_until_record_deleted(system):
    system.edge.post("/edge/v1/devices/cam-01/transition", headers=system.console, json={"to": "decommissioned"})
    assert poll(system, CAM_UID, state["cam_token"]).json()["status"] == "rejected"
    assert announce(system, CAM_UID, "esp32_cam")["status"] == "rejected"
    flush(system)
    devices = {d["device_id"]: d for d in system.central.get("/api/v1/devices", headers=system.admin).json()}
    assert devices["cam-01"]["state"] == "decommissioned"

    assert system.edge.delete("/edge/v1/devices/cam-01", headers=system.console).status_code == 204
    assert announce(system, CAM_UID, "esp32_cam")["status"] == "pending", "xoá bản ghi -> phần cứng đăng ký lại từ đầu"


def test_viewer_cannot_operate(system):
    system.central.post("/api/v1/users", headers=system.admin, json={
        "email": "viewer@x.vn", "full_name": "Nguoi xem", "password": "Viewer@123", "role": "viewer",
        "room_ids": [state["room_id"]]})
    token = system.central.post("/api/v1/auth/login", json={"email": "viewer@x.vn", "password": "Viewer@123"}
                                ).json()["access_token"]
    viewer = {"Authorization": f"Bearer {token}"}
    assert system.central.get("/api/v1/devices", headers=viewer).status_code == 200
    assert system.central.post("/api/v1/devices/lcd-112233/commands", headers=viewer,
                               json={"action": "buzzer", "pattern": "stop"}).status_code == 403
    assert system.central.get(f"/api/v1/exam/events/{state['event_id']}/frame", headers=viewer).status_code == 403
