import { useState } from "react";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { AsyncButton, Badge, Card, Empty, ErrorText, Field, fmtDateTime, Modal, useLoad } from "../ui";

const ROLE = { admin: ["Quản trị", "red"], operator: ["Giảng viên / giám thị", "blue"], viewer: ["Người xem", "slate"] };

function UserModal({ user, rooms, onClose }) {
  const [f, setF] = useState(user ? { full_name: user.full_name, role: user.role, room_ids: user.rooms.map((r) => r.id), password: "" }
                                   : { email: "", full_name: "", role: "operator", room_ids: [], password: "" });
  const [error, setError] = useState(null);
  const toggleRoom = (id) => setF({ ...f, room_ids: f.room_ids.includes(id) ? f.room_ids.filter((x) => x !== id) : [...f.room_ids, id] });
  return (
    <Modal title={user ? `Sửa ${user.email}` : "Tạo tài khoản"} onClose={() => onClose(false)}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        try {
          const body = { ...f };
          if (user && !body.password) delete body.password;
          user ? await patch(`/users/${user.id}`, body) : await post("/users", body);
          onClose(true);
        } catch (err) {
          setError(err);
        }
      }}>
        {!user && <Field label="Email"><input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>}
        <Field label="Họ tên"><input className="input" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Vai trò">
          <select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            {Object.entries(ROLE).map(([k, [v]]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="Phòng được gán" hint="Giám thị / người xem chỉ thấy các phòng được gán">
          <div className="flex flex-wrap gap-2">{rooms?.map((r) => (
            <label key={r.id} className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-1 text-sm dark:border-slate-700">
              <input type="checkbox" className="accent-blue-600" checked={f.room_ids.includes(r.id)} onChange={() => toggleRoom(r.id)} /> {r.code}
            </label>))}</div>
        </Field>
        <Field label={user ? "Đặt lại mật khẩu (bỏ trống nếu không đổi)" : "Mật khẩu (≥ 8 ký tự)"}>
          <input className="input" type="password" minLength={8} required={!user} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
        <ErrorText error={error} />
        <button className="btn-primary w-full">Lưu</button>
      </form>
    </Modal>
  );
}

export function Users() {
  const { user: me } = useAuth();
  const [users, reload] = useLoad(() => get("/users"));
  const [rooms, reloadRooms] = useLoad(() => get("/rooms"));
  const [editing, setEditing] = useState(null);
  const [room, setRoom] = useState({ code: "", name: "", building: "" });
  const [error, setError] = useState(null);
  return (
    <div className="space-y-4">
      <Card title="Người dùng" actions={<button className="btn-primary btn-sm" onClick={() => setEditing("new")}>Tạo tài khoản</button>}>
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>Email</th><th>Họ tên</th><th>Vai trò</th><th>Phòng</th><th>Trạng thái</th><th /></tr></thead>
            <tbody>{users?.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td><td>{u.full_name}</td><td><Badge map={ROLE} value={u.role} /></td>
                <td>{u.role === "admin" ? "Tất cả" : u.rooms.map((r) => r.code).join(", ") || "–"}</td>
                <td><Badge map={{ active: ["Hoạt động", "green"], locked: ["Đã khoá", "red"] }} value={u.status} /></td>
                <td className="whitespace-nowrap text-right">
                  <button className="btn-ghost btn-sm" onClick={() => setEditing(u)}>Sửa</button>
                  {u.id !== me.id && <AsyncButton className="btn-secondary btn-sm" onClick={async () => { await patch(`/users/${u.id}`, { status: u.status === "active" ? "locked" : "active" }); reload(); }}>
                    {u.status === "active" ? "Khoá" : "Mở khoá"}</AsyncButton>}
                </td>
              </tr>))}</tbody>
          </table>
        </div>
      </Card>
      <Card title="Phòng thi">
        <form className="mb-3 grid gap-3 md:grid-cols-[1fr_2fr_1fr_auto] md:items-end" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await post("/rooms", { ...room, building: room.building || null });
            setRoom({ code: "", name: "", building: "" });
            setError(null);
            reloadRooms();
          } catch (err) {
            setError(err);
          }
        }}>
          <Field label="Mã phòng (dùng trong topic MQTT)"><input className="input" pattern="[A-Za-z0-9_-]{1,20}" required value={room.code} onChange={(e) => setRoom({ ...room, code: e.target.value })} /></Field>
          <Field label="Tên"><input className="input" required value={room.name} onChange={(e) => setRoom({ ...room, name: e.target.value })} /></Field>
          <Field label="Toà nhà"><input className="input" value={room.building} onChange={(e) => setRoom({ ...room, building: e.target.value })} /></Field>
          <button className="btn-primary">Thêm phòng</button>
          <div className="md:col-span-4"><ErrorText error={error} /></div>
        </form>
        <div className="flex flex-wrap gap-2">{rooms?.map((r) => <span key={r.id} className="badge-slate">{r.code} · {r.name}</span>)}</div>
      </Card>
      {editing && <UserModal user={editing === "new" ? null : editing} rooms={rooms} onClose={(c) => { setEditing(null); c && reload(); }} />}
    </div>
  );
}

