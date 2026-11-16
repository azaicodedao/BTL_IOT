import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: frontend cổng 5173, chuyển /api (REST + WebSocket) sang server trung tâm cổng 9000.
// Dùng 127.0.0.1 thay vì localhost: trên Windows "localhost" thử IPv6 trước -> chậm ~2s mỗi request.
const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:9000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": { target: BACKEND, ws: true } },
  },
});
