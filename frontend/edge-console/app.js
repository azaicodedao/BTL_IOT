// Bảng điều khiển server biên: duyệt thiết bị tự báo danh, gán phòng, vòng đời, cấu hình, lệnh thử, xem camera.
// JS thuần, không cần build. Dữ liệu động luôn đi qua esc() trước khi chèn vào HTML.
"use strict";

const API = "/edge/v1";
const TOKEN_KEY = "edge.console.token";
const REFRESH_MS = 3000;
const $ = (id) => document.getElementById(id);

let token = sessionStorage.getItem(TOKEN_KEY);
let timer = null;
let rooms = [];

const STATE = {
  registered: ["Đã đăng ký", ""], provisioned: ["Đã cấp khoá", "warn"], active: ["Hoạt động", "ok"],
  offline: ["Mất kết nối", "bad"], fault: ["Lỗi cảm biến", "bad"], maintenance: ["Bảo trì", "warn"],
  suspended: ["Tạm khoá", "bad"], decommissioned: ["Đã thu hồi", ""],
};
const ROLE = { checkin: "Camera cửa (kiểm tra vào phòng)", exam: "Camera phòng thi (giám sát)", both: "Cả hai" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function ago(iso) {
  if (!iso) return "chưa từng";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return `${Math.round(s)} giây trước`;
  if (s < 3600) return `${Math.round(s / 60)} phút trước`;
  return new Date(iso).toLocaleString("vi-VN");
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(API + path, {
    method,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && path !== "/auth/login") {
    logout();
    throw new Error("Phiên đăng nhập đã hết hạn");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Lỗi ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

/** Chạy 1 thao tác của người dùng: khoá nút khi đang chạy, báo lỗi ra banner, rồi tải lại dữ liệu. */
async function act(button, fn, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return;
  if (button) button.disabled = true;
  try {
    await fn();
    showBanner(null);
  } catch (e) {
    showBanner(e.message);
  } finally {
    if (button) button.disabled = false;
    refresh();
  }
}

function showBanner(message) {
  $("banner").hidden = !message;
  $("banner").textContent = message || "";
}

// ---------------------------------------------------------------- đăng nhập

function logout() {
  token = null;
  sessionStorage.removeItem(TOKEN_KEY);
  clearInterval(timer);
  $("dialog").close();
  $("app").hidden = true;
  $("login").hidden = false;
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    token = (await api("/auth/login", { method: "POST", body: { password: $("password").value } })).token;
    sessionStorage.setItem(TOKEN_KEY, token);
    $("password").value = "";
    $("login-error").textContent = "";
    start();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});
$("logout").addEventListener("click", logout);

function start() {
  $("login").hidden = true;
  $("app").hidden = false;
  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, REFRESH_MS);
}

// ---------------------------------------------------------------- hiển thị

async function refresh() {
  if (!token) return;
  try {
    const [status, enrollments, devices, roomList, sources] = await Promise.all([
      api("/status"), api("/enrollments"), api("/devices"), api("/rooms"), api("/camera-sources")]);
    rooms = roomList;
    renderStatus(status);
    renderEnrollments(enrollments);
    renderDevices(devices);
    renderCameraSources(sources, enrollments, devices);
    if ($("audit-box").open) renderAudit(await api("/audit-logs?limit=100"));
  } catch (e) {
    if (token) showBanner(`Không tải được dữ liệu: ${e.message}`);
  }
}

$("camera-source-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    await api("/camera-sources", { method: "POST", body: { name: $("source-name").value.trim(), url: $("source-url").value.trim() } });
    e.target.reset();
    $("source-message").textContent = "Đã thêm nguồn video. Chờ camera xuất hiện bên dưới, bấm Đăng ký rồi Gán phòng. Trong Cấu hình, chọn vai trò Camera phòng thi (giám sát).";
    refresh();
  } catch (err) { $("source-message").textContent = err.message; }
  finally { button.disabled = false; }
});

