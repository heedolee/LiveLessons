/**
 * frontend/vite.config.js  (OpenAI 버전)
 * =========================================
 * Vite 빌드 도구 설정.
 *
 * 핵심:
 *  - server.proxy: /api/* 요청을 FastAPI(8000포트)로 프록시
 *    → 브라우저 CORS 제한을 우회합니다.
 *    → 프론트엔드에서 fetch('/api/analyze') 상대 경로 사용 가능.
 *  - configure 콜백: 스트리밍 응답의 버퍼링을 방지하여
 *    gpt-4o 토큰이 실시간으로 클라이언트에 전달됩니다.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

  server: {
    port: 5173,   // 개발 서버 포트

    proxy: {
      // /api 로 시작하는 요청 → FastAPI 백엔드로 프록시
      "/api": {
        target:       "http://127.0.0.1:8000",
        changeOrigin: true,  // Host 헤더를 target URL로 변경 (CORS 우회)

        // 스트리밍(chunked transfer)을 위한 버퍼링 비활성화
        configure: (proxy, _options) => {
          proxy.on("proxyRes", (proxyRes, _req, _res) => {
            // gpt-4o 토큰이 즉시 클라이언트로 전달되도록 설정
            proxyRes.headers["x-accel-buffering"] = "no";
            proxyRes.headers["cache-control"]     = "no-cache";
          });

          // 프록시 에러 로깅
          proxy.on("error", (err, _req, _res) => {
            console.error("[Vite Proxy Error]", err.message);
          });
        },
      },
    },
  },

  build: {
    outDir:    "dist",
    sourcemap: false,  // 프로덕션 배포 시 소스맵 비활성화
  },
});
