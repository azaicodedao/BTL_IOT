// REST client: gắn access token, tự làm mới bằng refresh token khi gặp 401 (1 lần).
const API = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "") + "/api/v1";
const KEY = "classroom.auth";

let auth = load();
const listeners = new Set();

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) || null;
  } catch {
    return null;
  }
}

function save(next) {
  auth = next;
  try {
    next ? localStorage.setItem(KEY, JSON.stringify(next)) : localStorage.removeItem(KEY);
  } catch {
    /* trình duyệt chặn storage: vẫn chạy trong phiên hiện tại */
  }
  listeners.forEach((fn) => fn(auth));
}

export const session = {
  get: () => auth,
  set: save,
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let refreshing = null;
async function refresh() {
  if (!auth?.refresh_token) return false;
  refreshing ??= fetch(`${API}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: auth.refresh_token }),
  })
    .then(async (r) => {
      if (!r.ok) throw new Error();
      save(await r.json());
      return true;
    })
    .catch(() => {
      save(null);
      return false;
    })
    .finally(() => (refreshing = null));
  return refreshing;
}

async function errorMessage(res) {
  const body = await res.json().catch(() => ({}));
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
  return res.statusText || `Lỗi ${res.status}`;
}

export async function request(path, { method = "GET", body, form, raw, retry = true } = {}) {
  const headers = {};
  if (auth?.access_token) headers.Authorization = `Bearer ${auth.access_token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(API + path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (res.status === 401 && retry && auth?.refresh_token && !path.startsWith("/auth/")) {
    if (await refresh()) return request(path, { method, body, form, raw, retry: false });
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  if (raw) return res;
  return res.status === 204 ? null : res.json();
}

export const get = (p) => request(p);
export const post = (p, body) => request(p, { method: "POST", body: body ?? {} });
export const patch = (p, body) => request(p, { method: "PATCH", body });
export const put = (p, body) => request(p, { method: "PUT", body });

export async function login(email, password) {
  const data = await request("/auth/login", { method: "POST", body: { email, password }, retry: false });
  save(data);
  return data;
}

export async function logout() {
  try {
    if (auth?.refresh_token) await post("/auth/logout", { refresh_token: auth.refresh_token });
  } finally {
    save(null);
  }
}

// Ảnh không đi qua server trung tâm: trung tâm chỉ cấp URL có vé ngắn hạn, trình duyệt làm việc thẳng với server biên.
export async function postToEdge(url, form) {
  let res;
  try {
    res = await fetch(url, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, "Không kết nối được server biên (máy này phải cùng mạng với server biên)");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res.json();
}

export async function download(path, filename) {
  const res = await request(path, { raw: true });
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function wsUrl(path = "/ws") {
  const base = (import.meta.env.VITE_API_URL || window.location.origin).replace(/^http/, "ws");
  return `${base}/api/v1${path}?token=${encodeURIComponent(auth?.access_token || "")}`;
}

export { refresh as refreshToken };
