"""
backend/llm_service.py  (OpenAI 버전)
=======================================
역할:
  1. ChatOpenAI (gpt-4o) 및 OpenAIEmbeddings (text-embedding-3-small) 초기화
  2. 데이터 직렬화: 마스킹된 DB 행 → description.json 참조 자연어 문장
  3. Windowing: 연속 15~20행을 하나의 Window Context로 묶기
  4. 로컬 RAG: Chroma DB에서 유사 정상 패턴 검색
  5. LCEL RAG 파이프라인 구성 (ChatOpenAI + StrOutputParser)
  6. 스트리밍 제너레이터: FastAPI StreamingResponse 용 async generator

환경 변수: OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBED_MODEL,
           CHROMA_PERSIST (.env 파일 참조)
"""

import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

# .env 로드
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. 설정 상수 (환경 변수 우선)
# ──────────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")          # 필수: .env에 설정
CHAT_MODEL        = os.getenv("OPENAI_CHAT_MODEL",  "gpt-4o")
EMBED_MODEL       = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
CHROMA_PERSIST    = os.getenv("CHROMA_PERSIST",     "./chroma_db")
CHROMA_COLLECTION = "ssd_normal_patterns"                     # 정상 패턴 컬렉션

# description.json: 프로젝트 루트
DESCRIPTION_PATH  = Path(__file__).parent.parent / "description.json"

# 윈도우 크기 기본값 (15행)
WINDOW_SIZE = 15

# ──────────────────────────────────────────────
# 2. description.json 로드
# ──────────────────────────────────────────────
def load_description() -> dict[str, str]:
    """
    description.json 을 읽어 컬럼명 → 한글 레이블 딕셔너리를 반환합니다.
    _comment 키는 자동으로 제외됩니다.
    """
    if not DESCRIPTION_PATH.exists():
        logger.warning(f"description.json 을 찾을 수 없습니다: {DESCRIPTION_PATH}")
        return {}
    with open(DESCRIPTION_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}

DESCRIPTION: dict[str, str] = load_description()

# ──────────────────────────────────────────────
# 3. LLM 및 임베딩 초기화
# ──────────────────────────────────────────────
def get_llm() -> ChatOpenAI:
    """
    ChatOpenAI 인스턴스를 반환합니다.
    - model: gpt-4o (결함 분석의 정확성과 긴 컨텍스트 처리 모두 적합)
    - temperature: 0.1 (분석 일관성 우선)
    - streaming: True (astream() 사용을 위해 활성화)
    """
    if not OPENAI_API_KEY:
        raise EnvironmentError(
            "OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
            ".env 파일을 확인하거나 export OPENAI_API_KEY=sk-... 로 설정하세요."
        )
    return ChatOpenAI(
        api_key=OPENAI_API_KEY,
        model=CHAT_MODEL,
        temperature=0.1,
        streaming=True,   # 토큰 단위 스트리밍 활성화
        max_tokens=4096,  # 분석 결과가 길 수 있으므로 여유있게 설정
    )


def get_embeddings() -> OpenAIEmbeddings:
    """
    OpenAIEmbeddings 인스턴스를 반환합니다.
    text-embedding-3-small: 비용 효율적이며 RAG 검색 품질이 우수합니다.
    """
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
    return OpenAIEmbeddings(
        api_key=OPENAI_API_KEY,
        model=EMBED_MODEL,
    )


# ──────────────────────────────────────────────
# 4. Chroma 벡터 스토어 초기화
# ──────────────────────────────────────────────
def get_vectorstore() -> Chroma:
    """
    로컬 디스크에 영속화된 Chroma DB를 로드하거나,
    없으면 빈 컬렉션을 새로 생성합니다.

    정상 패턴 문서 사전 등록 예시:
        vs = get_vectorstore()
        vs.add_texts(["정상 패턴 문장1", "정상 패턴 문장2"])
    """
    embeddings = get_embeddings()
    vectorstore = Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_PERSIST,
    )
    logger.info(
        f"[Chroma] '{CHROMA_COLLECTION}' 컬렉션 로드 완료 "
        f"(persist={CHROMA_PERSIST})"
    )
    return vectorstore


# ──────────────────────────────────────────────
# 5. 데이터 직렬화: 마스킹된 DB 행 → 자연어 문장
# ──────────────────────────────────────────────
def serialize_row(row: dict) -> str:
    """
    마스킹이 적용된 DB 결과 한 행(dict)을 description.json 의 레이블을
    사용한 한글 자연어 문장으로 변환합니다.

    device_id는 database.py에서 이미 마스킹('Device_A' 등)되어 전달됩니다.

    예시:
        {'timestamp': '2024-01-01 12:00:00', 'device_id': 'Device_A',
         'cmd_name': 'READ', ...}
        → "발생시각=2024-01-01 12:00:00, 장치ID=Device_A, 커맨드=READ, ..."
    """
    parts = []
    for col, value in row.items():
        label = DESCRIPTION.get(col, col)   # 레이블 없으면 원본 컬럼명
        parts.append(f"{label}={value}")
    return ", ".join(parts)


