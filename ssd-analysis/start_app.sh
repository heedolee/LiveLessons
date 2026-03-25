#!/usr/bin/env bash
# ==============================================================
# start_app.sh  —  SSD 결함 분석 시스템 통합 실행 스크립트
# ==============================================================
# 실행:
#   chmod +x start_app.sh && ./start_app.sh
#
# 사전 조건:
#   1. .env 파일에 OPENAI_API_KEY, DB_URL 설정 완료
#   2. Python 3.11+ (가상환경 권장)
#   3. Node.js 18+ / npm
#   4. MariaDB 서비스 실행 중
# ==============================================================

set -euo pipefail

# ── 색상 헬퍼 ──────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
info()  { echo -e "${G}[INFO]${N}  $*"; }
warn()  { echo -e "${Y}[WARN]${N}  $*"; }
error() { echo -e "${R}[ERROR]${N} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
VENV_DIR="$SCRIPT_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"

# ── 1. .env 검증 ───────────────────────────────────────────
[ -f "$ENV_FILE" ] || { error ".env 파일 없음. cp .env.example .env 후 키 입력."; exit 1; }

# shellcheck disable=SC1090
source <(grep -v "^#\|^$" "$ENV_FILE" | sed 's/ *= */=/g' | sed 's/export //g')

[[ -z "${OPENAI_API_KEY:-}" || "$OPENAI_API_KEY" == sk-proj-your* ]] && {
  error "OPENAI_API_KEY 가 .env 에 올바르게 설정되지 않았습니다."; exit 1; }

[[ -z "${DB_URL:-}" ]] && {
  error "DB_URL 이 .env 에 설정되지 않았습니다."; exit 1; }

info "환경 변수 검증 완료"

# ── 2. Python 가상환경 활성화 ──────────────────────────────
if [ -d "$VENV_DIR" ]; then
    info "가상환경 활성화: $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
else
    warn "가상환경 없음. 시스템 Python 사용."
    warn "생성: python -m venv .venv && pip install -r requirements.txt"
fi

# ── 3. 프론트엔드 의존성 설치 ──────────────────────────────
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    info "npm install 실행 중..."
    cd "$FRONTEND_DIR" && npm install && cd "$SCRIPT_DIR"
fi

# ── 4. FastAPI 백엔드 시작 ────────────────────────────────
info "FastAPI 백엔드 시작 (포트 8000)..."
cd "$BACKEND_DIR"
uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!
cd "$SCRIPT_DIR"
info "백엔드 PID: $BACKEND_PID"

# 준비 대기 (최대 20초)
info "백엔드 준비 대기 중..."
for i in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:8000/api/health" &>/dev/null; then
        HEALTH=$(curl -s "http://127.0.0.1:8000/api/health")
        if echo "$HEALTH" | grep -q '"status":"error"'; then
            error "백엔드 초기화 오류: $HEALTH"
            kill "$BACKEND_PID" 2>/dev/null; exit 1
        fi
        info "백엔드 준비 완료!"
        break
    fi
    sleep 1
    [ "$i" -eq 20 ] && warn "백엔드 응답 없음 — 계속 진행"
done

# ── 5. Vite 개발 서버 시작 ───────────────────────────────
info "Vite 개발 서버 시작 (포트 5173)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"
sleep 3

# ── 6. Chrome --app 모드 실행 ────────────────────────────
APP_URL="http://localhost:5173"
CHROME_BIN=""
for c in google-chrome google-chrome-stable chromium-browser chromium \
          /usr/bin/google-chrome /usr/bin/chromium-browser /snap/bin/chromium; do
    command -v "$c" &>/dev/null && { CHROME_BIN="$c"; break; }
done

if [ -n "$CHROME_BIN" ]; then
    "$CHROME_BIN" \
        --app="$APP_URL" \
        --window-size=1280,820 \
        --disable-extensions \
        --no-first-run \
        --disable-default-apps \
        &>/dev/null &
    info "Chrome --app 모드 실행: $APP_URL"
else
    warn "Chrome 없음 — 브라우저에서 직접 접속: $APP_URL"
fi

# ── 7. 상태 출력 ────────────────────────────────────────
echo ""
info "================================================"
info "  SSD 결함 분석 시스템 실행 중"
info "  백엔드 API  : http://127.0.0.1:8000/docs"
info "  프론트엔드  : http://localhost:5173"
info "  종료        : Ctrl+C"
info "================================================"

# Ctrl+C 시 정리
cleanup() {
    info "종료 중..."
    kill "$BACKEND_PID"  2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM
wait "$BACKEND_PID" "$FRONTEND_PID"