function renderCameraSources(sources, enrollments, devices) {
  $("camera-sources").innerHTML = sources.length ? table(["Nguồn video", "Kết nối", "Đăng ký thiết bị", ""], sources.map((s) => {
    const device = devices.find(d => d.hardware_uid === s.hardware_uid);
    const enrollment = enrollments.find(e => e.hardware_uid === s.hardware_uid);
    const state = device ? `${device.device_id} · ${device.online ? "online" : "offline"}`
      : enrollment ? "Chờ duyệt đăng ký" : "Đang báo danh…";
    return `<tr><td><b>${esc(s.name)}</b><div class="muted">${esc(s.url)}</div><div class="muted">Mã nguồn: ${esc(s.id)}</div></td>
      <td>${s.running ? "Đang chạy" : s.enabled ? "Lỗi tiến trình — bấm Kết nối lại" : "Đã dừng"}</td><td>${esc(state)}</td>
      <td class="actions"><button data-source="${esc(s.id)}" data-source-action="edit">Sửa URL</button>
      <button data-source="${esc(s.id)}" data-source-action="toggle">${s.enabled && s.running ? "Dừng" : "Kết nối lại"}</button>
      <button data-source="${esc(s.id)}" data-source-action="delete" class="danger">Xoá nguồn</button></td></tr>`;
  })) : '<p class="muted">Chưa thêm camera điện thoại hoặc camera mạng.</p>';
  $("camera-sources").querySelectorAll("button[data-source]").forEach(button => button.addEventListener("click", () => {
    const source = sources.find(s => s.id === button.dataset.source);
    const path = `/camera-sources/${encodeURIComponent(source.id)}`;
    if (button.dataset.sourceAction === "edit") {
      openDialog("Đổi URL camera", `<form id="edit-source"><label for="edit-source-url">URL video mới</label>
        <input id="edit-source-url" required maxlength="2048" value="${esc(source.url)}"><p class="muted">Giữ nguyên danh tính, phòng và đăng ký khi đổi mạng.</p>
        <p id="edit-source-error" class="error" role="alert"></p><button class="primary">Lưu và kết nối lại</button></form>`);
      $("edit-source").addEventListener("submit", async e => {
        e.preventDefault(); const submit = e.target.querySelector("button"); submit.disabled = true;
        try { await api(path, { method: "PUT", body: { name: source.name, url: $("edit-source-url").value.trim(), enabled: true } }); $("dialog").close(); refresh(); }
        catch (err) { $("edit-source-error").textContent = err.message; }
        finally { submit.disabled = false; }
      });
    } else if (button.dataset.sourceAction === "delete") {
      act(button, () => api(path, { method: "DELETE" }), "Xoá nguồn video? Thiết bị đã đăng ký vẫn giữ trong danh sách; dùng Thu hồi trong Quản lý thiết bị nếu muốn ngừng cấp quyền.");
    } else {
      act(button, () => api(path, { method: "PUT", body: { name: source.name, url: source.url, enabled: !(source.enabled && source.running) } }));
    }
  }));
}

