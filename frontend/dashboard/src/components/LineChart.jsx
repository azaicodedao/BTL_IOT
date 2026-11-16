// Biểu đồ SVG: nhiệt độ (trục trái) + độ ẩm (trục phải), tooltip theo chuột
import { useMemo, useRef, useState } from "react";

const W = 640;
const H = 180;
const PAD = { l: 34, r: 34, t: 10, b: 22 };

function range(values, pad) {
  const v = values.filter((x) => x != null);
  if (!v.length) return [0, 1];
  let lo = Math.min(...v);
  let hi = Math.max(...v);
  if (hi - lo < 2) {
    lo -= pad;
    hi += pad;
  }
  return [Math.floor(lo), Math.ceil(hi)];
}

export default function LineChart({ rows }) {
  const ref = useRef();
  const [hover, setHover] = useState(null);

  const c = useMemo(() => {
    if (!rows?.length) return null;
    const ts = rows.map((r) => new Date(r.ts).getTime());
    const t0 = Math.min(...ts);
    const t1 = Math.max(...ts, t0 + 1);
    const [tLo, tHi] = range(rows.map((r) => r.temperature_c), 1);
    const [hLo, hHi] = range(rows.map((r) => r.humidity_pct), 5);
    const x = (t) => PAD.l + ((t - t0) / (t1 - t0)) * (W - PAD.l - PAD.r);
    const yT = (v) => PAD.t + (1 - (v - tLo) / (tHi - tLo)) * (H - PAD.t - PAD.b);
    const yH = (v) => PAD.t + (1 - (v - hLo) / (hHi - hLo)) * (H - PAD.t - PAD.b);
    const path = (k, y) => rows.map((r, i) => (r[k] == null ? "" : `${i ? "L" : "M"}${x(ts[i]).toFixed(1)},${y(r[k]).toFixed(1)}`)).join("");
    return { ts, t0, t1, tLo, tHi, hLo, hHi, x, yT, yH, temp: path("temperature_c", yT), hum: path("humidity_pct", yH) };
  }, [rows]);

  if (!c) return <p className="py-6 text-center text-xs text-slate-500">Chưa có số đo nhiệt độ trong 6 giờ qua</p>;

  const onMove = (e) => {
    const box = ref.current.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    c.ts.forEach((t, i) => Math.abs(c.x(t) - px) < Math.abs(c.x(c.ts[best]) - px) && (best = i));
    setHover(best);
  };
  const h = hover != null ? rows[hover] : null;
  const axis = "fill-slate-400 text-[10px]";

  return (
    <div>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
           role="img" aria-label="Nhiệt độ và độ ẩm">
        {[c.tLo, (c.tLo + c.tHi) / 2, c.tHi].map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={c.yT(v)} y2={c.yT(v)} className="stroke-slate-200 dark:stroke-slate-800" />
            <text x={PAD.l - 5} y={c.yT(v) + 3} textAnchor="end" className={axis}>{v.toFixed(0)}°</text>
          </g>
        ))}
        {[c.hLo, c.hHi].map((v) => <text key={v} x={W - PAD.r + 5} y={c.yH(v) + 3} className={axis}>{v.toFixed(0)}%</text>)}
        {[0, 0.5, 1].map((f) => {
          const t = c.t0 + f * (c.t1 - c.t0);
          return <text key={f} x={c.x(t)} y={H - 5} textAnchor="middle" className={axis}>
            {new Date(t).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</text>;
        })}
        <path d={c.hum} className="fill-none stroke-blue-500 stroke-2" />
        <path d={c.temp} className="fill-none stroke-red-500 stroke-2" />
        {h && <line x1={c.x(c.ts[hover])} x2={c.x(c.ts[hover])} y1={PAD.t} y2={H - PAD.b} className="stroke-slate-300 dark:stroke-slate-700" />}
      </svg>
      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-3 bg-red-500" /> Nhiệt độ</span>
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-3 bg-blue-500" /> Độ ẩm</span>
        {h && <span>{new Date(h.ts).toLocaleTimeString("vi-VN")}: <b>{Number(h.temperature_c).toFixed(1)}°C</b> · <b>{Number(h.humidity_pct).toFixed(0)}%</b></span>}
      </div>
    </div>
  );
}
