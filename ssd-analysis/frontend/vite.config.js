/**
 * frontend/vite.config.js
 * ========================
 * Vite 설정.
 * - /api/* 요청을 FastAPI(8000포트)로 프록시하여 CORS 우회.
 * - 스트리밍 응답 버퍼링 방지 설정 포함.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

  server: {
    port: 5173,
    proxy: {
      "/api": {
        target      : "http://127.0.0.1:8000",
        changeOrigin: true,
        configure   : (proxy) => {
          // gpt-4o 청크가 즉시 클라이언트에 전달되도록 버퍼링 비활성화
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["x-accel-buffering"] = "no";
            proxyRes.headers["cache-control"]     = "no-cache";
          });
          proxy.on("error", (err) => {
            console.error("[Vite Proxy]", err.message);
          });
        },
      },
    },
  },

  build: { outDir: "dist", sourcemap: false },
});
