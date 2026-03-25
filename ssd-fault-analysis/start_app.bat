@echo off
REM ==============================================================
REM start_app.bat
REM SSD 지능형 결함 분석 시스템 - Windows 구동 스크립트
REM
REM 실행 방법:
REM   start_app.bat  (관리자 권한 불필요)
REM
REM 전제 조건:
REM   - Python 3.11+  및 가상환경(.venv) 또는 시스템에 의존성 설치 완료
REM   - Node.js 18+  및 npm 설치 완료
REM   - Ollama 설치 완료 (PATH에 ollama.exe 포함)
REM   - MariaDB 서비스 실행 중
REM ==============================================================

SETLOCAL ENABLEDELAYEDEXPANSION
chcp 65001 >nul 2>&1
REM UTF-8 코드페이지 설정 (한글 출력)

echo.
echo =============================================
echo   SSD 지능형 결함 분석 시스템 시작
echo =============================================
echo.

REM ── 스크립트 위치 기준 디렉터리 설정 ─────────────────────
SET SCRIPT_DIR=%~dp0
SET BACKEND_DIR=%SCRIPT_DIR%backend
SET FRONTEND_DIR=%SCRIPT_DIR%frontend
SET VENV_DIR=%SCRIPT_DIR%.venv

REM ── 1. 가상환경 활성화 ────────────────────────────────────
IF EXIST "%VENV_DIR%\Scripts\activate.bat" (
    echo [INFO]  Python 가상환경 활성화: %VENV_DIR%
    CALL "%VENV_DIR%\Scripts\activate.bat"
) ELSE (
    echo [WARN]  가상환경이 없습니다. 시스템 Python을 사용합니다.
    echo [WARN]  가상환경 생성: python -m venv .venv ^&^& pip install -r requirements.txt
)

REM ── 2. Ollama 서비스 시작 확인 ────────────────────────────
echo [INFO]  Ollama 서비스 상태 확인...
curl -sf "http://localhost:11434/api/tags" >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARN]  Ollama 서비스가 실행 중이 아닙니다. 백그라운드로 시작합니다...
    WHERE ollama >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        START "" /B ollama serve
        echo [INFO]  Ollama 시작 중... 5초 대기
        TIMEOUT /T 5 /NOBREAK >nul
    ) ELSE (
        echo [ERROR] ollama.exe 를 찾을 수 없습니다. Ollama를 설치하세요.
        PAUSE
        EXIT /B 1
    )
) ELSE (
    echo [INFO]  Ollama 서비스 실행 중 확인됨.
)

REM ── 3. LLM 모델 확인 (간략 체크) ─────────────────────────
echo [INFO]  LLM 모델 목록 확인...
ollama list 2>nul | findstr /I "qwen2.5:7b" >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARN]  qwen2.5:7b 모델이 없습니다. 오프라인 환경에서는 사전 풀이 필요합니다.
    echo [WARN]  ollama pull qwen2.5:7b
)

ollama list 2>nul | findstr /I "nomic-embed-text" >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARN]  nomic-embed-text 모델이 없습니다.
    echo [WARN]  ollama pull nomic-embed-text
)

REM ── 4. 프론트엔드 의존성 설치 ────────────────────────────
IF NOT EXIST "%FRONTEND_DIR%\node_modules" (
    echo [INFO]  프론트엔드 의존성 설치 중 (npm install)...
    PUSHD "%FRONTEND_DIR%"
    CALL npm install
    POPD
)

REM ── 5. FastAPI 백엔드 서버 시작 (새 CMD 창) ───────────────
echo [INFO]  FastAPI 백엔드 서버 시작 (포트 8000)...
START "SSD-Backend" /MIN cmd /C "cd /D "%BACKEND_DIR%" && uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

REM 백엔드 준비 대기 (최대 15초)
echo [INFO]  백엔드 준비 대기 중...
SET RETRY=0
:WAIT_BACKEND
    TIMEOUT /T 2 /NOBREAK >nul
    curl -sf "http://127.0.0.1:8000/api/health" >nul 2>&1
    IF %ERRORLEVEL% EQU 0 GOTO BACKEND_READY
    SET /A RETRY+=1
    IF !RETRY! LSS 8 GOTO WAIT_BACKEND
    echo [WARN]  백엔드 응답 없음. 계속 진행합니다...
    GOTO BACKEND_DONE
:BACKEND_READY
    echo [INFO]  백엔드 준비 완료!
:BACKEND_DONE

REM ── 6. 프론트엔드 Vite 개발 서버 시작 (새 CMD 창) ─────────
echo [INFO]  Vite 개발 서버 시작 (포트 5173)...
START "SSD-Frontend" /MIN cmd /C "cd /D "%FRONTEND_DIR%" && npm run dev"

REM Vite 초기화 대기
TIMEOUT /T 4 /NOBREAK >nul

REM ── 7. Chrome --app 모드로 실행 ──────────────────────────
SET APP_URL=http://localhost:5173
echo [INFO]  Chrome --app 모드로 실행: %APP_URL%

REM Chrome 실행 경로 탐색 (설치 위치 순서대로 시도)
SET CHROME_FOUND=0
SET CHROME_PATHS=^
    "%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"^
    "%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"^
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"^
    "%PROGRAMFILES%\Chromium\Application\chrome.exe"

FOR %%C IN (%CHROME_PATHS%) DO (
    IF EXIST %%C (
        echo [INFO]  Chrome 경로: %%C
        START "" %%C --app="%APP_URL%" --window-size=1280,800 --disable-extensions --no-first-run --disable-default-apps
        SET CHROME_FOUND=1
        GOTO CHROME_DONE
    )
)

:CHROME_DONE
IF %CHROME_FOUND% EQU 0 (
    echo [WARN]  Chrome을 찾을 수 없습니다.
    echo [WARN]  브라우저에서 직접 접속하세요: %APP_URL%
    REM 기본 브라우저로 열기 시도
    START "" "%APP_URL%"
)

REM ── 8. 완료 메시지 ───────────────────────────────────────
echo.
echo =============================================
echo   SSD 결함 분석 시스템 실행 중
echo   백엔드: http://127.0.0.1:8000
echo   프론트: http://localhost:5173
echo   이 창을 닫으면 서버가 종료됩니다.
echo =============================================
echo.

PAUSE
ENDLOCAL
