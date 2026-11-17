import { useEffect, useState } from "react";
import { get, patch, post, postToEdge, put, request } from "../api";
import { AsyncButton, AuthImage, Card, Empty, ErrorText, Field, fmtDateTime, Modal, useLoad } from "../ui";

function ClassPicker({ classes, value, onChange }) {
  if (!classes?.length) return <span className="text-xs text-slate-500">Chưa có danh sách nào: tạo ở trên trước</span>;
  const toggle = (id) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <div className="flex flex-wrap gap-2">
      {classes.map((c) => (
        <label key={c.id} className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition
                                      ${value.includes(c.id) ? "border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300" : "border-slate-300 dark:border-slate-700"}`}>
          <input type="checkbox" className="accent-blue-600" checked={value.includes(c.id)} onChange={() => toggle(c.id)} /> {c.code}
        </label>
      ))}
    </div>
  );
}

function FaceModal({ student, onClose }) {
  const [samples, reload, loadError] = useLoad(() => get(`/students/${student.id}/face-samples`), [student.id]);
  const [files, setFiles] = useState([]);
  const [previews, setPreviews] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const [big, setBig] = useState(null);

  useEffect(() => {
    const urls = [...files].map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach(URL.revokeObjectURL);
  }, [files]);

  const upload = async (e) => {
    e.preventDefault();
    const form = new FormData();
    [...files].forEach((f) => form.append("files", f));
    setBusy(true);
    try {
      // Ảnh đi thẳng tới server biên (vé do trung tâm cấp); biên chỉ gửi vector lên trung tâm
      const { upload_url } = await post(`/students/${student.id}/face-ticket`);
      setResult(await postToEdge(upload_url, form));
      await post(`/students/${student.id}/face-refresh`);
      setFiles([]);
      e.target.reset();
      setError(null);
      setChanged(true);
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id) => {
    await request(`/students/${student.id}/face-samples/${id}`, { method: "DELETE" });
    setChanged(true);
    reload();
  };

  return (
    <Modal title={`Khuôn mặt: ${student.student_code} · ${student.full_name}`} onClose={() => onClose(changed)} wide>
      <div className="space-y-4">
        <h3 className="font-semibold">Mẫu đã đăng ký ({samples?.length ?? "…"})</h3>
        <ErrorText error={loadError} />
        {samples && !samples.length && <Empty>Chưa có mẫu khuôn mặt nào</Empty>}
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
          {samples?.map((s) => (
            <figure key={s.id} className="space-y-1">
              {s.has_image ? (
                <button className="block aspect-square w-full overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700" onClick={() => setBig(s)}>
                  <AuthImage path={`/students/${student.id}/face-samples/${s.id}/image`} className="h-full w-full object-cover" />
                </button>
              ) : <div className="grid aspect-square place-items-center rounded-lg bg-slate-100 p-2 text-center text-xs text-slate-500 dark:bg-slate-800">Không rõ server biên giữ ảnh</div>}
              <figcaption className="flex items-center justify-between text-xs text-slate-500">
                {fmtDateTime(s.created_at).split(" ")[1] || ""}
                <AsyncButton className="btn-ghost btn-sm text-red-600" confirm="Xoá mẫu khuôn mặt này?" onClick={() => remove(s.id)}>Xoá</AsyncButton>
              </figcaption>
            </figure>
          ))}
        </div>
        <p className="text-xs text-slate-500">Mỗi lượt xem ảnh khuôn mặt đều được ghi vào nhật ký (dữ liệu nhạy cảm).</p>

        <h3 className="font-semibold">Thêm mẫu</h3>
        {!student.consent_at ? (
          <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">Sinh viên chưa đồng ý thu thập khuôn mặt: bấm "Ghi nhận đồng ý" ở bảng trước.</p>
        ) : (
          <form onSubmit={upload} className="space-y-3">
            <p className="text-xs text-slate-500">3-5 ảnh rõ mặt (nhìn thẳng, nghiêng nhẹ, đủ sáng), mỗi ảnh 1 người. Ảnh được xử lý và lưu tại server biên; trung tâm chỉ nhận vector đặc trưng.</p>
            <input type="file" accept="image/*" multiple required onChange={(e) => setFiles(e.target.files)}
                   className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-blue-600 file:px-3 file:py-1.5 file:text-white" />
            {previews.length > 0 && <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
              {previews.map((u) => <img key={u} src={u} alt="" className="aspect-square w-full rounded-lg object-cover" />)}</div>}
            <button className="btn-primary" disabled={busy || !files.length}>{busy ? "Đang phát hiện khuôn mặt…" : `Tải lên ${files.length || ""} ảnh`}</button>
          </form>
        )}
        {result && <p className={result.added ? "text-sm text-emerald-600" : "text-sm text-red-600"}>Đã thêm {result.added} mẫu.
          {result.rejected.length > 0 && ` Bỏ qua: ${result.rejected.map((r) => `${r.file} (${r.reason})`).join(", ")}`}</p>}
        <ErrorText error={error} />
      </div>
      {big && <Modal title={`${student.student_code} · ${fmtDateTime(big.created_at)}`} onClose={() => setBig(null)}>
        <AuthImage path={`/students/${student.id}/face-samples/${big.id}/image`} className="w-full rounded-lg" /></Modal>}
    </Modal>
  );
}

function StudentClassesModal({ student, classes, onClose }) {
  const [value, setValue] = useState(student.classes.map((c) => c.id));
  const [error, setError] = useState(null);
  return (
    <Modal title={`Danh sách thi của ${student.student_code} · ${student.full_name}`} onClose={() => onClose(false)}>
      <div className="space-y-4">
        <ClassPicker classes={classes} value={value} onChange={setValue} />
        <ErrorText error={error} />
        <AsyncButton className="btn-primary w-full" onClick={async () => {
          try {
            await put(`/students/${student.id}/classes`, { class_ids: value });
            onClose(true);
          } catch (err) {
            setError(err);
          }
        }}>Lưu</AsyncButton>
      </div>
    </Modal>
  );
}

function ClassModal({ klass, onClose }) {
  const [detail] = useLoad(() => get(`/classes/${klass.id}`), [klass.id]);
  const [all] = useLoad(() => get("/students"));
  const [picked, setPicked] = useState(null);
  const [q, setQ] = useState("");
  const ids = picked ?? new Set(detail?.students.map((s) => s.id));
  const toggle = (id) => {
    const next = new Set(ids);
    next.has(id) ? next.delete(id) : next.add(id);
    setPicked(next);
  };
  const shown = (all || []).filter((s) => !q || `${s.student_code} ${s.full_name}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <Modal title={`Danh sách ${klass.code} · ${klass.name}`} onClose={() => onClose(false)}>
      {!all || !detail ? <Empty>Đang tải…</Empty> : (
        <div className="space-y-3">
          <p className="text-sm text-slate-500">Tích chọn sinh viên dự thi. Đang chọn {ids.size} / {all.length}.</p>
          <input className="input" placeholder="Lọc theo MSSV / tên" value={q} onChange={(e) => setQ(e.target.value)} />
          <ul className="max-h-80 space-y-1 overflow-auto">
            {shown.map((s) => (
              <li key={s.id}><label className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800">
                <input type="checkbox" className="accent-blue-600" checked={ids.has(s.id)} onChange={() => toggle(s.id)} />
                <span className="font-mono text-xs">{s.student_code}</span> {s.full_name}
                {s.face_samples === 0 && <span className="badge-amber ml-auto">chưa có mặt</span>}
              </label></li>
            ))}
          </ul>
          <AsyncButton className="btn-primary w-full" onClick={async () => { await put(`/classes/${klass.id}/students`, { student_ids: [...ids] }); onClose(true); }}>
            Lưu ({ids.size} SV)</AsyncButton>
        </div>
      )}
    </Modal>
  );
}

export default function People() {
  const [classes, reloadClasses] = useLoad(() => get("/classes"));
  const [q, setQ] = useState("");
  const [students, reloadStudents] = useLoad(() => get(`/students${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  const [cf, setCf] = useState({ code: "", name: "" });
  const [sf, setSf] = useState({ student_code: "", full_name: "", consent: false, class_ids: [] });
  const [classError, setClassError] = useState(null);
  const [studentError, setStudentError] = useState(null);
  const [editingClass, setEditingClass] = useState(null);
  const [editingStudent, setEditingStudent] = useState(null);
  const [face, setFace] = useState(null);
  const reloadAll = () => {
    reloadClasses();
    reloadStudents();
  };

  return (
    <div className="space-y-4">
      <Card title="Danh sách dự thi (lớp)">
        <form className="mb-4 grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await post("/classes", cf);
            setCf({ code: "", name: "" });
            setClassError(null);
            reloadClasses();
          } catch (err) {
            setClassError(err);
          }
        }}>
          <Field label="Mã"><input className="input" value={cf.code} required placeholder="IOT01" onChange={(e) => setCf({ ...cf, code: e.target.value })} /></Field>
          <Field label="Tên"><input className="input" value={cf.name} required placeholder="Lập trình IoT - Nhóm 1" onChange={(e) => setCf({ ...cf, name: e.target.value })} /></Field>
          <button className="btn-primary">Tạo danh sách</button>
          <div className="md:col-span-3"><ErrorText error={classError} /></div>
        </form>
        {!classes?.length ? <Empty>Chưa có danh sách</Empty> : (
          <table className="table">
            <thead><tr><th>Mã</th><th>Tên</th><th>Sĩ số</th><th /></tr></thead>
            <tbody>{classes.map((c) => (
              <tr key={c.id}><td className="font-medium">{c.code}</td><td>{c.name}</td><td>{c.student_count}</td>
                <td className="text-right"><button className="btn-secondary btn-sm" onClick={() => setEditingClass(c)}>Danh sách SV</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      <Card title="Sinh viên" actions={<input className="input w-56" placeholder="Tìm MSSV / tên" value={q} onChange={(e) => setQ(e.target.value)} />}>
        <form className="mb-4 space-y-3 border-b border-slate-200 pb-4 dark:border-slate-800" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await post("/students", sf);
            setSf({ student_code: "", full_name: "", consent: false, class_ids: [] });
            setStudentError(null);
            reloadAll();
          } catch (err) {
            setStudentError(err);
          }
        }}>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="MSSV"><input className="input" value={sf.student_code} required onChange={(e) => setSf({ ...sf, student_code: e.target.value })} /></Field>
            <Field label="Họ tên"><input className="input" value={sf.full_name} required onChange={(e) => setSf({ ...sf, full_name: e.target.value })} /></Field>
          </div>
          <Field label="Thuộc danh sách dự thi"><ClassPicker classes={classes} value={sf.class_ids} onChange={(class_ids) => setSf({ ...sf, class_ids })} /></Field>
          <div className="flex flex-wrap items-center gap-3">
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" className="accent-blue-600" checked={sf.consent}
                                                                              onChange={(e) => setSf({ ...sf, consent: e.target.checked })} />
              Sinh viên đã đồng ý thu thập khuôn mặt</label>
            <button className="btn-primary">Thêm sinh viên</button>
          </div>
          <ErrorText error={studentError} />
        </form>

        {!students?.length ? <Empty>Chưa có sinh viên</Empty> : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>MSSV</th><th>Họ tên</th><th>Danh sách thi</th><th>Đồng ý</th><th>Mẫu mặt</th><th /></tr></thead>
              <tbody>{students.map((s) => (
                <tr key={s.id}>
                  <td className="font-mono text-xs">{s.student_code}</td><td>{s.full_name}</td>
                  <td><div className="flex flex-wrap items-center gap-1">
                    {s.classes.length ? s.classes.map((c) => <span key={c.id} className="badge-blue">{c.code}</span>) : <span className="badge-red">Chưa có</span>}
                    <button className="btn-ghost btn-sm" onClick={() => setEditingStudent(s)}>Sửa</button></div></td>
                  <td className="text-xs">{s.consent_at ? fmtDateTime(s.consent_at) : (
                    <AsyncButton className="btn-secondary btn-sm" onClick={async () => { await patch(`/students/${s.id}`, { consent: true }); reloadStudents(); }}>Ghi nhận đồng ý</AsyncButton>)}</td>
                  <td>{s.face_samples > 0 ? <span className="badge-green">{s.face_samples} mẫu</span> : <span className="badge-amber">chưa có</span>}</td>
                  <td className="whitespace-nowrap text-right">
                    <button className="btn-secondary btn-sm" onClick={() => setFace(s)}>Khuôn mặt</button>
                    {s.face_samples > 0 && <AsyncButton className="btn-ghost btn-sm text-red-600" confirm={`Xoá toàn bộ dữ liệu khuôn mặt của ${s.student_code} (rút đồng ý)?`}
                                                        onClick={async () => { await request(`/students/${s.id}/face-data`, { method: "DELETE" }); reloadStudents(); }}>Rút đồng ý</AsyncButton>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {editingClass && <ClassModal klass={editingClass} onClose={(c) => { setEditingClass(null); c && reloadAll(); }} />}
      {editingStudent && <StudentClassesModal student={editingStudent} classes={classes} onClose={(c) => { setEditingStudent(null); c && reloadAll(); }} />}
      {face && <FaceModal student={face} onClose={(c) => { setFace(null); c && reloadStudents(); }} />}
    </div>
  );
}
