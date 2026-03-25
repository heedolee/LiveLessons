#!/usr/bin/env bash
# ==============================================================
# start_app.sh
# SSD 지능형 결함 분석 시스템 - Linux/macOS 구동 스크립트
#
# 실행 방법:
#   chmod +x start_app.sh
#   ./start_app.sh
#
# 전제 조건:
#   - Python 3.11+ 가상환경 또는 시스템에 의존성 설치 완료
#   - Node.js 18+ 및 npm/pnpm 설치 완료
#   - Ollama 서비스가 실행 중이거나 이 스크립트가 자동 시작
#   - MariaDB 서비스 실행 중
# ==============================================================

set -euo pipefail  # 오류 발생 시 즉시 종료, 미정의 변수 사용 금지

# ── 색상 출력 헬퍼 ──────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'  # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 스크립트 위치 기준 경로 설정 ───────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── 1. 가상환경 활성화 ──────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    log_info "Python 가상환경 활성화: $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
else
    log_warn "가상환경이 없습니다. 시스템 Python을 사용합니다."
    log_warn "가상환경 생성: python -m venv $VENV_DIR && pip install -r requirements.txt"
fi

# ── 2. Ollama 서비스 확인 및 자동 시작 ─────────────────────
log_info "Ollama 서비스 상태 확인..."
if ! curl -sf "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    log_warn "Ollama 서비스가 실행 중이 아닙니다. 백그라운드로 시작합니다..."
    if command -v ollama &>/dev/null; then
        ollama serve &>/dev/null &
        OLLAMA_PID=$!
        log_info "Ollama 시작됨 (PID: $OLLAMA_PID). 준비 대기 중..."
        sleep 5  # Ollama 초기화 대기
    else
        log_error "ollama 명령어를 찾을 수 없습니다. Ollama를 설치하세요."
        exit 1
    fi
else
    log_info "Ollama 서비스 실행 중 확인됨."
fi

# ── 3. 필요한 Ollama 모델 확인 ──────────────────────────────
log_info "LLM 모델(qwen2.5:7b) 확인..."
if ! ollama list 2>/dev/null | grep -q "qwen2.5:7b"; then
    log_warn "qwen2.5:7b 모델이 없습니다. 풀링 시작 (오프라인 환경에서는 사전 풀 필요)..."
    ollama pull qwen2.5:7b || log_warn "모델 풀 실패. 이미 로컬에 있으면 무시하세요."
fi

log_info "임베딩 모델(nomic-embed-text) 확인..."
if ! ollama list 2>/dev/null | grep -q "nomic-embed-text"; then
    log_warn "nomic-embed-text 모델이 없습니다. 풀링 시작..."
    ollama pull nomic-embed-text || log_warn "모델 풀 실패."
fi

# ── 4. 프론트엔드 의존성 설치 및 빌드 (첫 실행 또는 node_modules 없을 때) ──
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log_info "프론트엔드 의존성 설치 중 (npm install)..."
    cd "$FRONTEND_DIR" && npm install
    cd "$SCRIPT_DIR"
fi

# ── 5. FastAPI 백엔드 서버 시작 (백그라운드) ────────────────
log_info "FastAPI 백엔드 서버 시작 (포트 8000)..."
cd "$BACKEND_DIR"
uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!
log_info "백엔드 PID: $BACKEND_PID"
cd "$SCRIPT_DIR"

# 백엔드 준비 대기 (최대 15초)
log_info "백엔드 준비 대기 중..."
for i in $(seq 1 15); do
    if curl -sf "http://127.0.0.1:8000/api/health" > /dev/null 2>&1; then
        log_info "백엔드 준비 완료!"
        break
    fi
    sleep 1
    if [ "$i" -eq 15 ]; then
        log_warn "백엔드 응답 없음 (계속 시도 중...)."
    fi
done

# ── 6. 프론트엔드 Vite 개발 서버 시작 (백그라운드) ──────────
log_info "Vite 개발 서버 시작 (포트 5173)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
log_info "프론트엔드 PID: $FRONTEND_PID"
cd "$SCRIPT_DIR"

sleep 3  # Vite 초기화 대기

# ── 7. Chrome --app 모드로 실행 ─────────────────────────────
APP_URL="http://localhost:5173"
log_info "Chrome --app 모드로 실행: $APP_URL"

# Chrome/Chromium 실행파일 탐색
CHROME_BIN=""
for candidate in \
    "google-chrome" \
    "google-chrome-stable" \
    "chromium-browser" \
    "chromium" \
    "/usr/bin/google-chrome" \
    "/usr/bin/chromium-browser" \
    "/snap/bin/chromium"; do
    if command -v "$candidate" &>/dev/null; then
        CHROME_BIN="$candidate"
        break
    fi
done

if [ -n "$CHROME_BIN" ]; then
    log_info "Chrome 실행: $CHROME_BIN"
    "$CHROME_BIN" \
        --app="$APP_URL" \
        --window-size=1280,800 \
        --disable-extensions \
        --no-first-run \
        --disable-default-apps \
        &>/dev/null &
    log_info "Chrome 앱 모드로 실행됨."
else
    log_warn "Chrome을 찾을 수 없습니다. 브라우저를 수동으로 열어주세요: $APP_URL"
fi

# ── 8. 종료 처리 ─────────────────────────────────────────────
log_info ""
log_info "============================================"
log_info "  SSD 결함 분석 시스템 실행 중"
log_info "  백엔드: http://127.0.0.1:8000"
log_info "  프론트: http://localhost:5173"
log_info "  종료:   Ctrl+C"
log_info "============================================"

# Ctrl+C 시 자식 프로세스 정리
cleanup() {
    log_info "종료 중..."
    kill "$BACKEND_PID"  2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

# 백그라운드 프로세스가 끝날 때까지 대기
wait "$BACKEND_PID" "$FRONTEND_PID"
