import { useState } from "react";
import { get, patch, post, put } from "../api";
import { useAuth } from "../auth";
import { useLive } from "../live";
import { AsyncButton, Badge, Card, Empty, ErrorText, fmtDateTime, SEVERITY, useLoad } from "../ui";

const KIND = {
  temperature_c: "Nhiệt độ", humidity_pct: "Độ ẩm", device_offline: "Thiết bị offline",
  device_fault: "Thiết bị lỗi", suspicion: "Nghi vấn gian lận",
};
const STATE = { open: ["Mới", "red"], acked: ["Đã xác nhận", "amber"], resolved: ["Đã xử lý", "green"] };

function Rules() {
  const { isAdmin } = useAuth();
  const [rules, reload] = useLoad(() => get("/alert-rules"));
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const save = async (e) => {
    e.preventDefault();
    try {
      const { id, ...body } = { ...draft, threshold: +draft.threshold, duration_s: +draft.duration_s };
      id ? await put(`/alert-rules/${id}`, body) : await post("/alert-rules", body);
      setDraft(null);
      setError(null);
      reload();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <Card title="Quy tắc cảnh báo vi khí hậu"
          actions={isAdmin && <button className="btn-secondary btn-sm" onClick={() => setDraft({ metric: "temperature_c", operator: ">", threshold: 30, duration_s: 60, severity: "medium", enabled: true, message: "" })}>Thêm quy tắc</button>}>
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th>Chỉ số</th><th>Điều kiện</th><th>Kéo dài</th><th>Mức</th><th>Khuyến nghị (LCD)</th><th>Bật</th><th /></tr></thead>
          <tbody>
            {rules?.map((r) => (
              <tr key={r.id}>
                <td>{KIND[r.metric]}</td><td>{r.operator} {r.threshold}{r.metric === "temperature_c" ? "°C" : "%"}</td>
                <td>{r.duration_s}s</td><td><Badge map={SEVERITY} value={r.severity} /></td><td>{r.message}</td>
                <td>{r.enabled ? "✓" : "–"}</td>
                <td>{isAdmin && <button className="btn-ghost btn-sm" onClick={() => setDraft({ ...r, message: r.message || "" })}>Sửa</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {draft && (
        <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={save}>
          <select className="input w-32" value={draft.metric} onChange={(e) => setDraft({ ...draft, metric: e.target.value })}>
            <option value="temperature_c">Nhiệt độ</option><option value="humidity_pct">Độ ẩm</option></select>
          <select className="input w-20" value={draft.operator} onChange={(e) => setDraft({ ...draft, operator: e.target.value })}>
            {[">", ">=", "<", "<="].map((o) => <option key={o}>{o}</option>)}</select>
          <input className="input w-24" type="number" step="0.5" value={draft.threshold} onChange={(e) => setDraft({ ...draft, threshold: e.target.value })} />
          <input className="input w-24" type="number" min="0" title="Kéo dài (giây)" value={draft.duration_s} onChange={(e) => setDraft({ ...draft, duration_s: e.target.value })} />
          <select className="input w-32" value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}>
            {["low", "medium", "high"].map((s) => <option key={s} value={s}>{SEVERITY[s][0]}</option>)}</select>
          <input className="input min-w-[200px] flex-1" maxLength={40} placeholder="Khuyến nghị không dấu" value={draft.message} onChange={(e) => setDraft({ ...draft, message: e.target.value })} />
          <label className="inline-flex items-center gap-1 text-sm"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> Bật</label>
          <button className="btn-primary">Lưu</button>
          <button type="button" className="btn-ghost" onClick={() => setDraft(null)}>Huỷ</button>
          <ErrorText error={error} />
        </form>
      )}
    </Card>
  );
}

export default function Alerts() {
  const { canOperate } = useAuth();
  const [filter, setFilter] = useState("active");
  const [alerts, reload] = useLoad(() => get(`/alerts?limit=300${filter ? `&state=${filter}` : ""}`), [filter]);
  useLive("alert", reload);
  return (
    <div className="space-y-4">
      <Card title="Cảnh báo" actions={
        <select className="input w-40" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="active">Đang mở</option><option value="resolved">Đã xử lý</option><option value="">Tất cả</option>
        </select>}>
        {!alerts?.length ? <Empty>Không có cảnh báo</Empty> : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Thời gian</th><th>Loại</th><th>Mức</th><th>Nội dung</th><th>Trạng thái</th><th /></tr></thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td className="whitespace-nowrap text-slate-500">{fmtDateTime(a.ts)}</td>
                    <td>{KIND[a.kind] || a.kind}</td><td><Badge map={SEVERITY} value={a.severity} /></td>
                    <td>{a.message}</td><td><Badge map={STATE} value={a.state} /></td>
                    <td className="whitespace-nowrap text-right">
                      {canOperate && a.state === "open" && <AsyncButton className="btn-ghost btn-sm" onClick={async () => { await patch(`/alerts/${a.id}`, { state: "acked" }); reload(); }}>Xác nhận</AsyncButton>}
                      {canOperate && a.state !== "resolved" && <AsyncButton className="btn-secondary btn-sm" onClick={async () => { await patch(`/alerts/${a.id}`, { state: "resolved" }); reload(); }}>Đã xử lý</AsyncButton>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Rules />
    </div>
  );
}