function renderStatus(s) {
  $("edge-name").textContent = `${s.name} · ${s.edge_id}`;
  $("edge-sub").textContent = `Địa chỉ cho thiết bị và trình duyệt: ${s.public_url}`;
  const c = s.central;
  const queue = c.queue || { pending: c.outbox };
  $("sync-summary").textContent = !c.link
    ? `Mất kết nối trung tâm. Biên tiếp tục hoạt động và lưu dữ liệu tại máy: ${queue.pending} sự kiện chờ gửi.`
    : queue.pending ? `Đang chờ đồng bộ ${queue.pending} sự kiện đã lưu tại biên.` : "Đã đồng bộ. Không có dữ liệu chờ gửi.";
  $("sync-detail").textContent = `Lưu trên SQLite, giữ qua khởi động lại. Tự gửi bù khi kết nối phục hồi. Lần gửi thành công: ${c.last_flush ? new Date(c.last_flush).toLocaleString("vi-VN") : "chưa có"}.`
    + (queue.oldest_at ? ` Dữ liệu chờ lâu nhất: ${ago(queue.oldest_at)}.` : "")
    + (c.next_retry_at ? ` Lần thử tiếp: ${new Date(c.next_retry_at).toLocaleTimeString("vi-VN")}.` : "");
  $("sync-error").hidden = !c.delivery_error;
  $("sync-error").textContent = c.delivery_error ? `Chưa gửi được: ${c.delivery_error}` : "";
  const item = (title, on, text) => `<div><b>${esc(title)}</b><span class="dot ${on ? "on" : ""}"></span>${esc(text)}</div>`;
  $("health").innerHTML =
    item("MQTT broker", s.mqtt.connected, s.mqtt.connected ? `đã kết nối ${s.mqtt.host}:${s.mqtt.port}` : "CHƯA kết nối - kiểm tra Mosquitto") +
    item("Server trung tâm", c.link, c.link ? `đang nối ${c.url}` : `mất kết nối ${c.url}`) +
    item("Sự kiện chờ gửi lên trung tâm", c.outbox === 0, c.outbox === 0 ? "không có" : `${c.outbox} sự kiện (sẽ gửi bù)`) +
    item("Tự phát hiện trong LAN (mDNS)", s.mdns.advertised, s.mdns.detail);
  const local = /\/\/(127\.|localhost)/.test(s.public_url);
  if (local) showBanner("Server biên đang quảng bá địa chỉ 127.0.0.1: thiết bị sẽ không kết nối được. Đặt EDGE_PUBLIC_HOST = IP LAN của máy này.");

  $("exams").innerHTML = s.exams.length
    ? table(["Ca thi", "Phòng", "Giai đoạn", "SV nhận diện được"], s.exams.map((e) =>
        `<tr><td>#${esc(e.exam_id)} ${esc(e.name || "")}</td><td>${esc(e.room_code)}</td>
         <td>${e.phase === "checkin" ? "Kiểm tra vào phòng" : "Đang làm bài (giám sát)"}</td><td>${esc(e.roster)}</td></tr>`))
    : `<div class="empty">Không có ca thi nào. Ca thi được mở từ dashboard của server trung tâm.</div>`;
}

$("sync-retry").addEventListener("click", e => act(e.target, () => api("/sync/retry", { method: "POST" })));

function table(heads, rows) {
  return `<div class="table-wrap"><table><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
          <tbody>${rows.join("")}</tbody></table></div>`;
}

function renderEnrollments(list) {
  $("pending-count").textContent = list.length ? `(${list.length})` : "";
  if (!list.length) {
    $("enrollments").innerHTML = `<div class="empty">Chưa có thiết bị nào chờ. Cắm nguồn thiết bị và cấu hình Wi-Fi cho nó (mạng SMART-…).</div>`;
    return;
  }
  $("enrollments").innerHTML = table(["Thiết bị", "Loại", "Mã ghép", "IP · firmware", "Lần cuối", ""], list.map((e) => {
    let actions;
    if (e.reclaim_pending) {
      actions = `<button class="primary" data-act="reclaim-yes" data-id="${e.id}">Chấp nhận ghép lại</button>
                 <button class="danger" data-act="reclaim-no" data-id="${e.id}">Bác bỏ</button>`;
    } else if (e.status === "rejected") {
      actions = `<span class="badge bad">Đã từ chối</span> <button data-act="forget" data-id="${e.id}">Xoá (cho báo danh lại)</button>`;
    } else {
      actions = `<button class="primary" data-act="register" data-id="${e.id}" data-suggest="${esc(e.suggested_device_id)}"
                         data-name="${esc(e.factory_name || e.hardware_uid)}">Đăng ký</button>
                 <button class="danger" data-act="reject" data-id="${e.id}">Từ chối</button>`;
    }
    const note = e.reclaim_pending
      ? `<div class="muted">Đã đăng ký là <b>${esc(e.device_id)}</b>, đang xin ghép lại (mất khoá sau factory reset, hoặc có thiết bị giả địa chỉ MAC)</div>` : "";
    return `<tr><td><b>${esc(e.factory_name || "?")}</b><div class="muted">${esc(e.hardware_uid)}</div>${note}</td>
      <td>${e.type === "camera" ? "Camera" : "LCD + còi"}</td>
      <td><span class="code">${esc(e.reclaim_pending ? e.reclaim_pair_code : e.pair_code)}</span></td>
      <td class="muted">${esc(e.ip || "–")} · ${esc(e.firmware_version || "–")}</td>
      <td class="muted">${esc(ago(e.last_seen))}</td><td class="actions">${actions}</td></tr>`;
  }));
}

