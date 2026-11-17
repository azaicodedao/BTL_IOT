"""
exam_worker - Phân tích khung hình trong giờ thi (mục 9.3.2, sơ đồ 3), chạy TẠI BIÊN.

Mỗi khung ảnh (lấy mẫu ~1 ảnh / 2 giây khi ca thi ở giai đoạn in_progress):
  1. YOLO11n: phát hiện điện thoại (COCO "cell phone")
  2. InsightFace + landmark 3D: ước lượng góc quay đầu (yaw) của từng khuôn mặt
  3. Điểm nghi vấn theo từng tín hiệu: phone = độ tin cậy, head_turn = |yaw| lớn nhất (quy về 0..1)
  4. suspicion_state: số khung nghi vấn liên tiếp (streak); khung bình thường -> về 0
  5. streak đạt STREAK_N -> đánh dấu ảnh bằng chứng (lưu tại biên), đoán MSSV trong số SV đã vào phòng
     -> báo trung tâm: loại, điểm, MSSV, số tham chiếu ảnh. KHÔNG gửi ảnh.
AI chỉ cảnh báo; quyết định là của giám thị.

Chạy (ở thư mục gốc, server biên đang chạy):  python -m backend.edge.app.workers.exam_worker
"""

import time

import cv2

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import CheckinDone, ExamState, SuspicionState
from backend.edge.app.services import gallery
from backend.edge.app.workers.common import notify, run_forever, setup_logging
from backend.shared.db import utcnow

PHONE_CLASS = 67              # COCO: cell phone
PHONE_CONF = 0.35
YAW_LIMIT = 35.0              # quay đầu quá 35 độ được coi là nghi vấn
STREAK_N = 3                  # 3 khung liên tiếp (~6 giây) mới cảnh báo
COOLDOWN_S = 30               # cùng 1 loại tín hiệu: tối đa 1 cảnh báo / 30 giây
IDENTIFY_SIM = 0.40           # ngưỡng đoán MSSV cho ảnh bằng chứng (chỉ để tham khảo)

log = setup_logging("exam_worker")
_yolo = None
_device = None
_last_event: dict[tuple, float] = {}


def yolo():
    global _yolo, _device
    if _yolo is None:
        import torch
        from ultralytics import YOLO

        _device = 0 if torch.cuda.is_available() else "cpu"
        _yolo = YOLO(str(settings.yolo_weights))
    return _yolo


def nearest_face(faces, box):
    if not faces:
        return None
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return min(faces, key=lambda f: ((f.bbox[0] + f.bbox[2]) / 2 - cx) ** 2 + ((f.bbox[1] + f.bbox[3]) / 2 - cy) ** 2)


def analyse(img):
    """-> {type: (score, detail, face_for_identity, boxes_to_draw)} chỉ gồm các tín hiệu nghi vấn trong khung."""
    from backend.edge.app.services import face_model

    found = {}
    faces = face_model.faces(img, 30, with_pose=True)

    res = yolo().predict(img, classes=[PHONE_CLASS], conf=PHONE_CONF, imgsz=640, device=_device, verbose=False)[0]
    phones = [(float(b.conf), b.xyxy[0].tolist()) for b in res.boxes] if res.boxes is not None else []
    if phones:
        conf, box = max(phones)
        found["phone"] = (conf, {"phone_conf": round(conf, 3), "phone_box": [round(v) for v in box],
                                 "phones": len(phones)}, nearest_face(faces, box), [("phone", box)])

    turned = [(abs(float(f.pose[1])), f) for f in faces if getattr(f, "pose", None) is not None
              and abs(float(f.pose[1])) > YAW_LIMIT]
    if turned:
        yaw, f = max(turned, key=lambda x: x[0])
        found["head_turn"] = (min(1.0, yaw / 90.0), {"yaw_deg": round(yaw, 1), "faces": len(faces),
                                                     "turned": len(turned)},
                              f, [("head", f.bbox.tolist()) for _, f in turned])
    return found


def annotate(path, boxes):
    img = cv2.imread(str(path))
    for label, (x1, y1, x2, y2) in boxes:
        color = (0, 0, 255) if label == "phone" else (0, 165, 255)
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        cv2.putText(img, "DIEN THOAI" if label == "phone" else "QUAY DAU", (int(x1), max(int(y1) - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])


def update_streak(exam_id: int, type_: str, flagged: bool) -> int:
    with session_scope() as db:
        s = db.get(SuspicionState, (exam_id, type_))
        if s is None:
            s = SuspicionState(exam_id=exam_id, type=type_, streak=0)
            db.add(s)
        s.streak, s.updated_at = (s.streak + 1 if flagged else 0), utcnow()
        return s.streak


def reset_streak(exam_id: int, type_: str):
    with session_scope() as db:
        db.query(SuspicionState).filter_by(exam_id=exam_id, type=type_).update({"streak": 0})


def identify(exam_id, roster_hash, students, face, admitted_ids):
    """Đoán MSSV của khuôn mặt trong số SV đã vào phòng (quay nghiêng nên chỉ là tham khảo)."""
    if face is None or not admitted_ids:
        return None
    top = gallery.search(exam_id, roster_hash, face.normed_embedding, only_ids=admitted_ids, limit=1)
    return students.get(str(top[0][0]), [None])[0] if top and top[0][1] >= IDENTIFY_SIM else None


def handle(job):
    exam_id = job["exam_id"]
    with session_scope() as db:
        exam = db.get(ExamState, exam_id)
        if exam is None or exam.phase != "in_progress":
            return
        students, roster_hash = dict(exam.students or {}), exam.roster_hash
        admitted = {r.student_id for r in db.query(CheckinDone.student_id).filter_by(exam_id=exam_id)}
        admitted |= {int(k) for k, v in (exam.decided or {}).items() if v == "admitted"}
        admitted -= {int(k) for k, v in (exam.decided or {}).items() if v == "denied"}

    path = settings.storage_dir / job["file_path"]
    img = cv2.imread(str(path))
    if img is None:
        raise RuntimeError(f"Không đọc được ảnh {job['file_path']}")

    found = analyse(img)
    for type_ in ("phone", "head_turn"):
        streak = update_streak(exam_id, type_, type_ in found)
        if type_ not in found or streak < STREAK_N:
            continue
        if time.time() - _last_event.get((exam_id, type_), 0) < COOLDOWN_S:
            continue
        _last_event[(exam_id, type_)] = time.time()

        score, detail, face, boxes = found[type_]
        annotate(path, boxes)
        code = identify(exam_id, roster_hash, students, face, admitted)
        reset_streak(exam_id, type_)
        log.info("NGHI VẤN %s ca %s (%.2f) %s", type_, exam_id, score, code or "")
        notify(log, "/emit", {"type": "exam.suspicion", "data": {
            "exam_id": exam_id, "type": type_, "score": round(score, 3), "student_code": code,
            "detail": {**detail, "streak": streak}, "frame_ref": job["frame_id"],
            "severity": "high" if type_ == "phone" else "medium"}})


if __name__ == "__main__":
    from backend.edge.app.services import face_model

    yolo()
    log.info("Đã nạp YOLO11n (%s), đang nạp InsightFace...", "GPU" if _device == 0 else "CPU")
    face_model.get_app(with_pose=True)
    run_forever("exam", handle, log)
