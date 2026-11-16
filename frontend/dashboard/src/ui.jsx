// Thành phần giao diện dùng chung (Tailwind)
import { useEffect, useRef, useState } from "react";
import { get } from "./api";

// ---------------------------------------------------------------- định dạng
export const fmtTime = (t) => (t ? new Date(t).toLocaleTimeString("vi-VN") : "–");
export const fmtDateTime = (t) =>
  t ? new Date(t).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "medium" }) : "–";
export const fmtNum = (v, d = 1) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
export const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
export const ago = (t) => {
  if (!t) return "chưa từng";
  const s = Math.round((Date.now() - new Date(t)) / 1000);
  if (s < 60) return `${Math.max(s, 0)}s trước`;
  if (s < 3600) return `${Math.floor(s / 60)} phút trước`;
  return `${Math.floor(s / 3600)} giờ trước`;
};

// ---------------------------------------------------------------- nhãn trạng thái: [chữ, màu]
export const STATE_LABEL = {
  registered: ["Đã đăng ký", "slate"], provisioned: ["Đã cấp khoá", "blue"], active: ["Hoạt động", "green"],
  offline: ["Mất kết nối", "red"], fault: ["Lỗi", "amber"], maintenance: ["Bảo trì", "amber"],
  suspended: ["Tạm khoá", "red"], decommissioned: ["Thu hồi", "slate"],
};
export const CHECKIN_STATUS = {
  admitted: ["Đã vào phòng", "green"], denied: ["Từ chối", "red"], absent: ["Vắng thi", "red"],
  waiting: ["Chưa đến", "slate"],
};
export const EXAM_PHASE = {
  checkin: ["Kiểm tra vào phòng", "blue"], in_progress: ["Đang thi", "red"], closed: ["Đã kết thúc", "slate"],
};
export const SEVERITY = { high: ["Cao", "red"], medium: ["Trung bình", "amber"], low: ["Thấp", "blue"] };
export const EVENT_TYPE = { phone: "Dùng điện thoại", head_turn: "Quay đầu nhiều" };
export const REVIEW = { pending: ["Chờ duyệt", "amber"], confirmed: ["Đã xác nhận", "red"], dismissed: ["Bác bỏ", "slate"] };
export const CMD_STATUS = {
  pending: ["Chờ", "slate"], sent: ["Đã gửi", "blue"], acked: ["Đã ACK", "green"], failed: ["Lỗi", "red"], timeout: ["Hết giờ", "red"],
};

export function Badge({ map, value, tone, children }) {
  const [text, t] = map?.[value] || [value, tone || "slate"];
  return <span className={`badge-${tone || t}`}>{children ?? text}</span>;
}

export function Dot({ on, title }) {
  return (
    <span title={title} className={`inline-block h-2.5 w-2.5 flex-none rounded-full ${on ? "bg-emerald-500" : "bg-red-500"}`} />
  );
}