function renderDevices(list) {
  $("device-count").textContent = list.length ? `(${list.length})` : "";
  if (!list.length) {
    $("devices").innerHTML = `<div class="empty">Chưa có thiết bị nào được đăng ký.</div>`;
    return;
  }
  $("devices").innerHTML = table(["Thiết bị", "Loại", "Phòng", "Trạng thái", "Kết nối", ""], list.map((d) => {
    const [label, tone] = STATE[d.state] || [d.state, ""];
    let hint = "";
    if (d.state === "registered" && !d.room_code) hint = `<div class="muted">Chưa gán phòng</div>`;
    else if (d.awaiting_key) hint = `<div class="muted">Chờ thiết bị tự nhận khoá…</div>`;
    return `<tr><td><b>${esc(d.device_id)}</b><div class="muted">${esc(d.hw_version || "")} · ${esc(d.hardware_uid || "")}</div></td>
      <td>${d.type === "camera" ? esc(ROLE[d.config.role || "both"]) : "LCD + còi"}</td>
      <td>${esc(d.room_code || "–")}</td>
      <td><span class="badge ${tone}">${esc(label)}</span>${hint}</td>
      <td><span class="dot ${d.online ? "on" : ""}"></span><span class="muted">${esc(ago(d.last_seen))} · fw ${esc(d.fw_version || "–")}</span></td>
      <td class="actions">${!d.room_code && d.state === "registered"
        ? `<button class="primary" data-act="open" data-device="${esc(d.device_id)}">Gán phòng</button>`
        : `<button data-act="open" data-device="${esc(d.device_id)}">Quản lý</button>`}</td></tr>`;
  }));
  window.__devices = Object.fromEntries(list.map((d) => [d.device_id, d]));
}

function renderAudit(rows) {
  $("audit").innerHTML = rows.length ? table(["Thời gian", "Ai", "Thao tác", "Đối tượng", "Chi tiết"], rows.map((a) =>
    `<tr><td class="muted">${esc(new Date(a.ts).toLocaleString("vi-VN"))}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
     <td>${esc(a.target || "")}</td><td class="muted">${esc(a.detail ? JSON.stringify(a.detail) : "")}</td></tr>`))
    : `<div class="empty">Chưa có thao tác nào</div>`;
}
$("audit-box").addEventListener("toggle", refresh);

// ---------------------------------------------------------------- thao tác trên bảng (uỷ quyền sự kiện)

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  const actions = {
    register: () => openRegister(id, b.dataset.suggest, b.dataset.name),
    reject: () => act(b, () => api(`/enrollments/${id}/reject`, { method: "POST" }), "Từ chối thiết bị này?"),
    forget: () => act(b, () => api(`/enrollments/${id}`, { method: "DELETE" })),
    "reclaim-yes": () => act(b, () => api(`/enrollments/${id}/reclaim?accept=true`, { method: "POST" }),
      "Chỉ chấp nhận khi mã ghép trùng với mã đang hiện trên chính thiết bị. Khoá cũ sẽ bị thu hồi. Tiếp tục?"),
    "reclaim-no": () => act(b, () => api(`/enrollments/${id}/reclaim?accept=false`, { method: "POST" })),
    open: () => openDevice(window.__devices[b.dataset.device]),
  };
  actions[b.dataset.act]?.();
});

// ---------------------------------------------------------------- hộp thoại

function openDialog(title, html) {
  $("dialog-title").textContent = title;
  $("dialog-body").innerHTML = html;
  if (!$("dialog").open) $("dialog").showModal();
}
$("dialog-close").addEventListener("click", () => $("dialog").close());
$("dialog").addEventListener("close", () => { $("dialog-body").innerHTML = ""; });   // dừng luồng camera đang xem

function roomField(current) {
  const options = rooms.map((r) => `<option value="${esc(r.code)}">${esc(r.name || "")}</option>`).join("");
  return `<div><label for="f-room">Phòng (chọn trong danh sách của trung tâm hoặc nhập mã mới)</label>
          <input id="f-room" list="room-list" value="${esc(current || "")}" pattern="[A-Za-z0-9_-]{1,20}" required placeholder="A101">
          <datalist id="room-list">${options}</datalist></div>`;
}

