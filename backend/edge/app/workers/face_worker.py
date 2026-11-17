"""
face_worker - Kiểm tra khuôn mặt sinh viên trước khi vào phòng thi (giai đoạn "checkin"), chạy TẠI BIÊN.

Mỗi khung ảnh camera ở cửa (lấy mẫu ~1 ảnh/giây):
  1. Phát hiện khuôn mặt + vector 512 chiều (InsightFace buffalo_l)
  2. So với vector của danh sách dự thi đã tải về biên (services/gallery.py):
       điểm cao nhất >= SIM_THRESHOLD và cách ứng viên 2 >= MARGIN -> khớp
  3. Khớp CONFIRM_HITS khung liên tiếp -> CHO VÀO (checkin_done, mỗi SV 1 lần / ca)
       -> server biên: còi chíp 1 lần ngay + báo trung tâm (chỉ mã SV, điểm, số tham chiếu ảnh)
  4. Không khớp danh sách: gửi VECTOR (không gửi ảnh) lên trung tâm để tìm trên toàn bộ SV
       - nhận ra SV khác   -> lượt từ chối "not_in_roster" (sai phòng / không có tên)
       - không nhận ra ai  -> lượt từ chối "unknown" (người lạ; cũng dùng khi mất kết nối trung tâm)
     Mỗi người tối đa 1 lượt từ chối / REJECT_COOLDOWN_S

Chạy (ở thư mục gốc, server biên đang chạy):  python -m backend.edge.app.workers.face_worker
"""

import time
from datetime import timedelta

import cv2
import requests
from sqlalchemy.exc import IntegrityError

from backend.edge.app.core.config import settings
from backend.edge.app.core.db import session_scope
from backend.edge.app.models import CheckinDone, CheckinHit, ExamState
from backend.edge.app.services import gallery
from backend.edge.app.workers.common import notify, run_forever, setup_logging
from backend.shared.db import utcnow

SIM_THRESHOLD = 0.45        # cosine tối thiểu
MARGIN = 0.05               # cách ứng viên thứ 2 tối thiểu
MIN_FACE_PX = 40            # mặt nhỏ hơn: quá xa, không nhận diện
MIN_FACE_REJECT_PX = 70     # chỉ báo "người lạ" khi mặt đủ to, rõ (đang đứng trước camera)
CONFIRM_HITS = 2            # 2 khung liên tiếp
CONFIRM_WINDOW_S = 5        # 2 lần khớp cách nhau tối đa 5s (ảnh lấy mẫu mỗi 1s)
REJECT_COOLDOWN_S = 10

log = setup_logging("face_worker")
_last_reject: dict[tuple, float] = {}


def match(exam_id: int, roster_hash: str, embedding):
    """-> (student_id, sim) nếu khớp chắc chắn, ngược lại (None, sim cao nhất)."""
    top = gallery.search(exam_id, roster_hash, embedding, limit=2)
    if not top:
        return None, 0.0
    sid, sim = top[0]
    second = top[1][1] if len(top) > 1 else 0.0
    return (sid, sim) if sim >= SIM_THRESHOLD and sim - second >= MARGIN else (None, sim)


def confirm(db, exam_id: int, student_id: int, frame_id: int, sim: float, decided: dict):
    """Bộ đếm 2 khung. -> (vừa được cho vào?, số lần khớp, trạng thái đã có trước đó | None)"""
    previous = decided.get(str(student_id))
    if previous is None and db.query(CheckinDone).filter_by(exam_id=exam_id, student_id=student_id).first():
        previous = "admitted"
    if previous:
        return False, 0, previous

    now = utcnow()
    hit = db.get(CheckinHit, (exam_id, student_id))
    if hit is None:
        hit = CheckinHit(exam_id=exam_id, student_id=student_id, hits=0, updated_at=now)
        db.add(hit)
    fresh = hit.hits and now - hit.updated_at <= timedelta(seconds=CONFIRM_WINDOW_S)
    hit.hits, hit.updated_at = (hit.hits + 1 if fresh else 1), now
    if hit.hits < CONFIRM_HITS:
        return False, hit.hits, None
    hits = hit.hits
    try:                                              # UNIQUE(exam_id, student_id): mỗi SV đúng 1 lần / ca
        db.add(CheckinDone(exam_id=exam_id, student_id=student_id, confidence=round(sim, 3), frame_id=frame_id))
        db.flush()
    except IntegrityError:                            # worker khác vừa ghi nhận SV này
        db.rollback()
        return False, hits, "admitted"
    return True, hits, None


def identify_outside_roster(embedding):
    """Hỏi trung tâm bằng vector: đây có phải SV khác của trường không? -> (student_id, sim) | (None, sim)."""
    from backend.edge.app.services import uplink
    try:
        hit = uplink.post("/identify", {"vector": embedding.astype(float).tolist()}, timeout=4)
    except requests.RequestException as e:
        log.warning("Không hỏi được trung tâm, coi là người lạ: %s", e)
        return None, 0.0
    return (hit["student_id"], hit["score"]) if hit.get("matched") else (None, hit.get("score", 0.0))


def reject(exam_id, frame_id, result, student_id, sim):
    key = (exam_id, result, student_id)
    if time.time() - _last_reject.get(key, 0) < REJECT_COOLDOWN_S:
        return
    _last_reject[key] = time.time()
    log.info("TỪ CHỐI %s %s (%.2f)", result, student_id or "", sim)
    notify(log, "/emit", {"type": "checkin.rejected", "data": {
        "exam_id": exam_id, "result": result, "student_id": student_id, "confidence": round(sim, 3),
        "frame_ref": frame_id}})


def handle(job):
    from backend.edge.app.services import face_model

    exam_id = job["exam_id"]
    with session_scope() as db:
        exam = db.get(ExamState, exam_id)
        if exam is None or exam.phase != "checkin":
            return
        students, decided, roster_hash = dict(exam.students or {}), dict(exam.decided or {}), exam.roster_hash

    img = cv2.imread(str(settings.storage_dir / job["file_path"]))
    if img is None:
        raise RuntimeError(f"Không đọc được ảnh {job['file_path']}")

    for f in face_model.faces(img, MIN_FACE_PX):
        size = int(min(f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1]))
        sid, sim = match(exam_id, roster_hash, f.normed_embedding)

        if sid is not None:
            with session_scope() as db:
                admitted, hits, previous = confirm(db, exam_id, sid, job["frame_id"], sim, decided)
            code, name = students.get(str(sid), ["?", "?"])
            if admitted:
                log.info("CHO VÀO %s %s (%.2f) ca %s", code, name, sim, exam_id)
                notify(log, "/checkin-admitted", {"exam_id": exam_id, "student_id": sid,
                                                  "confidence": round(sim, 3), "frame_id": job["frame_id"]})
            else:
                notify(log, "/emit", {"type": "checkin.seen", "ttl_s": 5, "data": {
                    "exam_id": exam_id, "student_id": sid, "student_code": code, "full_name": name,
                    "confidence": round(sim, 3), "state": "already" if previous else "confirming",
                    "previous_status": previous, "hits": hits}})
            continue

        if size < MIN_FACE_REJECT_PX:
            continue
        other, other_sim = identify_outside_roster(f.normed_embedding)
        if other is not None and str(other) not in students:
            reject(exam_id, job["frame_id"], "not_in_roster", other, other_sim)
        elif other is None:
            reject(exam_id, job["frame_id"], "unknown", None, max(sim, other_sim))


if __name__ == "__main__":
    from backend.edge.app.services import face_model

    log.info("Nạp mô hình InsightFace...")
    face_model.get_app()
    run_forever("checkin", handle, log)
