"""
backend/main.py
===============
역할:
  - FastAPI 애플리케이션 진입점
  - CORS 미들웨어 설정 (로컬 React 개발 서버 허용)
  - 라우터 정의:
      POST /api/analyze  → 자연어 질문 수신 → Text-to-SQL → RAG 스트리밍 응답
      GET  /api/health   → 서버 상태 확인
  - 앱 시작 시 Chroma DB, LLM, Embeddings 한 번만 초기화 (lifespan 이벤트)
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from database import run_text_to_sql, get_llm
from llm_service import (
    get_llm,
    get_vectorstore,
    build_windows,
    build_rag_chain,
    stream_analysis,
)

# ──────────────────────────────────────────────
# 로거 설정
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 앱 전역 상태 (lifespan 이벤트에서 초기화)
# ──────────────────────────────────────────────
app_state: dict = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 앱 라이프사이클 관리.
    서버 시작 시 무거운 리소스(LLM, VectorDB)를 한 번만 로드합니다.
    """
    logger.info("=== SSD 결함 분석 서버 시작 ===")

    # LLM 인스턴스 (Text-to-SQL 및 RAG 분석 공용)
    llm = get_llm()
    app_state["llm"] = llm
    logger.info(f"LLM 로드 완료: {llm.model}")

    # Chroma 벡터 스토어 로드
    vectorstore = get_vectorstore()
    app_state["vectorstore"] = vectorstore
    logger.info("Chroma 벡터 스토어 로드 완료")

    # LCEL RAG 체인 빌드
    rag_chain = build_rag_chain(vectorstore, llm)
    app_state["rag_chain"] = rag_chain
    logger.info("RAG 파이프라인 빌드 완료")

    logger.info("=== 초기화 완료. 요청 대기 중 ===")
    yield  # 서버 실행

    # 서버 종료 시 정리
    logger.info("=== 서버 종료 ===")
    app_state.clear()


# ──────────────────────────────────────────────
# FastAPI 앱 생성
# ──────────────────────────────────────────────
app = FastAPI(
    title="SSD 지능형 결함 분석 시스템",
    description="로컬 LLM + RAG 기반 SSD 결함 판정 API (100% 오프라인)",
    version="1.0.0",
    lifespan=lifespan,
)

# ──────────────────────────────────────────────
# CORS 미들웨어
# 로컬 React 개발 서버(Vite 기본 포트 5173)와
# Chrome --app 모드(null origin)를 모두 허용합니다.
# ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite 개발 서버
        "http://127.0.0.1:5173",
        "http://localhost:3000",   # 빌드 후 프리뷰 포트 (선택)
        "null",                    # Chrome --app 모드는 origin이 'null'
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
        description="사용자의 자연어 질문 (예: '최근 온도 상승 원인이 뭐야?')",
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
    서버와 핵심 컴포넌트(LLM, VectorDB)의 준비 상태를 반환합니다.
    프론트엔드 초기 로딩 시 연결 테스트에 사용하세요.
    """
    return {
        "status": "ok",
        "llm_model": app_state.get("llm", {}).model if app_state.get("llm") else "미초기화",
        "vectorstore_ready": "vectorstore" in app_state,
    }


@app.post("/api/analyze", summary="SSD 결함 분석 (스트리밍)")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """
    자연어 질문을 받아 다음 파이프라인을 실행하고 결과를 스트리밍합니다:

    1. Text-to-SQL (Auto-Retry): 자연어 → SQL → DB 실행
    2. Windowing + 직렬화: DB 행 → 자연어 Window Context
    3. RAG 분석 (LCEL): Chroma 검색 + LLM 분석 → 스트리밍 응답

    응답 형식: text/event-stream (청크 단위 텍스트)
    """
    llm       = app_state.get("llm")
    rag_chain = app_state.get("rag_chain")

    if not llm or not rag_chain:
        raise HTTPException(
            status_code=503,
            detail="서버 초기화 중입니다. 잠시 후 다시 시도하세요.",
        )

    # Step 1: Text-to-SQL → DB 조회
    try:
        rows = run_text_to_sql(req.question, llm)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        # 결과 없음을 스트리밍 형태로 즉시 반환
        async def empty_stream() -> AsyncGenerator[str, None]:
            yield "쿼리 결과가 없습니다. 질문을 바꾸거나 데이터를 확인하세요."
        return StreamingResponse(empty_stream(), media_type="text/plain; charset=utf-8")

    # Step 2: Windowing
    windows = build_windows(rows, window_size=req.window_size)

    # Step 3: 스트리밍 RAG 분석 응답
    return StreamingResponse(
        content=stream_analysis(windows, req.question, rag_chain),
        media_type="text/plain; charset=utf-8",
        headers={
            # 청크 단위 전송을 위해 버퍼링 비활성화
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
        },
    )
