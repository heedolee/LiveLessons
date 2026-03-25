"""
backend/llm_service.py
======================
역할:
  1. 로컬 LLM (Ollama / qwen2.5:7b) 및 임베딩 (nomic-embed-text) 초기화
  2. 데이터 직렬화: DB 행 → description.json 참조 자연어 문장
  3. Windowing: 연속 15~20행을 하나의 '흐름(Window Context)'으로 묶기
  4. 로컬 RAG: Chroma DB에서 유사 정상 패턴 검색
  5. LCEL RAG 파이프라인 구성
  6. 스트리밍 제너레이터: FastAPI StreamingResponse 용 async generator
"""

import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from langchain_community.llms import Ollama
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. 설정 상수
# ──────────────────────────────────────────────
OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL        = os.getenv("LLM_MODEL",       "qwen2.5:7b")
EMBED_MODEL       = os.getenv("EMBED_MODEL",     "nomic-embed-text")
CHROMA_PERSIST    = os.getenv("CHROMA_PERSIST",  "./chroma_db")      # Chroma 영속 디렉터리
CHROMA_COLLECTION = "ssd_normal_patterns"                             # 정상 패턴 컬렉션 이름

# description.json 위치: 프로젝트 루트
DESCRIPTION_PATH = Path(__file__).parent.parent / "description.json"

# 윈도우 크기: 15~20행을 한 윈도우로 묶음
WINDOW_SIZE = 15

# ──────────────────────────────────────────────
# 2. description.json 로드
# ──────────────────────────────────────────────
def load_description() -> dict[str, str]:
    """
    description.json 파일을 읽어 컬럼명 → 한글 레이블 딕셔너리를 반환합니다.
    파일이 없으면 빈 딕셔너리를 반환합니다.
    """
    if not DESCRIPTION_PATH.exists():
        logger.warning(f"description.json 을 찾을 수 없습니다: {DESCRIPTION_PATH}")
        return {}
    with open(DESCRIPTION_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    # _comment 키 제외
    return {k: v for k, v in raw.items() if not k.startswith("_")}

DESCRIPTION: dict[str, str] = load_description()

# ──────────────────────────────────────────────
# 3. LLM 및 임베딩 초기화 (싱글톤 패턴)
# ──────────────────────────────────────────────
def get_llm() -> Ollama:
    """
    Ollama LLM 인스턴스를 반환합니다.
    temperature=0.1: 결함 분석 목적이므로 낮은 창의성(높은 일관성) 유지.
    streaming=True : 토큰 단위 스트리밍 활성화.
    """
    return Ollama(
        base_url=OLLAMA_BASE_URL,
        model=LLM_MODEL,
        temperature=0.1,
        # Ollama 커뮤니티 통합은 streaming 파라미터를 직접 지원
    )


def get_embeddings() -> OllamaEmbeddings:
    """
    nomic-embed-text 임베딩 인스턴스를 반환합니다.
    완전 로컬 처리 — 외부 API 호출 없음.
    """
    return OllamaEmbeddings(
        base_url=OLLAMA_BASE_URL,
        model=EMBED_MODEL,
    )


# ──────────────────────────────────────────────
# 4. Chroma 벡터 스토어 초기화
# ──────────────────────────────────────────────
def get_vectorstore() -> Chroma:
    """
    로컬 디스크에 영속화된 Chroma DB를 로드하거나,
    없으면 빈 컬렉션으로 새로 생성합니다.

    정상 패턴 문서를 미리 임포트(seed)하려면 별도 스크립트에서
    vectorstore.add_texts([...]) 를 호출하세요.
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
# 5. 데이터 직렬화: DB 행 → 자연어 문장
# ──────────────────────────────────────────────
def serialize_row(row: dict) -> str:
    """
    DB 쿼리 결과 한 행(dict)을 description.json 의 레이블을 사용한
    한글 자연어 문장으로 변환합니다.

    예시:
        {'timestamp': '2024-01-01 12:00:00', 'cmd_name': 'READ', ...}
        → "발생시각=2024-01-01 12:00:00, 커맨드=READ, ..."
    """
    parts = []
    for col, value in row.items():
        label = DESCRIPTION.get(col, col)   # 레이블 없으면 원본 컬럼명 사용
        parts.append(f"{label}={value}")
    return ", ".join(parts)


# ──────────────────────────────────────────────
# 6. Windowing: 연속 행을 흐름 단위로 묶기
# ──────────────────────────────────────────────
def build_windows(rows: list[dict], window_size: int = WINDOW_SIZE) -> list[str]:
    """
    DB 결과 행 목록을 WINDOW_SIZE 단위로 슬라이딩하여
    각 윈도우를 하나의 문자열(Window Context)로 묶습니다.

    Args:
        rows:        DB 쿼리 결과 행 리스트
        window_size: 윈도우 크기 (기본 15행)

    Returns:
        윈도우 문자열 목록.
        각 원소는 "{window_size}개 행의 직렬화 결과를 개행으로 연결한 문자열"입니다.
    """
    windows: list[str] = []
    total = len(rows)

    if total == 0:
        return windows

    # step = window_size 로 비겹침 슬라이딩 (겹침 원하면 step 조정)
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

[정상 패턴 (과거 유사 정상 데이터)]
{normal_patterns}

[분석 대상 SSD 로그 (Window Context)]
{log_context}

[사용자 질문]
{question}

[분석 지침]
1. 정상 패턴과 분석 대상 로그의 주요 차이점을 먼저 나열하세요.
2. 온도(temp), 에러횟수(error_count), 커맨드 패턴(cmd_name) 관점에서 각각 평가하세요.
3. 결함 판정: [정상 / 주의 / 경고 / 위험] 중 하나를 선택하고 이유를 설명하세요.
4. 권장 조치사항을 구체적으로 기술하세요.

분석 결과:"""
)

