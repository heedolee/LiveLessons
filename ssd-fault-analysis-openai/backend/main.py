"""
backend/main.py  (OpenAI 버전)
================================
역할:
  - FastAPI 애플리케이션 진입점
  - CORS 미들웨어 설정
  - lifespan 이벤트: 서버 시작 시 ChatOpenAI, Chroma, RAG 체인 한 번만 초기화
  - 라우터:
      POST /api/analyze  → 자연어 질문 → Text-to-SQL → RAG 스트리밍
      GET  /api/health   → 서버 및 초기화 상태 확인

환경 변수: .env 파일 참조 (OPENAI_API_KEY 필수)
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# .env 가장 먼저 로드 (다른 모듈 임포트 전)
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from database import run_text_to_sql
from llm_service import (
    get_llm,
    get_vectorstore,
    build_windows,
    build_rag_chain,
    stream_analysis,
)

# ──────────────────────────────────────────────
# 로거
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 앱 전역 상태
# ──────────────────────────────────────────────
app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 앱 라이프사이클 이벤트 핸들러.
    서버 시작 시 LLM, VectorDB, RAG 체인을 초기화합니다.
    한 번만 초기화함으로써 매 요청마다 초기화 오버헤드를 방지합니다.
    """
    logger.info("=== SSD 결함 분석 서버 (OpenAI 버전) 시작 ===")

    try:
        # ChatOpenAI 초기화 (OPENAI_API_KEY 검증 포함)
        llm = get_llm()
        app_state["llm"] = llm
        logger.info(f"ChatOpenAI 초기화 완료: {llm.model_name}")

        # Chroma 벡터 스토어 로드
        vectorstore = get_vectorstore()
        app_state["vectorstore"] = vectorstore
        logger.info("Chroma 벡터 스토어 로드 완료")

        # LCEL RAG 체인 빌드
        rag_chain = build_rag_chain(vectorstore, llm)
        app_state["rag_chain"] = rag_chain
        logger.info("LCEL RAG 파이프라인 빌드 완료")

    except EnvironmentError as e:
        # OPENAI_API_KEY 누락 등 환경 설정 오류
        logger.error(f"초기화 실패: {e}")
        app_state["init_error"] = str(e)

    logger.info("=== 초기화 완료. 요청 대기 중 ===")
    yield  # 서버 실행

    logger.info("=== 서버 종료 ===")
    app_state.clear()


# ──────────────────────────────────────────────
# FastAPI 앱 생성
# ──────────────────────────────────────────────
app = FastAPI(
    title="SSD 지능형 결함 분석 시스템 (OpenAI 버전)",
    description=(
        "OpenAI gpt-4o + LangChain LCEL + Chroma RAG 기반 SSD 결함 판정 API.\n"
        "OPENAI_API_KEY 환경 변수(또는 .env 파일)가 필요합니다."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ──────────────────────────────────────────────
# CORS 미들웨어
# ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",    # Vite 개발 서버
        "http://127.0.0.1:5173",
        "http://localhost:4173",    # Vite 프리뷰 서버
        "null",                     # Chrome --app 모드 (origin: null)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# 요청/응답 스키마
# ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    """분석 요청 바디 스키마"""
    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="자연어 질문 (예: '최근 온도가 상승한 원인이 뭐야?')",
        example="최근 온도가 상승한 원인이 뭐야?",
    )
    window_size: int = Field(
        default=15,
        ge=5,
        le=50,
        description="윈도우 크기 (5~50행, 기본 15행)",
    )


# ──────────────────────────────────────────────
# 라우터
# ──────────────────────────────────────────────
@app.get("/api/health", summary="서버 상태 확인")
async def health_check():
    """
    서버와 핵심 컴포넌트의 준비 상태를 반환합니다.
    init_error 키가 있으면 초기화 실패(주로 API 키 누락)를 의미합니다.
    """
    if "init_error" in app_state:
        return {
            "status": "error",
            "error":  app_state["init_error"],
        }
    return {
        "status":            "ok",
        "chat_model":        app_state.get("llm", {}).model_name if app_state.get("llm") else "미초기화",
        "vectorstore_ready": "vectorstore" in app_state,
        "rag_chain_ready":   "rag_chain" in app_state,
    }


@app.post("/api/analyze", summary="SSD 결함 분석 (스트리밍)")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """
    자연어 질문을 받아 아래 파이프라인을 실행하고 결과를 스트리밍합니다:

    1. Text-to-SQL (Auto-Retry, 최대 3회): 자연어 → SQL → DB 실행 → device_id 마스킹
    2. Windowing + 직렬화: 마스킹된 DB 행 → 자연어 Window Context
    3. RAG 분석 (LCEL): Chroma 정상패턴 검색 + gpt-4o 스트리밍 분석

    응답: text/plain; charset=utf-8 (Server-Sent 스트리밍)
    """
    # 초기화 오류 확인
    if "init_error" in app_state:
        raise HTTPException(
            status_code=503,
            detail=f"서버 초기화 오류: {app_state['init_error']}",
        )

    llm       = app_state.get("llm")
    rag_chain = app_state.get("rag_chain")

    if not llm or not rag_chain:
        raise HTTPException(
            status_code=503,
            detail="서버 초기화 중입니다. 잠시 후 다시 시도하세요.",
        )

    # ── Step 1: Text-to-SQL + 마스킹 ──
    try:
        rows = run_text_to_sql(req.question, llm)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        async def empty_stream() -> AsyncGenerator[str, None]:
            yield "쿼리 결과가 없습니다. 질문을 바꾸거나 데이터를 확인하세요."
        return StreamingResponse(
            empty_stream(),
            media_type="text/plain; charset=utf-8",
        )

    # ── Step 2: Windowing ──
    windows = build_windows(rows, window_size=req.window_size)

    # ── Step 3: 스트리밍 RAG 분석 ──
    return StreamingResponse(
        content=stream_analysis(windows, req.question, rag_chain),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",   # Nginx 리버스 프록시 버퍼링 비활성화
            "Cache-Control":     "no-cache",
        },
    )
