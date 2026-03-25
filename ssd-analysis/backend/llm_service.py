"""
backend/llm_service.py
======================
역할:
  1. ChatOpenAI (gpt-4o) 및 OpenAIEmbeddings 초기화 (싱글톤)
  2. Pydantic 모델 → 한국어 자연어 직렬화 (description.json 레이블 사용)
  3. 시계열 Windowing: 15행 단위 Window Context 생성
  4. Chroma 로컬 벡터 스토어 초기화 및 Retriever 구성
  5. LCEL RAG 파이프라인: 정상 패턴 + 마스킹 로그 + 질문 → ChatOpenAI 스트리밍
  6. 비동기 스트리밍 제너레이터 (FastAPI StreamingResponse 용)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import AsyncGenerator, Union

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.vectorstores import Chroma as ChromaCommunity
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from models import COLUMN_LABELS, LogRow, SmartRow

# .env 로드 (backend/ 위의 프로젝트 루트)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. 환경 변수
# ──────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL     = os.getenv("OPENAI_CHAT_MODEL",  "gpt-4o")
EMBED_MODEL    = os.getenv("OPENAI_EMBED_MODEL",  "text-embedding-3-small")
CHROMA_PERSIST = os.getenv("CHROMA_PERSIST",      "./chroma_db")
CHROMA_COL     = "ssd_normal_patterns"

WINDOW_SIZE    = 15   # 시계열 윈도우 크기 (15~20행)

# ──────────────────────────────────────────────
# 2. LLM / 임베딩 싱글톤 팩토리
# ──────────────────────────────────────────────
def get_llm() -> ChatOpenAI:
    """
    ChatOpenAI 인스턴스를 반환합니다.
    - model: gpt-4o
    - temperature: 0.1 (결함 분석의 일관성 우선)
    - streaming: True → astream() 토큰 단위 스트리밍 활성화
    """
    if not OPENAI_API_KEY:
        raise EnvironmentError(
            "OPENAI_API_KEY 가 설정되지 않았습니다.\n"
            ".env 파일 또는 환경 변수를 확인하세요."
        )
    return ChatOpenAI(
        api_key     = OPENAI_API_KEY,
        model       = CHAT_MODEL,
        temperature = 0.1,
        streaming   = True,
        max_tokens  = 4096,
    )


def get_embeddings() -> OpenAIEmbeddings:
    """OpenAIEmbeddings (text-embedding-3-small) 인스턴스를 반환합니다."""
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY 가 설정되지 않았습니다.")
    return OpenAIEmbeddings(api_key=OPENAI_API_KEY, model=EMBED_MODEL)


# ──────────────────────────────────────────────
# 3. Chroma 벡터 스토어 초기화
# ──────────────────────────────────────────────
def get_vectorstore() -> Chroma:
    """
    로컬 디스크에 영속화된 Chroma DB를 로드하거나 새로 생성합니다.

    정상 패턴 사전 등록 예시:
        vs = get_vectorstore()
        vs.add_texts(["정상 패턴 설명1", "정상 패턴 설명2"])
    """
    vs = Chroma(
        collection_name   = CHROMA_COL,
        embedding_function= get_embeddings(),
        persist_directory = CHROMA_PERSIST,
    )
    logger.info(f"[Chroma] '{CHROMA_COL}' 컬렉션 로드 완료 (persist={CHROMA_PERSIST})")
    return vs


# ──────────────────────────────────────────────
# 4. Pydantic Row → 한국어 자연어 직렬화
# ──────────────────────────────────────────────
def serialize_row(row: Union[LogRow, SmartRow]) -> str:
    """
    LogRow 또는 SmartRow Pydantic 객체를 COLUMN_LABELS 를 참조한
    한국어 자연어 문장으로 직렬화합니다.

    예시:
        LogRow(timestamp='2024-01-01 12:00', device_id='Device_A', cmd_name='READ', ...)
        → "발생시각=2024-01-01 12:00:00, 장치ID=Device_A, 커맨드=READ, ..."

    None 값인 필드는 출력에서 제외합니다.
    """
    parts = []
    # Pydantic v2: model_fields_set 대신 model_dump() 사용
    for field, value in row.model_dump().items():
        if value is None:
            continue
        label = COLUMN_LABELS.get(field, field)   # 레이블 없으면 원본 필드명
        parts.append(f"{label}={value}")
    return ", ".join(parts)


# ──────────────────────────────────────────────
# 5. 시계열 Windowing
# ──────────────────────────────────────────────
def build_windows(
    rows: list[Union[LogRow, SmartRow]],
    window_size: int = WINDOW_SIZE,
) -> list[str]:
    """
    Pydantic Row 목록을 timestamp 기준으로 정렬한 뒤
    window_size 단위로 묶어 Window Context 문자열 목록을 반환합니다.

    Args:
        rows:        직렬화·익명화된 Pydantic 행 리스트
        window_size: 윈도우 크기 (기본 15행, 권장 15~20)

    Returns:
        각 원소가 window_size 개 행의 직렬화 문자열을 개행으로 연결한 목록
    """
    if not rows:
        return []

    # timestamp 기준 정렬 (시계열 컨텍스트 보장)
    sorted_rows = sorted(rows, key=lambda r: r.timestamp or "")

    windows: list[str] = []
    for start in range(0, len(sorted_rows), window_size):
        chunk = sorted_rows[start: start + window_size]
        window_text = "\n".join(serialize_row(r) for r in chunk)
        windows.append(window_text)

    logger.info(
        f"[Windowing] {len(rows)}행 → {len(windows)}개 윈도우 "
        f"(크기={window_size})"
    )
    return windows


# ──────────────────────────────────────────────
# 6. RAG 분석 프롬프트
# ──────────────────────────────────────────────
RAG_PROMPT = PromptTemplate.from_template(
    """당신은 SSD 펌웨어 및 하드웨어 결함 분석 전문가입니다.
