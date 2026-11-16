import { useEffect, useMemo, useState } from "react";
import { get, post } from "../api";
import { useAuth } from "../auth";
import LineChart from "../components/LineChart";
import { useLive } from "../live";
import { navigate } from "../router";
import { ago, AsyncButton, Badge, Card, CMD_STATUS, Dot, Empty, EXAM_PHASE, fmtNum, LiveCamera, Stat, STATE_LABEL,
         useLoad } from "../ui";

function ControlPanel({ device }) {
  const [last, setLast] = useState(null);
  const [text, setText] = useState("");
  useLive("command", (m) => m.data.device_id === device.device_id && setLast(m.data));
  const send = async (body) => setLast(await post(`/devices/${device.device_id}/commands`, body));
  return (
    <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">Còi {device.device_id}:</span>
        <AsyncButton className="btn-secondary btn-sm" onClick={() => send({ action: "buzzer", pattern: "once" })}>Chíp 1 lần</AsyncButton>
        <AsyncButton className="btn-secondary btn-sm" onClick={() => send({ action: "buzzer", pattern: "stop" })}>Tắt còi</AsyncButton>
        {last && <span className="text-xs"><Badge map={CMD_STATUS} value={last.status} /> {last.params?.pattern || last.action}</span>}
      </div>
      <form className="flex gap-2" onSubmit={async (e) => {
        e.preventDefault();
        if (!text.trim()) return;
        try {
          await send({ action: "display", text: text.trim() });
          setText("");
        } catch (err) {
          alert(err.message);
        }
      }}>
        <input className="input" value={text} maxLength={32} onChange={(e) => setText(e.target.value)}
               placeholder="Hiện chữ lên LCD 15 giây (không dấu)" />
        <button className="btn-secondary">Gửi LCD</button>
      </form>
    </div>
  );
}

function RoomCard({ room, devices, exam, alerts }) {
  const { canOperate } = useAuth();
  const cams = devices.filter((d) => d.type === "camera");
  const sensor = cams.find((c) => c.hw_version?.includes("DHT")) || cams.find((c) => (c.config?.role || "both") !== "exam") || cams[0];
  // Có ca thi: camera của giai đoạn hiện tại; không có: camera đang online (ưu tiên camera phòng thi)
  const liveCam = exam ? (exam.status === "in_progress" ? exam.exam_camera : exam.checkin_camera)
    : (cams.find((c) => c.online && c.config?.role === "exam") || cams.find((c) => c.online) || cams[0])?.device_id;
  const [rows, setRows] = useState([]);
  const [latest, setLatest] = useState(null);

  useEffect(() => {
    if (!sensor) return;
    const from = new Date(Date.now() - 6 * 3600e3).toISOString();
    get(`/devices/${sensor.device_id}/telemetry?agg=5m&from=${encodeURIComponent(from)}`).then(setRows).catch(() => {});
    get(`/devices/${sensor.device_id}/telemetry?agg=raw&from=${encodeURIComponent(new Date(Date.now() - 3600e3).toISOString())}`)
      .then((r) => r.length && setLatest(r[r.length - 1])).catch(() => {});
  }, [sensor?.device_id]);
  useLive("telemetry", (m) => {
    if (m.data.device_id !== sensor?.device_id) return;
    setLatest(m.data);
    setRows((p) => [...p.slice(-500), m.data]);
  });
  const advice = latest?.advice?.[0];

  return (
    <Card title={`${room.name} (${room.code})`}
          actions={exam ? <button className="btn-primary btn-sm" onClick={() => navigate(`exams/${exam.id}`)}>
                            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> {EXAM_PHASE[exam.status][0]} · {exam.name || `#${exam.id}`} →</button>
                        : <span className="badge-slate">Không có ca thi</span>}>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          {cams.length ? cams.map(camera => <LiveCamera key={camera.device_id} deviceId={camera.device_id}
            label={`${{ checkin: "Điểm danh", exam: "Giám sát", both: "Điểm danh + giám sát" }[camera.config?.role || "both"]} · ${camera.device_id}${camera.device_id === liveCam ? " · Camera chính" : ""}`} />)
            : <LiveCamera deviceId={null} />}
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Nhiệt độ" value={`${fmtNum(latest?.temperature_c)}°C`} tone={advice ? "amber" : ""} />
            <Stat label="Độ ẩm" value={`${fmtNum(latest?.humidity_pct, 0)}%`} />
            <Stat label="Cảnh báo đang mở" value={alerts} tone={alerts ? "red" : ""} />
            <Stat label="Vào phòng" value={exam ? `${exam.admitted}/${exam.roster_total}` : "–"} tone={exam ? "green" : ""} />
          </div>
          {advice && <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">Khuyến nghị: {advice}</p>}
          <LineChart rows={rows} />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        {devices.map((d) => (
          <span key={d.device_id} className="inline-flex items-center gap-2" title={`RSSI ${d.rssi ?? "–"} dBm · ${ago(d.last_seen)}`}>
            <Dot on={d.online} /> {d.device_id}
            {d.type === "camera" && <span className="text-xs text-slate-500">({{ checkin: "cửa", exam: "phòng thi" }[d.config?.role] || "cửa + phòng"})</span>}
            <Badge map={STATE_LABEL} value={d.state} />
          </span>
        ))}
      </div>
      {canOperate && devices.filter((d) => d.type === "actuator" && ["active", "offline", "fault"].includes(d.state))
        .map((d) => <div key={d.device_id} className="mt-3"><ControlPanel device={d} /></div>)}
    </Card>
  );
}

export default function Dashboard() {
  const [rooms] = useLoad(() => get("/rooms"));
  const [devices, reloadDevices, , setDevices] = useLoad(() => get("/devices"));
  const [alerts, reloadAlerts] = useLoad(() => get("/alerts?state=active&limit=500"));
  const [exams, reloadExams] = useLoad(() => get("/exam/sessions?status=active"));

  useLive("device", (m) => {
    if (m.type === "device.status") {
      setDevices((p) => (p || []).some((d) => d.device_id === m.data.device_id)
        ? p.map((d) => (d.device_id === m.data.device_id ? m.data : d)) : [...(p || []), m.data]);
    } else if (m.type === "device.heartbeat") {
      setDevices((p) => p?.map((d) => (d.device_id === m.data.device_id ? { ...d, rssi: m.data.rssi, last_seen: m.data.last_seen, online: true } : d)));
    }
  });
  useLive("alert", reloadAlerts);
  useLive("session", () => {
    reloadExams();
    reloadDevices();
  });
  useLive("checkin", (m) => m.type === "checkin.admitted" && reloadExams());

  const alertsByRoom = useMemo(() => {
    const m = {};
    (alerts || []).forEach((a) => (m[a.room_id] = (m[a.room_id] || 0) + 1));
    return m;
  }, [alerts]);

  if (!rooms || !devices) return <Empty>Đang tải…</Empty>;
  if (!rooms.length) return <Empty>Bạn chưa được gán phòng nào. Liên hệ quản trị viên.</Empty>;
  return (
    <div className="space-y-4">
      {rooms.map((r) => (
        // Thiết bị đã thu hồi không còn thuộc phòng: không dùng làm cảm biến / camera của phòng
        <RoomCard key={r.id} room={r} devices={devices.filter((d) => d.room_id === r.id && d.state !== "decommissioned")}
                  exam={(exams || []).find((e) => e.room_id === r.id)} alerts={alertsByRoom[r.id] || 0} />
      ))}
    </div>
  );
}
