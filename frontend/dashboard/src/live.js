// 1 kết nối WebSocket dùng chung, phát lại sự kiện cho các trang qua useLive(type, handler).
import { useEffect, useRef, useState } from "react";
import { refreshToken, session, wsUrl } from "./api";

const handlers = new Set();
const statusListeners = new Set();
let ws = null;
let online = false;
let retryTimer = null;
let stopped = true;

function setOnline(v) {
  online = v;
  statusListeners.forEach((fn) => fn(v));
}

function connect() {
  if (stopped || !session.get()) return;
  ws = new WebSocket(wsUrl("/ws"));
  ws.onopen = () => setOnline(true);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    handlers.forEach((h) => (h.type === "*" || h.type === msg.type || msg.type.startsWith(h.type + ".")) && h.fn(msg));
  };
  ws.onclose = async (e) => {
    setOnline(false);
    ws = null;
    if (stopped) return;
    if (e.code === 4401) await refreshToken();          // token hết hạn -> làm mới rồi nối lại
    retryTimer = setTimeout(connect, 2000);
  };
}

export function startLive() {
  if (!stopped) return;
  stopped = false;
  connect();
}

export function stopLive() {
  stopped = true;
  clearTimeout(retryTimer);
  ws?.close();
}

/** type: "telemetry", "device", "alert", "exam", "attendance", "command", "session" hoặc "*" */
export function useLive(type, fn) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const h = { type, fn: (msg) => ref.current(msg) };
    handlers.add(h);
    return () => handlers.delete(h);
  }, [type]);
}

export function useLiveStatus() {
  const [v, setV] = useState(online);
  useEffect(() => {
    statusListeners.add(setV);
    return () => statusListeners.delete(setV);
  }, []);
  return v;
}