export function Card({ title, actions, children, className = "", bodyClass = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, tone = "" }) {
  const color = { red: "text-red-600 dark:text-red-400", green: "text-emerald-600 dark:text-emerald-400",
                  amber: "text-amber-600 dark:text-amber-400", blue: "text-blue-600 dark:text-blue-400" }[tone] || "";
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
      <div className={`text-xl font-semibold tabular-nums ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
    </div>
  );
}

export function Field({ label, children, hint, className = "" }) {
  return (
    <label className={`block ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Empty({ children }) {
  return <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">{children}</p>;
}

export function ErrorText({ error }) {
  return error ? <p className="text-sm text-red-600 dark:text-red-400">{String(error.message || error)}</p> : null;
}

export function Modal({ title, onClose, children, wide }) {
  const ref = useRef();
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    // Sự kiện close của dialog con lan lên dialog cha qua React -> chỉ đóng khi chính dialog này đóng
    <dialog ref={ref} onClose={(e) => e.target === ref.current && onClose()}
            onClick={(e) => e.target === ref.current && onClose()}
            className={`m-auto w-[calc(100vw-2rem)] rounded-2xl bg-white p-0 text-slate-900 shadow-2xl dark:bg-slate-900 dark:text-slate-100 ${wide ? "max-w-4xl" : "max-w-lg"}`}>
      <div className="p-5">
        <header className="mb-4 flex items-start justify-between gap-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Đóng">✕</button>
        </header>
        {children}
      </div>
    </dialog>
  );
}

/** Nút gọi API: khoá khi đang chạy, báo lỗi bằng alert */
export function AsyncButton({ onClick, children, className = "btn-secondary", confirm, ...rest }) {
  const [busy, setBusy] = useState(false);
  return (
    <button {...rest} className={className} disabled={busy || rest.disabled}
            onClick={async (e) => {
              if (confirm && !window.confirm(confirm)) return;
              setBusy(true);
              try {
                await onClick(e);
              } catch (err) {
                alert(err.message);
              } finally {
                setBusy(false);
              }
            }}>
      {busy ? "…" : children}
    </button>
  );
}

/**
 * Ảnh lưu tại server biên (bằng chứng, khuôn mặt). `path` là endpoint của trung tâm: nó kiểm tra quyền, ghi audit
 * rồi trả {url} có vé ngắn hạn; <img> tải ảnh thẳng từ biên.
 */
export function AuthImage({ path, alt = "", className = "", refreshMs, version }) {
  const [src, setSrc] = useState(null);
  const [failed, setFailed] = useState(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    let timer;
    const load = async () => {
      try {
        const { url } = await get(path);
        if (!alive) return;
        setSrc(url);
        setFailed(null);
      } catch (e) {
        if (alive) setFailed(e.message);
      }
      if (alive && refreshMs) timer = setTimeout(load, refreshMs);
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [path, refreshMs, version]);
  if (!src || failed)
    return (
      <div className={`grid place-items-center bg-slate-100 p-2 text-center text-xs text-slate-500 dark:bg-slate-800 ${className}`}>
        {failed || "Đang tải…"}
      </div>
    );
  return <img src={src} alt={alt} className={className}
              onError={() => setFailed("Không tải được ảnh từ server biên (cần cùng mạng với server biên)")} />;
}

const CAMERA_RETRY_MS = 3000;

/**
 * Camera realtime: luồng MJPEG lấy THẲNG từ server biên (hình không đi qua trung tâm).
 * Trung tâm chỉ cấp URL có vé; vé hết hạn / mất tín hiệu -> xin vé mới rồi nối lại.
 */
export function LiveCamera({ deviceId, label, overlay, className = "" }) {
  const [attempt, setAttempt] = useState(0);
  // value: connecting | live | error. Gắn với (deviceId, attempt) để kết quả của lần thử cũ không ghi đè lần mới
  const [stream, setStream] = useState({ key: null, src: null, value: "connecting", message: null });
  const key = `${deviceId}#${attempt}`;

  useEffect(() => {
    if (!deviceId) return;
    let alive = true;
    let timer;
    get(`/devices/${encodeURIComponent(deviceId)}/stream-ticket`)
      .then(({ url }) => alive && setStream({ key, src: url, value: "connecting", message: null }))
      .catch((e) => {
        if (!alive) return;
        setStream({ key, src: null, value: "error", message: e.message });
        timer = setTimeout(() => setAttempt((a) => a + 1), CAMERA_RETRY_MS);
      });
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [key]);

  if (!deviceId) {
    return (
      <div className={`grid aspect-[4/3] place-items-center rounded-xl bg-slate-900 text-sm text-slate-400 ${className}`}>
        Phòng chưa có camera
      </div>
    );
  }
  const current = stream.key === key ? stream : { src: null, value: "connecting", message: null };
  const state = current.value;
  return (
    <div className={`relative aspect-[4/3] overflow-hidden rounded-xl bg-slate-950 ${className}`}>
      {current.src && (
        <img key={current.src} src={current.src} alt={`Camera ${deviceId}`} className="h-full w-full object-contain"
             onLoad={() => setStream((s) => (s.key === key ? { ...s, value: "live" } : s))}
             onError={() => {
               setStream((s) => (s.key === key ? { ...s, src: null, value: "error", message: null } : s));
               setTimeout(() => setAttempt((a) => a + 1), CAMERA_RETRY_MS);
             }} />
      )}
      <div className="absolute left-2 top-2 flex items-center gap-2">
        <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-semibold text-white
                          ${state === "live" ? "bg-red-600" : "bg-slate-700/80"}`}>
          <span className={`h-1.5 w-1.5 rounded-full bg-white ${state === "live" ? "animate-pulse" : ""}`} />
          {state === "live" ? "LIVE" : state === "error" ? "Mất tín hiệu" : "Đang kết nối"}
        </span>
        <span className="rounded-md bg-black/50 px-2 py-0.5 text-xs text-white">{label || deviceId}</span>
      </div>
      {state !== "live" && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center px-4 text-center text-sm text-slate-400">
          {state === "error"
            ? current.message || "Không nhận được hình từ server biên (cần cùng mạng), đang thử lại…"
            : "Đang bật camera…"}
        </div>
      )}
      {overlay}
    </div>
  );
}

/** Hook tải dữ liệu: [data, reload, error, setData] */
export function useLoad(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const reload = async () => {
    try {
      setData(await fn());
      setError(null);
    } catch (e) {
      setError(e);
    }
  };
  useEffect(() => {
    reload();
  }, deps);
  return [data, reload, error, setData];
}
