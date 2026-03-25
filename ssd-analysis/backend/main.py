"""
backend/main.py
===============
FastAPI 애플리케이션 진입점.

포함 내용:
  - lifespan: 서버 시작 시 LLM, Chroma, RAG 체인 초기화 (1회)
  - CORS 미들웨어: Vite 개발 서버 허용
  - GET  /api/health  → 서버 및 컴포넌트 준비 상태 확인
  - POST /api/analyze → 자연어 → Text-to-SQL → RAG 스트리밍 응답
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

# .env 최우선 로드
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db, run_text_to_sql
from llm_service import (
    build_rag_chain,
    build_windows,
    get_llm,
    get_vectorstore,
    stream_analysis,
)

# ──────────────────────────────────────────────
# 로거
# ──────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream  = sys.stdout,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 앱 전역 상태 저장소
# ──────────────────────────────────────────────
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 앱 라이프사이클 핸들러.
    서버 시작 시 무거운 리소스를 한 번만 초기화하여
    매 요청마다 초기화 오버헤드를 방지합니다.
    """
    logger.info("=== SSD 결함 분석 서버 (SQLAlchemy + OpenAI) 시작 ===")

    try:
        llm = get_llm()
        _state["llm"] = llm
        logger.info(f"ChatOpenAI 초기화 완료: {llm.model_name}")

        vs = get_vectorstore()
        _state["vectorstore"] = vs
        logger.info("Chroma 벡터 스토어 초기화 완료")

        chain = build_rag_chain(vs, llm)
        _state["rag_chain"] = chain
        logger.info("LCEL RAG 파이프라인 빌드 완료")

    except EnvironmentError as exc:
        _state["init_error"] = str(exc)
        logger.error(f"초기화 실패: {exc}")

    logger.info("=== 서버 준비 완료 ===")
    yield
    logger.info("=== 서버 종료 ===")
    _state.clear()


# ──────────────────────────────────────────────
# FastAPI 앱
# ──────────────────────────────────────────────
app = FastAPI(
    title       = "SSD 지능형 결함 분석 시스템",
    description = (
        "SQLAlchemy ORM + OpenAI gpt-4o + LangChain LCEL + Chroma RAG.\n"
        "OPENAI_API_KEY 와 DB_URL 환경 변수(또는 .env 파일)가 필요합니다."
    ),
    version     = "2.0.0",
    lifespan    = lifespan,
)

# ──────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = [
        "http://localhost:5173",   # Vite 개발 서버
        "http://127.0.0.1:5173",
        "http://localhost:4173",   # Vite 프리뷰
        "null",                    # Chrome --app 모드
    ],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ──────────────────────────────────────────────
# 요청/응답 스키마
# ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    """POST /api/analyze 요청 바디."""
    question: str = Field(
        ...,
        min_length  = 1,
        max_length  = 500,
        description = "자연어 질문",
        example     = "최근 온도가 상승한 원인이 뭐야?",
    )
    window_size: int = Field(
        default     = 15,
        ge          = 5,
        le          = 50,
        description = "시계열 윈도우 크기 (기본 15행)",
    )


# ──────────────────────────────────────────────
# 라우터
# ──────────────────────────────────────────────
@app.get("/api/health", summary="서버 상태 확인", tags=["관리"])
async def health():
    """
    서버와 핵심 컴포넌트의 준비 상태를 JSON으로 반환합니다.
    init_error 키가 있으면 초기화 실패 (주로 API 키 누락)를 의미합니다.
    """
    if "init_error" in _state:
        return {"status": "error", "error": _state["init_error"]}

    llm = _state.get("llm")
    return {
        "status"           : "ok",
        "chat_model"       : llm.model_name if llm else "미초기화",
        "vectorstore_ready": "vectorstore" in _state,
        "rag_chain_ready"  : "rag_chain"   in _state,
    }


@app.post("/api/analyze", summary="SSD 결함 분석 (스트리밍)", tags=["분석"])
async def analyze(
    req: AnalyzeRequest,
    db : Session = Depends(get_db),   # SQLAlchemy 세션 DI
) -> StreamingResponse:
    """
    자연어 질문을 받아 아래 파이프라인을 실행하고 결과를 스트리밍합니다.

    파이프라인:
      1. Text-to-SQL (SQLAlchemy text() + Auto-Retry)
         → SELECT 쿼리 생성 → db.execute() → Row 목록
      2. Pydantic 변환 + device_id 가명화
      3. 시계열 Windowing (window_size 행 단위)
      4. LCEL RAG (Chroma 정상 패턴 검색 + gpt-4o 스트리밍 분석)

    응답: text/plain; charset=utf-8 (청크 단위 스트리밍)
    """
    if "init_error" in _state:
        raise HTTPException(503, detail=f"초기화 오류: {_state['init_error']}")

    llm       = _state.get("llm")
    rag_chain = _state.get("rag_chain")
    if not llm or not rag_chain:
        raise HTTPException(503, detail="서버 초기화 중입니다. 잠시 후 재시도하세요.")

    # ── Step 1: Text-to-SQL + Pydantic 변환 + 익명화 ──────────
    try:
        rows = run_text_to_sql(req.question, llm, db)
    except RuntimeError as exc:
        raise HTTPException(500, detail=str(exc))

    if not rows:
        async def _empty() -> AsyncGenerator[str, None]:
            yield "쿼리 결과가 없습니다. 질문을 바꾸거나 데이터를 확인하세요."
        return StreamingResponse(_empty(), media_type="text/plain; charset=utf-8")

    # ── Step 2: 시계열 Windowing ──────────────────────────────
    windows = build_windows(rows, window_size=req.window_size)

    # ── Step 3: 스트리밍 RAG 분석 ─────────────────────────────
    return StreamingResponse(
        content    = stream_analysis(windows, req.question, rag_chain),
        media_type = "text/plain; charset=utf-8",
        headers    = {
            "X-Accel-Buffering": "no",    # Nginx 버퍼링 비활성화
            "Cache-Control"    : "no-cache",
        },
    )
