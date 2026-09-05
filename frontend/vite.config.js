import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // 开发时由 Vite 转发同源 /api 请求，避免浏览器跨域并连接到本机 FastAPI。
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
