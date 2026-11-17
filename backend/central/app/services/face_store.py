"""
Kho vector khuôn mặt gốc trong Qdrant (ADR-04): collection face_embeddings, 512 chiều, cosine,
payload {student_id, student_code, edge_id, created_at}.

Trung tâm chỉ nhận VECTOR do server biên tính (ảnh ở lại biên). Vector của danh sách dự thi được phát xuống biên
khi mở ca thi để AI so khớp tại chỗ.
"""

import hashlib
import threading

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from backend.central.app.core.config import settings
from backend.shared.db import utcnow

DIM = 512
_client: QdrantClient | None = None
_lock = threading.Lock()


def client() -> QdrantClient:
    global _client
    with _lock:
        if _client is None:
            url = settings.qdrant_url
            if url.startswith("http"):
                _client = QdrantClient(url=url, timeout=10)
            elif url == ":memory:":
                _client = QdrantClient(location=":memory:")
            else:
                _client = QdrantClient(path=url)                   # Qdrant nhúng, lưu ra thư mục
            names = {c.name for c in _client.get_collections().collections}
            if settings.qdrant_collection not in names:
                _client.create_collection(settings.qdrant_collection,
                                          vectors_config=qm.VectorParams(size=DIM, distance=qm.Distance.COSINE))
                _client.create_payload_index(settings.qdrant_collection, "student_id",
                                             field_schema=qm.PayloadSchemaType.INTEGER)
        return _client


def _only(student_ids):
    return qm.Filter(must=[qm.FieldCondition(key="student_id", match=qm.MatchAny(any=[int(i) for i in student_ids]))])


def _scroll(student_ids, with_vectors: bool):
    """Mọi điểm của các sinh viên (lặp theo trang)."""
    if not student_ids:
        return []
    out, offset = [], None
    while True:
        points, offset = client().scroll(settings.qdrant_collection, scroll_filter=_only(student_ids), limit=256,
                                         offset=offset, with_payload=True, with_vectors=with_vectors)
        out.extend(points)
        if offset is None:
            return out


def add_vectors(student_id: int, student_code: str, edge_id: str, samples: list[tuple[str, list[float]]]) -> int:
    """samples: [(sample_id, vector 512 chiều)]. Trả tổng số mẫu của sinh viên. Raises ValueError nếu sai kích thước."""
    points = []
    for sample_id, vector in samples:
        v = np.asarray(vector, dtype=np.float64)
        if v.shape != (DIM,) or not np.isfinite(v).all() or not np.linalg.norm(v):
            raise ValueError("Vector khuôn mặt phải có 512 số thực")
        points.append(qm.PointStruct(id=sample_id, vector=(v / np.linalg.norm(v)).tolist(),
                                     payload={"student_id": student_id, "student_code": student_code,
                                              "edge_id": edge_id, "created_at": utcnow().isoformat()}))
    client().upsert(settings.qdrant_collection, points=points, wait=True)
    return count(student_id)


def list_samples(student_id: int) -> list[dict]:
    return sorted(({"id": str(p.id), "created_at": p.payload.get("created_at"), "edge_id": p.payload.get("edge_id")}
                   for p in _scroll([student_id], False)), key=lambda x: x["created_at"] or "")


def delete_point(student_id: int, point_id: str) -> int:
    client().delete(settings.qdrant_collection, points_selector=qm.PointIdsList(points=[point_id]), wait=True)
    return count(student_id)


def delete_student(student_id: int):
    client().delete(settings.qdrant_collection, points_selector=qm.FilterSelector(filter=_only([student_id])),
                    wait=True)


def count(student_id: int) -> int:
    return client().count(settings.qdrant_collection, count_filter=_only([student_id]), exact=True).count


def signature(student_ids: list[int]) -> str:
    """Dấu vân tay của tập vector (không tải vector): đổi khi thêm/xoá mẫu -> biên biết phải tải lại."""
    ids = sorted(f"{p.payload['student_id']}:{p.id}" for p in _scroll(student_ids, False))
    return hashlib.sha256("|".join(ids).encode()).hexdigest()


def vectors_by_student(student_ids: list[int]) -> dict[int, list[list[float]]]:
    out: dict[int, list] = {}
    for p in _scroll(student_ids, True):
        out.setdefault(p.payload["student_id"], []).append(p.vector)
    return out


def search(vector, student_ids: list[int] | None = None, limit: int = 2):
    """Top-k SINH VIÊN khác nhau: [(student_id, mã, điểm cosine)]."""
    v = np.asarray(vector, dtype=np.float64)
    if v.shape != (DIM,) or not np.linalg.norm(v):
        raise ValueError("Vector khuôn mặt phải có 512 số thực")
    hits = client().search(settings.qdrant_collection, query_vector=(v / np.linalg.norm(v)).tolist(),
                           limit=limit * 5, query_filter=_only(student_ids) if student_ids is not None else None)
    best = {}
    for h in hits:                          # 1 SV có nhiều mẫu: top-2 phải là 2 SV KHÁC nhau
        sid = h.payload["student_id"]
        if sid not in best or h.score > best[sid][1]:
            best[sid] = (h.payload.get("student_code"), h.score)
    return sorted(((sid, code, score) for sid, (code, score) in best.items()), key=lambda x: -x[2])[:limit]
