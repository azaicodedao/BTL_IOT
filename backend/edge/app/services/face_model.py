"""
InsightFace buffalo_l dùng chung (nạp 1 lần, lazy).

- Điểm danh / đăng ký mặt: chỉ cần detection + recognition (nhanh hơn).
- Giám sát thi: thêm landmark_3d_68 để lấy góc quay đầu (face.pose = pitch, yaw, roll).
"""

import threading

_apps = {}
_lock = threading.Lock()
MIN_DET_SCORE = 0.6


def get_app(with_pose: bool = False):
    with _lock:
        if with_pose not in _apps:
            import onnxruntime
            from insightface.app import FaceAnalysis

            gpu = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
            modules = ["detection", "recognition"] + (["landmark_3d_68"] if with_pose else [])
            app = FaceAnalysis(name="buffalo_l", allowed_modules=modules,
                               providers=(["CUDAExecutionProvider"] if gpu else []) + ["CPUExecutionProvider"])
            app.prepare(ctx_id=0 if gpu else -1, det_size=(640, 640))
            _apps[with_pose] = app
        return _apps[with_pose]


def faces(img, min_size: int = 40, with_pose: bool = False):
    """Khuôn mặt đủ rõ, đủ lớn trong ảnh BGR."""
    out = []
    for f in get_app(with_pose).get(img):
        w, h = f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1]
        if f.det_score >= MIN_DET_SCORE and min(w, h) >= min_size:
            out.append(f)
    return out
