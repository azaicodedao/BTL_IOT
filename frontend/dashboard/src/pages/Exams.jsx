import { useState } from "react";
import { get, post } from "../api";
import { navigate } from "../router";
import { useAuth } from "../auth";
import { useLive } from "../live";
import { Badge, Card, Empty, ErrorText, EXAM_PHASE, Field, fmtDateTime, useLoad } from "../ui";

function CreateExam() {
  const [rooms] = useLoad(() => get("/rooms"));
  const [classes] = useLoad(() => get("/classes"));
  const [f, setF] = useState({ room_id: "", class_id: "", name: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const s = await post("/exam/sessions", { room_id: +f.room_id, class_id: +f.class_id, name: f.name || null });
      navigate(`exams/${s.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Mở ca thi mới">
      <p className="mb-3 text-sm text-slate-500">
        Mở ca thi sẽ bật camera ở cửa: AI đối chiếu khuôn mặt sinh viên với danh sách dự thi trước khi cho vào phòng.
        Khi đủ người, bấm <b>Bắt đầu làm bài</b> để chuyển sang giám sát gian lận.
      </p>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto] md:items-end">
        <Field label="Phòng thi">
          <select className="input" value={f.room_id} onChange={(e) => setF({ ...f, room_id: e.target.value })} required>
            <option value="">Chọn phòng</option>
            {rooms?.map((r) => <option key={r.id} value={r.id}>{r.code} · {r.name}</option>)}
          </select>
        </Field>
        <Field label="Danh sách dự thi">
          <select className="input" value={f.class_id} onChange={(e) => setF({ ...f, class_id: e.target.value })} required>
            <option value="">Chọn danh sách</option>
            {classes?.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name} ({c.student_count} SV)</option>)}
          </select>
        </Field>
        <Field label="Tên ca thi">
          <input className="input" value={f.name} placeholder="VD: Cuối kỳ IoT - Ca 1" onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <button className="btn-primary" disabled={busy}>{busy ? "Đang mở…" : "Mở ca thi"}</button>
      </form>
      <div className="mt-2"><ErrorText error={error} /></div>
    </Card>
  );
}

export default function Exams() {
  const { canOperate } = useAuth();
  const [sessions, reload] = useLoad(() => get("/exam/sessions?limit=50"));
  useLive("session", reload);

  return (
    <div className="space-y-4">
      {canOperate && <CreateExam />}
      <Card title="Các ca thi">
        {!sessions ? <Empty>Đang tải…</Empty> : !sessions.length ? <Empty>Chưa có ca thi nào</Empty> : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Ca thi</th><th>Phòng</th><th>Danh sách</th><th>Bắt đầu</th><th>Vào phòng</th><th>Giai đoạn</th><th /></tr></thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className="cursor-pointer" onClick={() => navigate(`exams/${s.id}`)}>
                    <td className="font-medium">#{s.id} {s.name || "Ca thi"}</td>
                    <td>{s.room_code}</td>
                    <td>{s.class_code || "–"}</td>
                    <td className="text-slate-500">{fmtDateTime(s.started_at)}</td>
                    <td className="tabular-nums">{s.admitted}/{s.roster_total}</td>
                    <td><Badge map={EXAM_PHASE} value={s.status} /></td>
                    <td className="text-right"><span className="text-blue-600">Mở →</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