# ──────────────────────────────────────────────
# 6. Windowing: 연속 행을 흐름 단위로 묶기
# ──────────────────────────────────────────────
def build_windows(rows: list[dict], window_size: int = WINDOW_SIZE) -> list[str]:
    """
    마스킹된 DB 결과 행 목록을 window_size 단위로 묶어
    각 윈도우를 하나의 문자열(Window Context)로 반환합니다.

    Args:
        rows:        마스킹된 DB 쿼리 결과 행 리스트
        window_size: 윈도우 크기 (기본 15행, 요구사항: 15~20행)

    Returns:
        윈도우 문자열 목록 (각 원소 = window_size 행의 직렬화 결과 개행 연결)
    """
    windows: list[str] = []
    total = len(rows)

    if total == 0:
        return windows

    for start in range(0, total, window_size):
        chunk = rows[start: start + window_size]
        serialized_lines = [serialize_row(r) for r in chunk]
        window_text = "\n".join(serialized_lines)
        windows.append(window_text)

    logger.info(
        f"[Windowing] 총 {total}행 → {len(windows)}개 윈도우 "
        f"(윈도우 크기={window_size})"
    )
    return windows


# ──────────────────────────────────────────────
# 7. RAG 분석 프롬프트 템플릿
# ──────────────────────────────────────────────
RAG_PROMPT = PromptTemplate.from_template(
    """당신은 SSD 펌웨어 및 하드웨어 결함 분석 전문가입니다.
아래의 [정상 패턴], [분석 대상 로그], [사용자 질문] 을 종합하여
결함 여부를 판정하고 근거를 한국어로 상세히 설명하세요.

[정상 패턴 (과거 유사 정상 데이터, Chroma DB 검색 결과)]
{normal_patterns}

[분석 대상 SSD 로그 (Window Context, 민감정보 마스킹 완료)]
{log_context}

[사용자 질문]
{question}

[분석 지침]
1. 정상 패턴과 분석 대상 로그의 주요 차이점을 나열하세요.
2. 온도(temp), 에러횟수(error_count), 커맨드 패턴(cmd_name) 관점에서 각각 평가하세요.
3. 결함 판정: [정상 / 주의 / 경고 / 위험] 중 하나를 선택하고 이유를 설명하세요.
4. 권장 조치사항을 구체적으로 기술하세요.

분석 결과:"""
)

# ──────────────────────────────────────────────
# 8. LCEL RAG 파이프라인 구성
# ──────────────────────────────────────────────
def build_rag_chain(vectorstore: Chroma, llm: ChatOpenAI):
    """
    LCEL(LangChain Expression Language) 파이프라인을 구성합니다.

    파이프라인:
      입력 dict {"log_context": str, "question": str}
        → RunnableLambda: log_context로 Chroma에서 유사 정상 패턴 3개 검색
        → RAG_PROMPT:     normal_patterns + log_context + question → PromptValue
        → ChatOpenAI:     gpt-4o 스트리밍 추론
        → StrOutputParser: 문자열 청크 추출

    Returns:
        스트리밍 가능한 LangChain Runnable 체인
    """
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 3},   # 상위 3개 정상 패턴 검색
    )

    def retrieve_normal_patterns(inputs: dict) -> dict:
        """
        log_context 앞 500자를 쿼리로 사용하여 Chroma에서 정상 패턴을 검색하고,
        'normal_patterns' 키를 추가한 딕셔너리를 반환합니다.
        """
        query = inputs.get("log_context", "")[:500]
        docs = retriever.invoke(query)

        if docs:
            # 검색된 정상 패턴 문서를 구분선으로 연결
            normal_text = "\n---\n".join(d.page_content for d in docs)
        else:
            normal_text = "저장된 정상 패턴이 없습니다. 현재 로그만으로 분석합니다."

        return {**inputs, "normal_patterns": normal_text}

    # LCEL 체인 조립:
    #   retrieve_normal_patterns (dict → dict)
    #   | RAG_PROMPT              (dict → PromptValue)
    #   | llm (ChatOpenAI)        (PromptValue → AIMessageChunk, 스트리밍)
    #   | StrOutputParser          (AIMessageChunk → str)
    chain = (
        RunnableLambda(retrieve_normal_patterns)
        | RAG_PROMPT
        | llm
        | StrOutputParser()
    )
    return chain


# ──────────────────────────────────────────────
# 9. 스트리밍 제너레이터 (FastAPI StreamingResponse 용)
# ──────────────────────────────────────────────
async def stream_analysis(
    windows: list[str],
    question: str,
    chain,
) -> AsyncGenerator[str, None]:
    """
    각 Window Context에 대해 RAG 체인을 비동기 스트리밍으로 실행하고
    결과 텍스트 청크를 yield 합니다.

    FastAPI 사용 예시:
        return StreamingResponse(
            content=stream_analysis(windows, question, chain),
            media_type="text/plain; charset=utf-8",
        )

    Args:
        windows:  build_windows()가 반환한 윈도우 문자열 목록
        question: 사용자 자연어 질문 (마스킹된 컨텍스트와 함께 LLM에 전달)
        chain:    build_rag_chain()이 반환한 LCEL 체인

    Yields:
        gpt-4o 분석 결과 텍스트 청크 (str)
    """
    if not windows:
        yield "분석할 데이터가 없습니다. 쿼리 결과가 비어 있습니다."
        return

    for idx, window_ctx in enumerate(windows, start=1):
        # 윈도우 구분 헤더
        header = (
            f"\n\n{'='*60}\n"
            f"[윈도우 {idx}/{len(windows)}] 분석 시작\n"
            f"{'='*60}\n"
        )
        yield header

        try:
            # chain.astream(): ChatOpenAI의 스트리밍 토큰을 비동기로 수신
            async for chunk in chain.astream({
                "log_context": window_ctx,
                "question":    question,
            }):
                if chunk:   # 빈 청크 제외
                    yield chunk

        except Exception as e:
            error_msg = f"\n[오류] 윈도우 {idx} 분석 중 예외 발생: {e}\n"
            logger.error(error_msg)
            yield error_msg

    yield "\n\n[분석 완료]\n"
