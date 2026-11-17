import { useState } from "react";
import { download, get, put } from "../api";
import { useAuth } from "../auth";
import { useLive } from "../live";
import { ago, Badge, Card, CMD_STATUS, Dot, Empty, ErrorText, Field, fmtDateTime, Modal, STATE_LABEL, useLoad } from "../ui";

const ROLE = { checkin: "Camera cửa (kiểm tra vào phòng)", exam: "Camera phòng thi (giám sát gian lận)", both: "Cả hai" };
const isPhone = (d) => /phone|android|ip webcam|virtual/i.test(`${d.device_id} ${d.hw_version || ""}`);

/** Server biên: nơi thiết bị tự đăng ký, được gán phòng và quản lý vòng đời. Trung tâm chỉ hiển thị + dẫn link. */
function EdgeServers() {
  const [edges, reload, error] = useLoad(() => get("/edges"));
  useLive("edge", reload);
  return (  
    <Card title="Server biên">
      <p className="mb-3 text-sm text-slate-500">
        Thiết bị mới tự báo danh với server biên trong cùng mạng. Đăng ký, gán phòng, tạm khoá, thu hồi thực hiện ở
        bảng điều khiển của server biên đó — không cần nạp lại firmware hay chép khoá.
      </p>
      <ErrorText error={error} />
      {edges && !edges.length && <Empty>Chưa có server biên nào kết nối. Chạy server biên và kiểm tra EDGE_CENTRAL_URL / EDGE_CENTRAL_TOKEN.</Empty>}
      {edges?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>Server biên</th><th>Kênh điều khiển</th><th>Thiết bị</th><th>Đồng bộ gần nhất</th><th /></tr></thead>
            <tbody>
              {edges.map((e) => (
                <tr key={e.edge_id}>
                  <td><div className="font-medium">{e.edge_id}</div><div className="text-xs text-slate-500">{e.name}</div></td>
                  <td><span className="inline-flex items-center gap-1.5"><Dot on={e.online} /> {e.online ? "đang kết nối" : "mất kết nối"}</span></td>
                  <td>{e.devices}</td>
                  <td className="text-xs text-slate-500">{ago(e.last_seen)}</td>
                  <td className="text-right">
                    {e.public_url && <a className="btn-secondary btn-sm" href={e.public_url} target="_blank" rel="noreferrer">Mở bảng điều khiển biên</a>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function DeviceDetail({ device, onClose }) {
  const { canOperate } = useAuth();
  const [cmds, reloadCmds, cmdError] = useLoad(() => (device.type === "actuator" ? get(`/devices/${device.device_id}/commands?limit=30`) : []), [device.device_id]);
  const [cfg, setCfg] = useState({ ...device.config });
  const [error, setError] = useState(null);
  useLive("command", (m) => m.data.device_id === device.device_id && reloadCmds());

  const saveConfig = async (e) => {
    e.preventDefault();
    try {
      const body = {};
      for (const k of ["telemetry_interval_s", "heartbeat_interval_s", "frame_interval_s"]) if (cfg[k] !== undefined && cfg[k] !== "") body[k] = +cfg[k];
      if (device.type === "camera") {
        body.role = cfg.role || "both";
        if (cfg.stream_url) body.stream_url = cfg.stream_url;
      }
      await put(`/devices/${device.device_id}/config`, body);
      setError(null);
      onClose();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <Modal title={`${device.device_id} · ${device.hw_version || device.type}`} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Badge map={STATE_LABEL} value={device.state} />
          <span className="inline-flex items-center gap-1.5"><Dot on={device.online} /> {device.online ? "online" : "offline"}</span>
          <span className="text-slate-500">phòng {device.room_code || "–"} · server biên {device.edge_id || "–"} · firmware {device.fw_version || "–"} · RSSI {device.rssi ?? "–"} dBm · {ago(device.last_seen)}</span>
        </div>
        {!device.edge_online && (
          <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            Server biên {device.edge_id || ""} đang mất kết nối với trung tâm: trạng thái bên trên có thể đã cũ, chưa gửi được cấu hình / lệnh.</p>
        )}

        {canOperate && !["registered", "decommissioned"].includes(device.state) && (
          <form className="grid gap-3 md:grid-cols-3" onSubmit={saveConfig}>
            {device.type === "camera" && <>
              <Field label="Vai trò camera" className="md:col-span-2">
                <select className="input" value={cfg.role || "both"} onChange={(e) => setCfg({ ...cfg, role: e.target.value })}>
                  {Object.entries(ROLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
              <Field label="Chu kỳ gửi hình (s)"><input className="input" type="number" min="0.1" step="0.1" value={cfg.frame_interval_s ?? ""}
                                                        onChange={(e) => setCfg({ ...cfg, frame_interval_s: e.target.value })} /></Field>
              {isPhone(device) && <Field label="URL video điện thoại (ghi chú)" className="md:col-span-2">
                <input className="input" value={cfg.stream_url || ""} placeholder="http://192.168.1.30:8080/video"
                       onChange={(e) => setCfg({ ...cfg, stream_url: e.target.value })} /></Field>}
              {!isPhone(device) && <Field label="Telemetry (s)"><input className="input" type="number" min="5" value={cfg.telemetry_interval_s ?? ""}
                                                                      onChange={(e) => setCfg({ ...cfg, telemetry_interval_s: e.target.value })} /></Field>}
            </>}
            <Field label="Heartbeat (s)"><input className="input" type="number" min="5" value={cfg.heartbeat_interval_s ?? ""}
                                                onChange={(e) => setCfg({ ...cfg, heartbeat_interval_s: e.target.value })} /></Field>
            <div className="flex items-end"><button className="btn-primary w-full">Lưu cấu hình</button></div>
            <div className="md:col-span-3"><ErrorText error={error} /></div>
          </form>
        )}

        {device.type === "camera" && !isPhone(device) && canOperate && (
          <button className="btn-secondary btn-sm" onClick={() => download(`/export/telemetry.csv?device_id=${device.device_id}`, `telemetry_${device.device_id}.csv`)}>
            Xuất CSV nhiệt độ 24h</button>
        )}

        {device.type === "actuator" && (
          <div>
            <h3 className="mb-2 font-semibold">Lịch sử lệnh (lưu tại server biên)</h3>
            <ErrorText error={cmdError} />
            {!cmdError && !cmds?.length ? <Empty>Chưa có lệnh</Empty> : cmds?.length > 0 && (
              <div className="max-h-72 overflow-auto">
                <table className="table">
                  <thead><tr><th>Thời gian</th><th>Lệnh</th><th>Trạng thái</th><th>Lần gửi</th><th>ACK sau</th><th>Nguồn</th></tr></thead>
                  <tbody>
                    {cmds.map((c) => (
                      <tr key={c.cmd_id}>
                        <td className="text-slate-500">{fmtDateTime(c.ts_created)}</td>
                        <td>{c.action} {c.params?.pattern || c.params?.text}</td>
                        <td><Badge map={CMD_STATUS} value={c.status} /></td>
                        <td>{c.attempts}</td>
                        <td>{c.ts_ack && c.ts_sent ? `${new Date(c.ts_ack) - new Date(c.ts_sent)} ms` : "–"}</td>
                        <td className="text-xs">{c.issued_by || "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

export default function Devices() {
  const [devices, reload, error] = useLoad(() => get("/devices"));
  const [detail, setDetail] = useState(null);
  useLive("device", (m) => m.type === "device.status" && reload());
  useLive("edge", reload);

  return (
    <div className="space-y-4">
      <EdgeServers />
      <Card title="Thiết bị">
        <ErrorText error={error} />
        {devices && !devices.length && <Empty>Chưa có thiết bị. Cắm nguồn thiết bị, cấu hình Wi-Fi rồi duyệt trên bảng điều khiển của server biên.</Empty>}
        {devices?.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Thiết bị</th><th>Loại / vai trò</th><th>Phòng</th><th>Server biên</th><th>Trạng thái</th><th>Kết nối</th><th>Firmware</th><th /></tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.device_id}>
                    <td><div className="font-medium">{d.device_id}</div><div className="text-xs text-slate-500">{d.hw_version}</div></td>
                    <td className="text-xs">{d.type === "camera" ? ROLE[d.config?.role || "both"] : "LCD + còi"}</td>
                    <td>{d.room_code || "–"}</td>
                    <td className="text-xs">{d.edge_id || "–"}</td>
                    <td><Badge map={STATE_LABEL} value={d.state} /></td>
                    <td><span className="inline-flex items-center gap-1.5"><Dot on={d.online && d.edge_online} /> <span className="text-xs text-slate-500">{ago(d.last_seen)}</span></span></td>
                    <td className="text-xs">{d.fw_version || "–"}</td>
                    <td className="text-right"><button className="btn-secondary btn-sm" onClick={() => setDetail(d)}>Chi tiết</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {detail && <DeviceDetail device={detail} onClose={() => { setDetail(null); reload(); }} />}
    </div>
  );
}