아래의 세 가지 정보를 종합하여 결함 여부를 한국어로 판정하고 설명하세요.

[정상 패턴 (Chroma DB 유사도 검색 결과 — 익명화 완료)]
{normal_patterns}

[분석 대상 SSD 로그 (Window Context — device_id 가명화 완료)]
{log_context}

[사용자 질문]
{question}

[분석 지침]
1. 정상 패턴과 분석 대상 로그의 차이점을 항목별로 나열하세요.
2. 온도(temp), 에러횟수(error_count), 커맨드 패턴(cmd_name) 세 관점에서 각각 평가하세요.
3. 결함 판정: [정상 / 주의 / 경고 / 위험] 중 하나를 선택하고 근거를 설명하세요.
4. 권장 조치사항을 구체적으로 서술하세요.

분석 결과:"""
)


# ──────────────────────────────────────────────
# 7. LCEL RAG 파이프라인 빌드
# ──────────────────────────────────────────────
def build_rag_chain(vectorstore: Chroma, llm: ChatOpenAI):
    """
    LCEL 파이프라인을 구성합니다.

    구성:
      입력: {"log_context": str, "question": str}
        → RunnableLambda: log_context로 Chroma 유사 정상 패턴 k=3 검색
        → RAG_PROMPT:     normal_patterns + log_context + question 조합
        → ChatOpenAI:     gpt-4o 스트리밍 추론
        → StrOutputParser: 텍스트 청크 추출

    Returns:
        astream() 호출 가능한 LangChain Runnable
    """
    retriever = vectorstore.as_retriever(
        search_type   = "similarity",
        search_kwargs = {"k": 3},
    )

    def fetch_normal_patterns(inputs: dict) -> dict:
        """
        log_context 앞 500자를 쿼리로 Chroma에서 정상 패턴 검색 후
        'normal_patterns' 키를 추가한 dict를 반환합니다.
        """
        query = inputs.get("log_context", "")[:500]
        docs  = retriever.invoke(query)
        normal = (
            "\n---\n".join(d.page_content for d in docs)
            if docs
            else "저장된 정상 패턴이 없습니다. 현재 로그만으로 분석합니다."
        )
        return {**inputs, "normal_patterns": normal}

    return (
        RunnableLambda(fetch_normal_patterns)
        | RAG_PROMPT
        | llm
        | StrOutputParser()
    )


# ──────────────────────────────────────────────
# 8. 비동기 스트리밍 제너레이터
# ──────────────────────────────────────────────
async def stream_analysis(
    windows : list[str],
    question: str,
    chain,
) -> AsyncGenerator[str, None]:
    """
    각 Window Context에 대해 RAG 체인을 비동기 스트리밍으로 실행하고
    결과 텍스트 청크를 yield 합니다.

    FastAPI 사용 예:
        StreamingResponse(
            content=stream_analysis(windows, question, chain),
            media_type="text/plain; charset=utf-8",
        )

    Args:
        windows : build_windows()가 반환한 윈도우 문자열 목록
        question: 사용자 자연어 질문
        chain   : build_rag_chain()이 반환한 LCEL Runnable

    Yields:
        gpt-4o 분석 결과 텍스트 청크 (str)
    """
    if not windows:
        yield "분석할 데이터가 없습니다. 쿼리 결과가 비어 있습니다."
        return

    total = len(windows)
    for idx, window_ctx in enumerate(windows, start=1):
        yield f"\n\n{'='*60}\n[윈도우 {idx}/{total}] 분석 시작\n{'='*60}\n"

        try:
            async for chunk in chain.astream({
                "log_context": window_ctx,
                "question":    question,
            }):
                if chunk:
                    yield chunk
        except Exception as exc:
            msg = f"\n[오류] 윈도우 {idx} 분석 중 예외: {exc}\n"
            logger.error(msg)
            yield msg

    yield "\n\n[전체 분석 완료]\n"
