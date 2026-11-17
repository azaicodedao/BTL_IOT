import { useEffect, useMemo, useState } from "react";
import { get, patch, post } from "../api";
import { navigate } from "../router";
import { useAuth } from "../auth";
import { useLive } from "../live";
import { AsyncButton, AuthImage, Badge, Card, CHECKIN_STATUS, Empty, ErrorText, EVENT_TYPE, EXAM_PHASE, Field,
         fmtDateTime, fmtTime, LiveCamera, Modal, pct, REVIEW, Stat, useLoad } from "../ui";

const STEPS = [["checkin", "Kiểm tra vào phòng"], ["in_progress", "Đang thi"], ["closed", "Kết thúc"]];

function Stepper({ status }) {
  const idx = STEPS.findIndex(([k]) => k === status);
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm">
      {STEPS.map(([k, label], i) => (
        <li key={k} className="flex items-center gap-2">
          <span className={`grid h-6 w-6 place-items-center rounded-full text-xs font-bold
                           ${i < idx ? "bg-emerald-600 text-white" : i === idx ? "bg-blue-600 text-white ring-4 ring-blue-600/20" : "bg-slate-200 text-slate-500 dark:bg-slate-800"}`}>
            {i < idx ? "✓" : i + 1}
          </span>
          <span className={i === idx ? "font-semibold" : "text-slate-500"}>{label}</span>
          {i < STEPS.length - 1 && <span className="mx-1 h-px w-8 bg-slate-300 dark:bg-slate-700" />}
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ kết quả nhận diện ở cửa

function ResultCard({ result }) {
  if (!result) {
    return (
      <div className="grid min-h-[180px] place-items-center rounded-xl border-2 border-dashed border-slate-300 p-6 text-center text-slate-500 dark:border-slate-700">
        <div>
          <div className="text-3xl">🙂</div>
          <div className="mt-2 font-medium">Mời sinh viên nhìn thẳng vào camera</div>
          <div className="text-xs">Kết quả nhận diện sẽ hiện ở đây</div>
        </div>
      </div>
    );
  }
  const S = {
    admitted: ["bg-emerald-600", "MỜI VÀO PHÒNG", "✓"],
    already: ["bg-sky-600", "ĐÃ VÀO PHÒNG TRƯỚC ĐÓ", "↺"],
    confirming: ["bg-amber-500", "ĐANG XÁC NHẬN…", "…"],
    not_in_roster: ["bg-red-600", "KHÔNG CÓ TRONG DANH SÁCH DỰ THI", "✕"],
    unknown: ["bg-red-600", "KHÔNG NHẬN RA - NGƯỜI LẠ", "?"],
  }[result.kind];
  return (
    <div className={`rounded-xl p-5 text-white shadow-lg ${S[0]}`}>
      <div className="flex items-start gap-4">
        <div className="grid h-14 w-14 flex-none place-items-center rounded-full bg-white/20 text-3xl font-bold">{S[2]}</div>
        <div className="min-w-0">
          <div className="text-xs font-semibold tracking-wider opacity-90">{S[1]}</div>
          <div className="mt-1 truncate text-2xl font-bold">{result.full_name || (result.kind === "unknown" ? "Không xác định" : "")}</div>
          {result.student_code && <div className="text-lg opacity-95">MSSV {result.student_code}</div>}
          <div className="mt-1 text-xs opacity-90">
            {fmtTime(result.ts || Date.now())}{result.confidence != null && ` · độ giống ${pct(result.confidence)}`}
            {result.kind === "already" && result.previous_status && ` · trạng thái: ${CHECKIN_STATUS[result.previous_status]?.[0]}`}
          </div>
        </div>
      </div>
    </div>
  );
}

function ManualModal({ row, examId, onClose }) {
  const [status, setStatus] = useState(row.status === "admitted" ? "denied" : "admitted");
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  return (
    <Modal title={`${row.student_code} · ${row.full_name}`} onClose={() => onClose(false)}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        try {
          await post(`/exam/sessions/${examId}/checkin`, { student_id: row.student_id, status, note });
          onClose(true);
        } catch (err) {
          setError(err);
        }
      }}>
        <div className="grid grid-cols-2 gap-2">
          {[["admitted", "Cho vào phòng", "peer-checked:bg-emerald-600"], ["denied", "Từ chối", "peer-checked:bg-red-600"]].map(([v, l, c]) => (
            <label key={v}>
              <input type="radio" name="st" className="peer sr-only" checked={status === v} onChange={() => setStatus(v)} />
              <span className={`block cursor-pointer rounded-lg border border-slate-300 px-3 py-2 text-center font-medium peer-checked:text-white dark:border-slate-700 ${c}`}>{l}</span>
            </label>
          ))}
        </div>
        <Field label="Lý do (bắt buộc, ghi vào nhật ký)">
          <textarea className="input" rows={3} minLength={3} required value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="VD: Đối chiếu thẻ SV, camera không nhận do đeo khẩu trang" />
        </Field>
        <ErrorText error={error} />
        <button className="btn-primary w-full">Lưu</button>
      </form>
    </Modal>
  );
}

function CheckinPhase({ data, reload, result }) {
  const { canOperate } = useAuth();
  const { session: s, roster, attempts } = data;
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [manual, setManual] = useState(null);
  const [viewing, setViewing] = useState(null);

  const shown = roster.filter((r) => (filter === "all" || r.status === filter)
    && (!q || `${r.student_code} ${r.full_name}`.toLowerCase().includes(q.toLowerCase())));
  const admitted = roster.filter((r) => r.status === "admitted").length;
  const total = s.roster_total || roster.length;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(340px,2fr)]">
      <div className="space-y-4">
        <Card title={`Danh sách dự thi · ${s.class_code}`}
              actions={<>
                <input className="input w-44" placeholder="Tìm MSSV / tên" value={q} onChange={(e) => setQ(e.target.value)} />
                <select className="input w-40" value={filter} onChange={(e) => setFilter(e.target.value)}>
                  <option value="all">Tất cả</option><option value="waiting">Chưa đến</option>
                  <option value="admitted">Đã vào phòng</option><option value="denied">Từ chối</option><option value="absent">Vắng thi</option>
                </select>
              </>}>
          <div className="max-h-[420px] overflow-auto">
            <table className="table">
              <thead className="sticky top-0 bg-white dark:bg-slate-900"><tr><th>MSSV</th><th>Họ tên</th><th>Trạng thái</th><th>Giờ</th><th>Cách</th><th /></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.student_id}>
                    <td className="font-mono text-xs">{r.student_code}</td>
                    <td>{r.full_name}{r.note && <div className="text-xs text-slate-500">{r.note}</div>}
                      {r.status === "waiting" && r.face_samples === 0 && <div className="text-xs text-amber-600">Chưa đăng ký khuôn mặt</div>}</td>
                    <td><Badge map={CHECKIN_STATUS} value={r.status} /></td>
                    <td className="text-slate-500">{fmtTime(r.ts)}</td>
                    <td className="text-xs text-slate-500">{r.method === "manual" ? "Giám thị" : r.method ? `AI ${pct(r.confidence)}` : "–"}</td>
                    <td className="whitespace-nowrap text-right">
                      {r.has_frame && canOperate && <button className="btn-ghost btn-sm" onClick={() => setViewing({ path: `/exam/checkin/records/${r.id}/frame`, title: r.full_name })}>Ảnh</button>}
                      {canOperate && s.status !== "closed" && <button className="btn-ghost btn-sm" onClick={() => setManual(r)}>Xử lý tay</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!shown.length && <Empty>Không có sinh viên phù hợp</Empty>}
          </div>
        </Card>
      </div>

      <div className="space-y-4">
        <ResultCard result={result} />
        <Card>
          <div className="mb-2 flex items-end justify-between">
            <div><div className="text-3xl font-bold tabular-nums">{admitted}<span className="text-lg text-slate-400">/{total}</span></div>
              <div className="text-xs text-slate-500">sinh viên đã vào phòng</div></div>
            <div className="text-right text-xs text-slate-500">{s.rejected_attempts} lượt bị từ chối</div>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div className="h-full bg-emerald-500 transition-all" style={{ width: `${total ? (admitted / total) * 100 : 0}%` }} />
          </div>
        </Card>
        <Card title="Lượt bị từ chối ở cửa">
          {!attempts.length ? <Empty>Chưa có</Empty> : (
            <ul className="max-h-[360px] space-y-2 overflow-auto">
              {attempts.map((a) => (
                <li key={a.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-2 dark:border-slate-800">
                  <button className="h-14 w-20 flex-none overflow-hidden rounded-md" disabled={!a.has_frame || !canOperate}
                          onClick={() => setViewing({ path: `/exam/checkin/attempts/${a.id}/frame`, title: "Lượt bị từ chối" })}>
                    {a.has_frame && canOperate ? <AuthImage path={`/exam/checkin/attempts/${a.id}/frame`} className="h-full w-full object-cover" />
                                              : <div className="h-full w-full bg-slate-200 dark:bg-slate-800" />}
                  </button>
                  <div className="min-w-0 text-sm">
                    <div className="font-medium text-red-600 dark:text-red-400">
                      {a.result === "unknown" ? "Người lạ" : `${a.student_code} · ${a.full_name}`}</div>
                    <div className="text-xs text-slate-500">{a.result === "not_in_roster" ? "Không có trong danh sách dự thi" : "Không khớp ai trong hệ thống"} · {fmtTime(a.ts)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {manual && <ManualModal row={manual} examId={s.id} onClose={(c) => { setManual(null); c && reload(); }} />}
      {viewing && <Modal title={viewing.title} onClose={() => setViewing(null)} wide><AuthImage path={viewing.path} className="w-full rounded-lg" /></Modal>}
    </div>
  );
}

// ------------------------------------------------------------------ giám sát gian lận

function ReviewModal({ event, onClose }) {
  const [note, setNote] = useState("");
  const decide = async (decision) => {
    const out = await patch(`/exam/events/${event.id}`, { decision, note: note || null });
    // Quyết định đã lưu; còi do server biên bật nên có thể thất bại riêng (biên mất kết nối, phòng chưa có node còi)
    if (out.buzzer_error) window.alert(`Đã lưu quyết định nhưng chưa bật được còi: ${out.buzzer_error}`);
    onClose(true);
  };
  return (
    <Modal title={`Nghi vấn #${event.id}: ${EVENT_TYPE[event.type] || event.type}`} onClose={() => onClose(false)} wide>
      <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
        {event.has_frame ? <AuthImage path={`/exam/events/${event.id}/frame`} className="w-full rounded-lg" />
                         : <div className="grid aspect-video place-items-center rounded-lg bg-slate-200">Không có ảnh</div>}
        <div className="space-y-3">
          <div>
            <div className="text-lg font-semibold">{event.student_code ? `MSSV ${event.student_code}` : "Chưa rõ danh tính"}</div>
            <div className="text-sm text-slate-500">{fmtDateTime(event.ts)} · điểm nghi vấn {pct(event.score)}</div>
          </div>
          {event.detail && (
            <dl className="grid grid-cols-2 gap-1 text-xs">
              {Object.entries(event.detail).map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-slate-500">{k}</dt><dd className="font-mono">{JSON.stringify(v)}</dd></div>
              ))}
            </dl>
          )}
          <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            AI chỉ cảnh báo. Giám thị quyết định. Xác nhận sẽ bật còi liên tục trong phòng (tự tắt sau 2 phút hoặc khi bấm Tắt còi).
          </p>
          {event.review_status === "pending" ? <>
            <Field label="Ghi chú"><textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
            <div className="flex gap-2">
              <AsyncButton className="btn-danger flex-1" onClick={() => decide("confirmed")}>Xác nhận gian lận</AsyncButton>
              <AsyncButton className="btn-secondary flex-1" onClick={() => decide("dismissed")}>Bác bỏ</AsyncButton>
            </div>
          </> : <p><Badge map={REVIEW} value={event.review_status} /> {event.review_note}</p>}
        </div>
      </div>
    </Modal>
  );
}

function ExamPhase({ data, reload, alarm }) {
  const { canOperate } = useAuth();
  const { session: s, events, roster } = data;
  const [reviewing, setReviewing] = useState(null);
  const pending = events.filter((e) => e.review_status === "pending").length;
  const admitted = roster.filter((r) => r.status === "admitted").length;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(340px,2fr)]">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Đang dự thi" value={admitted} />
          <Stat label="Chờ duyệt" value={pending} tone={pending ? "amber" : ""} />
          <Stat label="Đã xác nhận" value={events.filter((e) => e.review_status === "confirmed").length} tone="red" />
          <Stat label="Bác bỏ" value={events.filter((e) => e.review_status === "dismissed").length} />
        </div>
      </div>
      <Card title="Nghi vấn gian lận (AI)">
        {!events.length ? <Empty>{s.status === "in_progress" ? "Chưa có nghi vấn: AI đang theo dõi" : "Không có nghi vấn"}</Empty> : (
          <ul className="max-h-[560px] space-y-2 overflow-auto">
            {events.map((e) => (
              <li key={e.id} className={`flex items-center gap-3 rounded-lg border p-2 ${e.review_status === "pending" ? "border-red-400 bg-red-50/50 dark:bg-red-500/5" : "border-slate-200 dark:border-slate-800"}`}>
                <button className="h-16 w-24 flex-none overflow-hidden rounded-md" onClick={() => setReviewing(e)}>
                  {e.has_frame && canOperate ? <AuthImage path={`/exam/events/${e.id}/frame`} className="h-full w-full object-cover" />
                                            : <div className="h-full w-full bg-slate-200 dark:bg-slate-800" />}
                </button>
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{EVENT_TYPE[e.type] || e.type}</div>
                  <div className="text-xs text-slate-500">{e.student_code || "chưa rõ"} · {fmtTime(e.ts)} · {pct(e.score)}</div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Badge map={REVIEW} value={e.review_status} />
                  {canOperate && <button className="btn-secondary btn-sm" onClick={() => setReviewing(e)}>{e.review_status === "pending" ? "Duyệt" : "Xem"}</button>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {reviewing && <ReviewModal event={reviewing} onClose={(c) => { setReviewing(null); c && reload(); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ trang

export default function ExamDetail({ id }) {
  const { canOperate } = useAuth();
  const [data, reload, error] = useLoad(() => get(`/exam/sessions/${id}`), [id]);
  const [devices, reloadDevices, deviceError] = useLoad(() => get("/devices"));
  const [result, setResult] = useState(null);
  const [alarm, setAlarm] = useState(false);
  const [tab, setTab] = useState(null);

  // Kết quả nhận diện ở cửa: hiện 6 giây rồi về trạng thái chờ
  useEffect(() => {
    if (!result) return;
    const t = setTimeout(() => setResult(null), result.kind === "confirming" ? 3000 : 6000);
    return () => clearTimeout(t);
  }, [result]);

  useLive("checkin", (m) => {
    if (m.session_id !== id) return;
    const d = m.data;
    if (m.type === "checkin.admitted") setResult({ kind: "admitted", ...d });
    else if (m.type === "checkin.rejected") setResult({ kind: d.result, ...d });
    else if (m.type === "checkin.seen") setResult((cur) => (cur?.kind === "admitted" && cur.student_id === d.student_id ? cur
                                                                   : { kind: d.state === "already" ? "already" : "confirming", ...d }));
    if (m.type !== "checkin.seen") reload();
  });
  useLive("exam", (m) => {
    if (m.type === "exam.alarm_on") setAlarm(true);
    else if (m.type === "exam.alarm_off") setAlarm(false);
    else if (m.session_id === id) reload();
  });
  useLive("session", (m) => m.data.id === id && reload());
  useLive("device", (m) => { if (m.type === "device.status") reloadDevices(); });

  const lcds = useMemo(() => (devices || []).filter((d) => d.room_id === data?.session.room_id && d.type === "actuator"), [devices, data]);
  const cameras = useMemo(() => (devices || []).filter((d) => d.room_id === data?.session.room_id
    && d.type === "camera" && d.state !== "decommissioned"), [devices, data]);

  if (error) return <ErrorText error={error} />;
  if (!data) return <Empty>Đang tải…</Empty>;
  const s = data.session;
  const view = tab || (s.status === "in_progress" ? "exam" : "checkin");
  const stopBuzzer = () => Promise.all(lcds.map((d) => post(`/devices/${d.device_id}/commands`, { action: "buzzer", pattern: "stop" })));

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <button className="btn-ghost btn-sm -ml-2 mb-1" onClick={() => navigate("exams")}>← Các ca thi</button>
            <h2 className="text-xl font-bold">{s.name || `Ca thi #${s.id}`}</h2>
            <div className="mt-1 text-sm text-slate-500">
              Phòng <b>{s.room_code}</b> · danh sách <b>{s.class_code}</b> ({s.roster_total} SV) · mở lúc {fmtDateTime(s.started_at)}
              {s.exam_started_at && <> · làm bài từ {fmtTime(s.exam_started_at)}</>}{s.ended_at && <> · kết thúc {fmtTime(s.ended_at)}</>}
            </div>
            <div className="mt-3"><Stepper status={s.status} /></div>
          </div>
          {canOperate && (
            <div className="flex flex-wrap gap-2">
              {lcds.length > 0 && s.status !== "closed" && <AsyncButton onClick={stopBuzzer}>Tắt còi</AsyncButton>}
              {s.status === "checkin" && (
                <AsyncButton className="btn-primary" confirm={`Kết thúc kiểm tra vào phòng (${s.admitted}/${s.roster_total} SV đã vào) và bắt đầu làm bài?`}
                             onClick={async () => { await post(`/exam/sessions/${id}/start`); setTab(null); reload(); }}>
                  Bắt đầu làm bài →</AsyncButton>
              )}
              {s.status !== "closed" && (
                <AsyncButton className="btn-danger" confirm="Kết thúc ca thi? SV chưa vào phòng sẽ bị ghi vắng thi."
                             onClick={async () => { await post(`/exam/sessions/${id}/close`); reload(); }}>Kết thúc ca thi</AsyncButton>
              )}
            </div>
          )}
        </div>
        {s.status !== "checkin" && (
          <div className="mt-4 flex gap-1 border-t border-slate-200 pt-3 dark:border-slate-800">
            {[["checkin", "Kiểm tra vào phòng"], ["exam", "Giám sát gian lận"]].map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={view === k ? "btn-primary btn-sm" : "btn-ghost btn-sm"}>{l}</button>
            ))}
          </div>
        )}
      </Card>
      <Card title={`Camera phòng ${s.room_code} · ${cameras.length}`}>
        {deviceError ? <ErrorText error={deviceError} /> : s.status === "closed" ? <Empty>Ca thi đã kết thúc.</Empty>
          : !devices ? <Empty>Đang tải camera…</Empty> : !cameras.length ? <Empty>Phòng chưa có camera.</Empty> : (
          <div className={`grid gap-4 ${cameras.length > 1 ? "lg:grid-cols-2" : ""}`}>
            {cameras.map(camera => {
              const role = camera.config?.role || "both";
              const label = { checkin: "Điểm danh", exam: "Giám sát", both: "Điểm danh + giám sát" }[role];
              const selected = camera.device_id === (s.status === "in_progress" ? s.exam_camera : s.checkin_camera);
              return <div key={camera.device_id} className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-sm"><b>{label}</b><span className="text-slate-500">{camera.device_id}</span>
                  {selected && <span className="badge-green">Camera chính của giai đoạn</span>}</div>
                <LiveCamera deviceId={camera.device_id} label={`${label} · ${camera.device_id}`}
                  overlay={alarm && <div className="absolute inset-x-0 bottom-0 animate-pulse bg-red-600/90 py-2 text-center font-bold text-white">CÒI ĐANG KÊU</div>} />
              </div>;
            })}
          </div>
        )}
      </Card>
      {view === "checkin" ? <CheckinPhase data={data} reload={reload} result={result} />
                          : <ExamPhase data={data} reload={reload} alarm={alarm} />}
    </div>
  );
}
