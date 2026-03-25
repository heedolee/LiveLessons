/**
 * frontend/vite.config.js
 * =======================
 * Vite 빌드 도구 설정 파일.
 *
 * 핵심 설정:
 *  - server.proxy: 개발 서버에서 /api/* 요청을 FastAPI(8000포트)로 프록시
 *    → 브라우저의 Same-Origin Policy(CORS) 제한을 우회합니다.
 *    → 프론트엔드 코드에서 fetch('/api/analyze') 처럼 상대 경로를 사용할 수 있습니다.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // React 플러그인 활성화 (JSX 변환, Fast Refresh 등)
  plugins: [react()],

  server: {
    // 개발 서버 포트 (기본값 5173)
    port: 5173,

    proxy: {
      // '/api' 로 시작하는 모든 요청을 FastAPI 백엔드로 전달
      "/api": {
        target: "http://127.0.0.1:8000",  // FastAPI 서버 주소
        changeOrigin: true,               // Host 헤더를 target URL로 변경
        // rewrite 불필요: /api/analyze → /api/analyze 그대로 전달

        // 스트리밍 응답을 위해 버퍼링을 비활성화합니다.
        // (Vite 개발 서버가 응답을 버퍼링하면 스트리밍이 끊겨 보임)
        configure: (proxy, _options) => {
          proxy.on("proxyRes", (proxyRes, req, res) => {
            // Transfer-Encoding: chunked 응답을 즉시 클라이언트로 전달
            proxyRes.headers["x-accel-buffering"] = "no";
          });
        },
      },
    },
  },

  build: {
    // 빌드 결과물 출력 디렉터리 (기본값 dist)
    outDir: "dist",
    // 소스맵 생성 (프로덕션 디버깅용, 오프라인 환경에서는 선택)
    sourcemap: false,
  },
});