export function Audit() {
  const [action, setAction] = useState("");
  const [logs] = useLoad(() => get(`/audit-logs?limit=300${action ? `&action=${action}` : ""}`), [action]);
  return (
    <Card title="Nhật ký thao tác" actions={
      <select className="input w-44" value={action} onChange={(e) => setAction(e.target.value)}>
        <option value="">Tất cả</option><option value="auth">Đăng nhập</option><option value="exam">Ca thi</option>
        <option value="checkin">Kiểm tra vào phòng</option><option value="device">Thiết bị</option>
        <option value="student">Sinh viên</option><option value="user">Người dùng</option><option value="alert">Cảnh báo</option>
      </select>}>
      {!logs?.length ? <Empty>Chưa có nhật ký</Empty> : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>Thời gian</th><th>Người dùng</th><th>Thao tác</th><th>Đối tượng</th><th>IP</th><th>Chi tiết</th></tr></thead>
            <tbody>{logs.map((l) => (
              <tr key={l.id}>
                <td className="whitespace-nowrap text-slate-500">{fmtDateTime(l.ts)}</td><td>{l.user_email || "–"}</td>
                <td><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs dark:bg-slate-800">{l.action}</code></td>
                <td>{l.target}</td><td className="text-xs">{l.ip}</td>
                <td className="max-w-md truncate text-xs text-slate-500" title={l.detail ? JSON.stringify(l.detail) : ""}>{l.detail ? JSON.stringify(l.detail) : ""}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function Account() {
  const { user } = useAuth();
  const [f, setF] = useState({ old_password: "", new_password: "" });
  const [msg, setMsg] = useState(null);
  const [error, setError] = useState(null);
  return (
    <Card title="Tài khoản" className="max-w-lg">
      <p className="mb-4 text-sm">{user.full_name} · {user.email} · <Badge map={ROLE} value={user.role} /></p>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        try {
          await patch("/users/me/password", f);
          setMsg("Đã đổi mật khẩu");
          setError(null);
          setF({ old_password: "", new_password: "" });
        } catch (err) {
          setError(err);
        }
      }}>
        <Field label="Mật khẩu hiện tại"><input className="input" type="password" required value={f.old_password} onChange={(e) => setF({ ...f, old_password: e.target.value })} /></Field>
        <Field label="Mật khẩu mới (≥ 8 ký tự)"><input className="input" type="password" minLength={8} required value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} /></Field>
        {msg && <p className="text-sm text-emerald-600">{msg}</p>}
        <ErrorText error={error} />
        <button className="btn-primary">Đổi mật khẩu</button>
      </form>
    </Card>
  );
}
