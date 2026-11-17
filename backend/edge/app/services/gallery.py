"""
Kho vector khuôn mặt cục bộ của từng ca thi: data/gallery/exam_<id>.npz (ids, vectors đã chuẩn hoá).

Trung tâm giữ kho vector gốc (Qdrant). Biên chỉ tải vector của danh sách dự thi cho ca đang diễn ra và so khớp
bằng tích vô hướng (vài trăm vector -> không cần CSDL vector tại biên). File bị xoá khi ca thi kết thúc.
"""

import numpy as np

from backend.edge.app.core.config import settings

DIM = 512
_cache: dict[int, tuple[str, np.ndarray, np.ndarray]] = {}       # exam_id -> (roster_hash, ids, ma trận)


def _path(exam_id: int):
    return settings.data_dir / "gallery" / f"exam_{exam_id}.npz"


def exists(exam_id: int) -> bool:
    return _path(exam_id).exists()


def save(exam_id: int, students: list[dict]):
    """students: [{student_id, vectors: [[512 số], ...]}]"""
    ids, vecs = [], []
    for s in students:
        for v in s.get("vectors") or []:
            if len(v) == DIM:
                ids.append(int(s["student_id"]))
                vecs.append(v)
    mat = np.asarray(vecs, dtype=np.float32).reshape(-1, DIM)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat = mat / np.where(norms == 0, 1, norms)
    path = _path(exam_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, ids=np.asarray(ids, dtype=np.int64), vectors=mat)
    tmp.replace(path)                                 # ghi nguyên tử: worker không đọc phải file dở
    _cache.pop(exam_id, None)


def remove(exam_id: int):
    _path(exam_id).unlink(missing_ok=True)
    _cache.pop(exam_id, None)


def load(exam_id: int, roster_hash: str | None) -> tuple[np.ndarray, np.ndarray]:
    """-> (ids, ma trận vector). Cache theo roster_hash để worker không đọc đĩa mỗi khung hình."""
    hit = _cache.get(exam_id)
    if hit and hit[0] == roster_hash:
        return hit[1], hit[2]
    try:
        with np.load(_path(exam_id)) as f:
            ids, mat = f["ids"], f["vectors"]
    except (OSError, ValueError):
        ids, mat = np.zeros(0, dtype=np.int64), np.zeros((0, DIM), dtype=np.float32)
    _cache[exam_id] = (roster_hash, ids, mat)
    return ids, mat


def search(exam_id: int, roster_hash: str | None, embedding: np.ndarray, only_ids=None, limit: int = 2):
    """Top-k sinh viên KHÁC nhau theo cosine: [(student_id, điểm)]. only_ids: giới hạn tập ứng viên."""
    ids, mat = load(exam_id, roster_hash)
    if not len(ids):
        return []
    e = np.asarray(embedding, dtype=np.float32)
    sims = mat @ (e / (np.linalg.norm(e) or 1))
    best: dict[int, float] = {}
    for sid, sim in zip(ids.tolist(), sims.tolist()):
        if only_ids is not None and sid not in only_ids:
            continue
        if sim > best.get(sid, -1.0):
            best[sid] = sim
    return sorted(best.items(), key=lambda x: -x[1])[:limit]
