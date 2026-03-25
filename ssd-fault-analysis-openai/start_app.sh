#!/usr/bin/env bash
# ==============================================================
# start_app.sh  (OpenAI 버전)
# SSD 지능형 결함 분석 시스템 - Linux/macOS 구동 스크립트
#
# 실행 방법:
#   chmod +x start_app.sh
#   ./start_app.sh
#
# 전제 조건:
#   1. .env 파일에 OPENAI_API_KEY 가 설정되어 있어야 합니다.
#      cp .env.example .env && vi .env
#   2. Python 3.11+ 및 의존성 설치 완료
#   3. Node.js 18+ 및 npm 설치 완료
#   4. MariaDB 서비스 실행 중
# ==============================================================

set -euo pipefail

# ── 색상 헬퍼 ──────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
VENV_DIR="$SCRIPT_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"

# ── 1. .env 파일 존재 확인 ─────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    log_error ".env 파일이 없습니다."
    log_error "cp .env.example .env 실행 후 OPENAI_API_KEY 를 설정하세요."
    exit 1
fi

# OPENAI_API_KEY 값 확인 (빈 값 또는 플레이스홀더 체크)
# shellcheck disable=SC1090
source <(grep -v "^#" "$ENV_FILE" | sed 's/ *= */=/g')
if [[ -z "${OPENAI_API_KEY:-}" || "$OPENAI_API_KEY" == "sk-proj-your_api_key_here" ]]; then
    log_error "OPENAI_API_KEY 가 .env 에 설정되지 않았습니다."
    log_error ".env 파일을 열어 실제 API 키를 입력하세요."
    exit 1
fi
log_info "OPENAI_API_KEY 확인 완료 (sk-...${OPENAI_API_KEY: -4})"

# ── 2. 가상환경 활성화 ─────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    log_info "Python 가상환경 활성화: $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
else
    log_warn "가상환경 없음. 시스템 Python 사용."
    log_warn "생성 방법: python -m venv $VENV_DIR && pip install -r requirements.txt"
fi

# ── 3. 프론트엔드 의존성 설치 ──────────────────────────────
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log_info "npm install 실행 중..."
    cd "$FRONTEND_DIR" && npm install
    cd "$SCRIPT_DIR"
fi

# ── 4. FastAPI 백엔드 서버 시작 ────────────────────────────
log_info "FastAPI 백엔드 서버 시작 (포트 8000)..."
cd "$BACKEND_DIR"
uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!
log_info "백엔드 PID: $BACKEND_PID"
cd "$SCRIPT_DIR"

# 백엔드 준비 대기 (최대 20초)
log_info "백엔드 준비 대기 중..."
for i in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:8000/api/health" > /dev/null 2>&1; then
        HEALTH=$(curl -s "http://127.0.0.1:8000/api/health")
        if echo "$HEALTH" | grep -q '"status":"error"'; then
            log_error "백엔드 초기화 오류: $HEALTH"
            kill "$BACKEND_PID" 2>/dev/null || true
            exit 1
        fi
        log_info "백엔드 준비 완료!"
        break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
        log_warn "백엔드 응답 없음 (계속 진행)."
    fi
done

# ── 5. Vite 개발 서버 시작 ────────────────────────────────
log_info "Vite 개발 서버 시작 (포트 5173)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
log_info "프론트엔드 PID: $FRONTEND_PID"
cd "$SCRIPT_DIR"
sleep 3

# ── 6. Chrome --app 모드 실행 ─────────────────────────────
APP_URL="http://localhost:5173"
CHROME_BIN=""
for c in google-chrome google-chrome-stable chromium-browser chromium \
          /usr/bin/google-chrome /usr/bin/chromium-browser /snap/bin/chromium; do
    if command -v "$c" &>/dev/null; then CHROME_BIN="$c"; break; fi
done

if [ -n "$CHROME_BIN" ]; then
    "$CHROME_BIN" --app="$APP_URL" --window-size=1280,820 \
        --disable-extensions --no-first-run --disable-default-apps &>/dev/null &
    log_info "Chrome 앱 모드 실행: $APP_URL"
else
    log_warn "Chrome 없음. 브라우저에서 수동 접속: $APP_URL"
fi

# ── 7. 상태 출력 및 종료 대기 ─────────────────────────────
echo ""
log_info "================================================"
log_info "  SSD 결함 분석 시스템 (OpenAI 버전) 실행 중"
log_info "  백엔드: http://127.0.0.1:8000/docs"
log_info "  프론트: http://localhost:5173"
log_info "  종료:   Ctrl+C"
log_info "================================================"

cleanup() {
    log_info "서버 종료 중..."
    kill "$BACKEND_PID"  2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM
wait "$BACKEND_PID" "$FRONTEND_PID"
