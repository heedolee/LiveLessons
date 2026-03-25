@echo off
REM ==============================================================
REM start_app.bat  (OpenAI 버전)
REM SSD 지능형 결함 분석 시스템 - Windows 구동 스크립트
REM
REM 실행 전 준비:
REM   1. .env.example → .env 복사 후 OPENAI_API_KEY 입력
REM      copy .env.example .env
REM      메모장으로 .env 열어 API 키 입력
REM   2. Python 3.11+ 및 pip install -r requirements.txt
REM   3. Node.js 18+ 및 npm
REM   4. MariaDB 서비스 실행 중
REM ==============================================================

SETLOCAL ENABLEDELAYEDEXPANSION
chcp 65001 >nul 2>&1

echo.
echo =============================================
echo   SSD 결함 분석 시스템 (OpenAI 버전) 시작
echo =============================================
echo.

SET SCRIPT_DIR=%~dp0
SET BACKEND_DIR=%SCRIPT_DIR%backend
SET FRONTEND_DIR=%SCRIPT_DIR%frontend
SET VENV_DIR=%SCRIPT_DIR%.venv
SET ENV_FILE=%SCRIPT_DIR%.env

REM ── 1. .env 파일 존재 확인 ──────────────────────────────
IF NOT EXIST "%ENV_FILE%" (
    echo [ERROR] .env 파일이 없습니다.
    echo [ERROR] copy .env.example .env 실행 후 OPENAI_API_KEY 를 입력하세요.
    PAUSE
    EXIT /B 1
)

REM OPENAI_API_KEY 값 간략 확인 (플레이스홀더 체크)
findstr /I "sk-proj-your_api_key_here" "%ENV_FILE%" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [ERROR] .env 의 OPENAI_API_KEY 가 기본값입니다.
    echo [ERROR] 실제 OpenAI API 키를 입력하세요.
    PAUSE
    EXIT /B 1
)
echo [INFO]  .env 파일 확인 완료.

REM ── 2. 가상환경 활성화 ────────────────────────────────
IF EXIST "%VENV_DIR%\Scripts\activate.bat" (
    echo [INFO]  가상환경 활성화: %VENV_DIR%
    CALL "%VENV_DIR%\Scripts\activate.bat"
) ELSE (
    echo [WARN]  가상환경 없음. 시스템 Python 사용.
    echo [WARN]  python -m venv .venv ^&^& pip install -r requirements.txt
)

REM ── 3. 프론트엔드 의존성 설치 ────────────────────────
IF NOT EXIST "%FRONTEND_DIR%\node_modules" (
    echo [INFO]  npm install 실행 중...
    PUSHD "%FRONTEND_DIR%"
    CALL npm install
    POPD
)

REM ── 4. FastAPI 백엔드 시작 ───────────────────────────
echo [INFO]  FastAPI 백엔드 서버 시작 (포트 8000)...
START "SSD-Backend-OpenAI" /MIN cmd /C "cd /D "%BACKEND_DIR%" && uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

REM 백엔드 준비 대기 (최대 20초)
echo [INFO]  백엔드 준비 대기 중...
SET RETRY=0
:WAIT_BACKEND
    TIMEOUT /T 2 /NOBREAK >nul
    curl -sf "http://127.0.0.1:8000/api/health" >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        REM 초기화 오류 여부 확인
        curl -s "http://127.0.0.1:8000/api/health" | findstr /I "error" >nul 2>&1
        IF %ERRORLEVEL% EQU 0 (
            echo [ERROR] 백엔드 초기화 오류 - OPENAI_API_KEY 또는 DB 설정을 확인하세요.
            PAUSE
            EXIT /B 1
        )
        GOTO BACKEND_READY
    )
    SET /A RETRY+=1
    IF !RETRY! LSS 10 GOTO WAIT_BACKEND
    echo [WARN]  백엔드 응답 없음. 계속 진행합니다.
    GOTO BACKEND_DONE
:BACKEND_READY
    echo [INFO]  백엔드 준비 완료!
:BACKEND_DONE

REM ── 5. Vite 개발 서버 시작 ───────────────────────────
echo [INFO]  Vite 개발 서버 시작 (포트 5173)...
START "SSD-Frontend-OpenAI" /MIN cmd /C "cd /D "%FRONTEND_DIR%" && npm run dev"
TIMEOUT /T 4 /NOBREAK >nul

REM ── 6. Chrome --app 모드 실행 ───────────────────────
SET APP_URL=http://localhost:5173
echo [INFO]  Chrome --app 모드 실행: %APP_URL%

SET CHROME_FOUND=0
FOR %%C IN (
    "%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"
    "%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    "%PROGRAMFILES%\Chromium\Application\chrome.exe"
) DO (
    IF EXIST %%C (
        START "" %%C --app="%APP_URL%" --window-size=1280,820 ^
            --disable-extensions --no-first-run --disable-default-apps
        SET CHROME_FOUND=1
        GOTO CHROME_DONE
    )
)
:CHROME_DONE
IF %CHROME_FOUND% EQU 0 (
    echo [WARN]  Chrome 없음. 기본 브라우저로 열기 시도...
    START "" "%APP_URL%"
)

REM ── 7. 완료 메시지 ──────────────────────────────────
echo.
echo =============================================
echo   SSD 결함 분석 시스템 (OpenAI 버전) 실행 중
echo   백엔드 API: http://127.0.0.1:8000/docs
echo   프론트엔드: http://localhost:5173
echo   이 창을 닫으면 종료됩니다.
echo =============================================
echo.

PAUSE
ENDLOCAL