# ──────────────────────────────────────────────
# 8. LCEL RAG 파이프라인 구성
# ──────────────────────────────────────────────
def build_rag_chain(vectorstore: Chroma, llm: Ollama):
    """
    LCEL(LangChain Expression Language) 파이프라인을 구성합니다.

    파이프라인 구성:
      입력 dict {"log_context": str, "question": str}
        → Retriever: log_context로 Chroma에서 유사 정상 패턴 검색
        → 프롬프트: normal_patterns + log_context + question 조합
        → LLM (Ollama)
        → 문자열 파서

    Returns:
        LangChain Runnable 체인
    """
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 3},   # 상위 3개 정상 패턴 문서 검색
    )

    def retrieve_normal_patterns(inputs: dict) -> dict:
        """
        log_context를 쿼리로 사용하여 Chroma에서 정상 패턴 검색 후
        입력 dict 에 "normal_patterns" 키를 추가합니다.
        """
        query = inputs.get("log_context", "")[:500]  # 쿼리 길이 제한
        docs = retriever.invoke(query)
        if docs:
            normal_text = "\n---\n".join(d.page_content for d in docs)
        else:
            normal_text = "저장된 정상 패턴이 없습니다. 현재 로그만으로 분석합니다."
        return {**inputs, "normal_patterns": normal_text}

    # LCEL 체인:
    #   retrieve_normal_patterns (dict → dict, normal_patterns 추가)
    #   | RAG_PROMPT              (dict → PromptValue)
    #   | llm                     (PromptValue → AIMessage or str)
    #   | StrOutputParser         (→ str)
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
    각 Window Context에 대해 RAG 체인을 스트리밍 방식으로 실행하고
    결과 청크를 yield 합니다.

    FastAPI의 StreamingResponse(content=stream_analysis(...)) 에 전달하세요.

    Args:
        windows:  build_windows()가 반환한 윈도우 문자열 목록
        question: 사용자 자연어 질문
        chain:    build_rag_chain()이 반환한 LCEL 체인

    Yields:
        분석 결과 텍스트 청크 (str)
    """
    if not windows:
        yield "분석할 데이터가 없습니다. 쿼리 결과가 비어 있습니다."
        return

    for idx, window_ctx in enumerate(windows, start=1):
        # 구분 헤더 출력
        header = f"\n\n{'='*60}\n[윈도우 {idx}/{len(windows)}] 분석 시작\n{'='*60}\n"
        yield header

        try:
            # astream: Ollama LangChain 통합의 비동기 스트리밍 메서드
            async for chunk in chain.astream({
                "log_context": window_ctx,
                "question":    question,
            }):
                if chunk:           # 빈 청크 제외
                    yield chunk
        except Exception as e:
            error_msg = f"\n[오류] 윈도우 {idx} 분석 중 예외 발생: {e}\n"
            logger.error(error_msg)
            yield error_msg

    yield "\n\n[분석 완료]\n"
