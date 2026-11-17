import { useState } from "react";
import { login } from "../api";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-gradient-to-br from-slate-100 to-blue-50 p-4 dark:from-slate-950 dark:to-slate-900">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
        <div>
          <h1 className="text-xl font-bold">Phòng thi thông minh</h1>
          <p className="text-sm text-slate-500">Kiểm tra khuôn mặt vào phòng · Giám sát gian lận · Vi khí hậu</p>
        </div>
        <label className="block">
          <span className="label">Email</span>
          <input className="input" type="email" autoComplete="username" value={email} required autoFocus
                 onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">Mật khẩu</span>
          <input className="input" type="password" autoComplete="current-password" value={password} required
                 onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button className="btn-primary w-full py-2" disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}</button>
      </form>
    </main>
  );
}