function openRegister(id, suggestion, name) {
  openDialog(`Đăng ký ${name}`, `
    <form id="f-register">
      <p class="muted">Bước 1/2. Đặt mã thiết bị (cũng là tên đăng nhập MQTT). Bước tiếp theo là gán phòng.</p>
      <div class="row"><div><label for="f-device-id">Mã thiết bị</label>
        <input id="f-device-id" value="${esc(suggestion)}" pattern="[a-z0-9-]{3,40}" required></div>
        <button class="primary">Đăng ký</button></div>
      <p id="f-error" class="error" role="alert"></p>
    </form>`);
  $("f-register").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const d = await api(`/enrollments/${id}/register`, { method: "POST", body: { device_id: $("f-device-id").value.trim() } });
      await refresh();
      openDevice({ ...d, awaiting_key: false, viewers: 0 });
    } catch (err) {
      $("f-error").textContent = err.message;
    }
  });
}

function openDevice(d) {
  if (!d) return;
  const live = !["decommissioned", "suspended"].includes(d.state);
  const hasKey = d.has_credentials;
  const [label] = STATE[d.state] || [d.state];
  let html = `<p><span class="badge">${esc(label)}</span> <span class="muted">${esc(d.hardware_uid || "")} · firmware ${esc(d.fw_version || "–")}
              · RSSI ${esc(d.rssi ?? "–")} dBm · ${esc(ago(d.last_seen))}</span></p><p id="d-error" class="error" role="alert"></p>`;

  if (live) {
    html += `<h3>${d.room_code ? "Đổi phòng" : "Bước 2/2 · Gán phòng"}</h3>
      <form id="f-assign" class="row">${roomField(d.room_code)}<button class="primary">${d.room_code ? "Đổi phòng" : "Gán phòng"}</button></form>
      <p class="muted">Sau khi gán, thiết bị tự nhận khoá MQTT + topic của phòng trong vài giây. ${d.room_code
        ? "Đổi phòng sẽ thu hồi khoá cũ; thiết bị tự xin khoá mới, không cần nạp lại firmware." : ""}</p>`;
  }
  if (hasKey && live) {
    html += `<h3>Cấu hình</h3><form id="f-config" class="row">
      ${d.type === "camera" ? `<div><label for="c-role">Vai trò camera</label><select id="c-role">${Object.entries(ROLE).map(([k, v]) =>
        `<option value="${k}" ${k === (d.config.role || "both") ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        <div><label for="c-tel">Telemetry (giây)</label><input id="c-tel" type="number" min="5" max="3600" value="${esc(d.config.telemetry_interval_s ?? 30)}"></div>` : ""}
      <div><label for="c-hb">Heartbeat (giây)</label><input id="c-hb" type="number" min="5" max="300" value="${esc(d.config.heartbeat_interval_s ?? 15)}"></div>
      <button>Lưu cấu hình</button></form>`;
    if (d.type === "actuator") {
      html += `<h3>Thử thiết bị</h3><div class="row">
        <button data-cmd="once">Còi chíp 1 lần</button><button data-cmd="continuous">Còi liên tục</button><button data-cmd="stop">Tắt còi</button>
        <form id="f-text" class="row" style="flex:2 1 260px"><div><label for="c-text">Hiện lên LCD (tối đa 32 ký tự)</label>
          <input id="c-text" maxlength="32" required></div><button>Gửi</button></form></div>
        <div id="d-commands"></div>`;
    } else {
      html += `<h3>Camera</h3><button id="d-preview">Xem camera trực tiếp</button><div id="d-stream"></div>`;
    }
  }
  html += `<h3>Vòng đời</h3><div class="row">
    ${hasKey && live ? `<button data-life="rekey">Cấp lại khoá</button>` : ""}
    ${["active", "offline", "fault"].includes(d.state) ? `<button data-life="maintenance">Chuyển bảo trì</button>` : ""}
    ${d.state === "maintenance" ? `<button data-life="active">Kết thúc bảo trì</button>` : ""}
    ${d.state === "suspended" ? `<button data-life="active">Mở khoá</button>` : ""}
    ${live ? `<button class="danger" data-life="suspended">Tạm khoá</button>` : ""}
    ${d.state !== "decommissioned" ? `<button class="danger" data-life="decommissioned">Thu hồi vĩnh viễn</button>` : ""}
    ${["decommissioned", "registered"].includes(d.state) ? `<button class="danger" data-life="delete">Xoá bản ghi</button>` : ""}
  </div>`;
  openDialog(`${d.device_id} · ${d.type === "camera" ? "Camera" : "LCD + còi"}`, html);
  wireDevice(d);
}

function wireDevice(d) {
  const base = `/devices/${encodeURIComponent(d.device_id)}`;
  const fail = (e) => { $("d-error").textContent = e.message; };
  const reopen = async () => { await refresh(); const next = window.__devices[d.device_id]; next ? openDevice(next) : $("dialog").close(); };
  const run = (fn) => async (e) => { e?.preventDefault(); try { await fn(); await reopen(); } catch (err) { fail(err); } };

  $("f-assign")?.addEventListener("submit", run(() => api(`${base}/assign`, { method: "POST", body: { room_code: $("f-room").value.trim() } })));
  $("f-config")?.addEventListener("submit", run(() => {
    const body = { heartbeat_interval_s: +$("c-hb").value };
    if (d.type === "camera") Object.assign(body, { role: $("c-role").value, telemetry_interval_s: +$("c-tel").value });
    return api(`${base}/config`, { method: "PUT", body });
  }));

  const loadCommands = async () => {
    const box = $("d-commands");
    if (!box) return;
    const rows = await api(`${base}/commands?limit=8`).catch(() => []);
    box.innerHTML = rows.length ? table(["Lúc", "Lệnh", "Trạng thái", "Lần gửi", "ACK sau"], rows.map((c) =>
      `<tr><td class="muted">${esc(new Date(c.ts_created).toLocaleTimeString("vi-VN"))}</td><td>${esc(c.action)} ${esc(c.params.pattern || c.params.text || "")}</td>
       <td><span class="badge ${c.status === "acked" ? "ok" : c.status === "sent" || c.status === "pending" ? "warn" : "bad"}">${esc(c.status)}</span></td>
       <td>${esc(c.attempts)}</td><td>${c.ts_ack && c.ts_sent ? `${new Date(c.ts_ack) - new Date(c.ts_sent)} ms` : "–"}</td></tr>`)) : "";
  };
  const send = async (body) => {
    try {
      await api(`${base}/commands`, { method: "POST", body });
      $("d-error").textContent = "";
      loadCommands();
      setTimeout(loadCommands, 1500);            // ACK thường về trong < 1 giây
    } catch (err) { fail(err); }
  };
  $("dialog-body").querySelectorAll("button[data-cmd]").forEach((b) =>
    b.addEventListener("click", () => send({ action: "buzzer", pattern: b.dataset.cmd })));
  $("f-text")?.addEventListener("submit", (e) => { e.preventDefault(); send({ action: "display", text: $("c-text").value }); });
  loadCommands();

  $("d-preview")?.addEventListener("click", () => {
    $("d-stream").innerHTML = `<img class="preview" alt="Camera ${esc(d.device_id)}"
      src="${API}${base}/stream?token=${encodeURIComponent(token)}"><p class="muted">Hình chỉ phát trong mạng của server biên, không gửi lên trung tâm.</p>`;
  });

  const CONFIRM = {
    rekey: "Thu hồi khoá hiện tại? Thiết bị sẽ tự nhận khoá mới sau vài giây.",
    suspended: "Tạm khoá thiết bị? Phiên MQTT bị ngắt ngay.",
    decommissioned: "Thu hồi vĩnh viễn? Khoá và ACL bị xoá, thiết bị không thể kết nối lại.",
    delete: "Xoá bản ghi? Phần cứng này sẽ phải báo danh và được duyệt lại từ đầu.",
  };
  $("dialog-body").querySelectorAll("button[data-life]").forEach((b) => b.addEventListener("click", async () => {
    const to = b.dataset.life;
    if (CONFIRM[to] && !window.confirm(CONFIRM[to])) return;
    await run(() => to === "rekey" ? api(`${base}/rekey`, { method: "POST" })
      : to === "delete" ? api(base, { method: "DELETE" })
      : api(`${base}/transition`, { method: "POST", body: { to } }))();
  }));
}

token ? start() : ($("login").hidden = false);
