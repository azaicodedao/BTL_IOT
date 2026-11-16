import { useEffect, useState } from "react";
import { get, logout } from "./api";
import { useAuth } from "./auth";
import { useLive, useLiveStatus } from "./live";
import { Account, Audit, Users } from "./pages/Admin";
import Alerts from "./pages/Alerts";
import Dashboard from "./pages/Dashboard";
import Devices from "./pages/Devices";
import ExamDetail from "./pages/ExamDetail";
import Exams from "./pages/Exams";
import Login from "./pages/Login";
import People from "./pages/People";

// Menu theo ma trận quyền (tài liệu mục 11)
const PAGES = [
  { id: "dashboard", label: "Tổng quan", icon: "▦", el: Dashboard, roles: ["admin", "operator", "viewer"] },
  { id: "exams", label: "Ca thi", icon: "◉", el: Exams, roles: ["admin", "operator", "viewer"] },
  { id: "alerts", label: "Cảnh báo", icon: "⚠", el: Alerts, roles: ["admin", "operator", "viewer"] },
  { id: "devices", label: "Thiết bị", icon: "⌁", el: Devices, roles: ["admin", "operator", "viewer"] },
  { id: "people", label: "Sinh viên & danh sách thi", icon: "☺", el: People, roles: ["admin", "operator"] },
  { id: "users", label: "Người dùng & phòng", icon: "⚙", el: Users, roles: ["admin"] },
  { id: "audit", label: "Nhật ký", icon: "≡", el: Audit, roles: ["admin"] },
  { id: "account", label: "Tài khoản", icon: "●", el: Account, roles: ["admin", "operator", "viewer"] },
];

function useHashRoute() {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "dashboard").split("/");
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function Toasts() {
  const [items, setItems] = useState([]);
  const push = (text, tone) => {
    const id = Math.random();
    setItems((p) => [...p.slice(-3), { id, text, tone }]);
    setTimeout(() => setItems((p) => p.filter((x) => x.id !== id)), 6000);
  };
  useLive("alert", (m) => m.type === "alert.created" && push(m.data.message, m.data.severity === "high" ? "red" : "amber"));
  const border = { red: "border-l-red-500", amber: "border-l-amber-500", green: "border-l-emerald-500" };
  return (
    <div className="fixed bottom-4 right-4 z-50 grid w-[min(380px,calc(100vw-2rem))] gap-2" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`rounded-lg border border-l-4 border-slate-200 bg-white p-3 text-sm shadow-lg dark:border-slate-800 dark:bg-slate-900 ${border[t.tone]}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const { user } = useAuth();
  const [routeId, param] = useHashRoute();
  const live = useLiveStatus();
  const [openAlerts, setOpenAlerts] = useState(0);
  const [menu, setMenu] = useState(false);

  const refreshCount = () => user && get("/alerts?state=open&limit=500").then((a) => setOpenAlerts(a.length)).catch(() => {});
  useEffect(() => {
    refreshCount();
  }, [user?.id]);
  useLive("alert", refreshCount);

  if (!user) return <Login />;

  const pages = PAGES.filter((p) => p.roles.includes(user.role));
  const page = pages.find((p) => p.id === routeId) || pages[0];
  const showDetail = page.id === "exams" && param;
  const Page = page.el;

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
      {menu && <div className="fixed inset-0 z-30 bg-slate-950/50 lg:hidden" onClick={() => setMenu(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-slate-200 bg-white px-3 py-4 transition-transform
                         dark:border-slate-800 dark:bg-slate-900 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${menu ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="mb-5 px-2">
          <div className="text-base font-bold">Phòng thi thông minh</div>
          <div className="text-xs text-slate-500">Kiểm tra vào phòng · Giám sát thi</div>
        </div>
        <nav className="grid gap-0.5">
          {pages.map((p) => (
            <a key={p.id} href={`#/${p.id}`} onClick={() => setMenu(false)}
               className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition
                           ${p.id === page.id ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
              <span className="w-4 text-center opacity-80">{p.icon}</span>
              <span className="flex-1">{p.label}</span>
              {p.id === "alerts" && openAlerts > 0 && (
                <span className="rounded-full bg-red-600 px-1.5 text-xs text-white">{openAlerts}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="mt-auto rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <div className="text-sm font-medium">{user.full_name}</div>
          <div className="truncate text-xs text-slate-500">{user.email}</div>
          <button className="btn-ghost btn-sm mt-2 -ml-2" onClick={logout}>Đăng xuất</button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90 lg:px-6">
          <button className="btn-ghost btn-sm lg:hidden" onClick={() => setMenu(true)} aria-label="Mở menu">☰</button>
          <h1 className="flex-1 text-lg font-semibold">{showDetail ? "Chi tiết ca thi" : page.label}</h1>
          <span className="inline-flex items-center gap-2 text-xs text-slate-500">
            <span className={`h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-red-500"}`} />
            {live ? "Trực tiếp" : "Đang kết nối lại…"}
          </span>
        </header>
        <main className="p-4 lg:p-6">
          {showDetail ? <ExamDetail key={param} id={Number(param)} /> : <Page key={page.id} />}
        </main>
      </div>
      <Toasts />
    </div>
  );
}
