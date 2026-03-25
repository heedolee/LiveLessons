"""
backend/database.py
===================
역할:
  1. SQLAlchemy 엔진 및 세션 팩토리 초기화 (mysql+pymysql dialect)
  2. device_id 익명화(가명화) 테이블 관리
  3. Text-to-SQL 에이전트: 자연어 → OpenAI → SELECT 쿼리
  4. 보안 가드레일: SELECT 문 전용 검증
  5. Auto-Retry Self-Correction: SQLAlchemy Exception 발생 시
     에러 피드백 → LLM 재생성 (최대 3회)
  6. ORM Row → Pydantic 모델 변환 + 익명화 적용

환경 변수 (pydantic-settings로 .env 자동 로드):
  DB_URL, OPENAI_API_KEY, OPENAI_CHAT_MODEL
"""

from __future__ import annotations

import logging
import re
from collections.abc import Generator
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from models import LogRow, SmartRow, SsdLog, SsdSmartStatus

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. 환경 변수 설정 (pydantic-settings)
# ──────────────────────────────────────────────
class Settings(BaseSettings):
    """
    .env 파일과 환경 변수를 자동으로 읽어 타입 안전하게 관리합니다.
    pydantic-settings v2 스타일 (model_config 사용).
    """
    model_config = SettingsConfigDict(
        env_file=".env",          # 탐색할 .env 파일 경로
        env_file_encoding="utf-8",
        extra="ignore",           # 정의되지 않은 변수 무시
    )

    db_url:            str = "mysql+pymysql://ssd_reader:reader_pass@127.0.0.1:3306/ssd_db?charset=utf8mb4"
    openai_api_key:    str = ""
    openai_chat_model: str = "gpt-4o"

settings = Settings()


# ──────────────────────────────────────────────
# 2. SQLAlchemy 엔진 및 세션 팩토리
# ──────────────────────────────────────────────
engine = create_engine(
    settings.db_url,
    # 커넥션 풀 설정
    pool_size=5,          # 상시 유지 커넥션 수
    max_overflow=10,      # 피크 시 추가 커넥션 수
    pool_pre_ping=True,   # 커넥션 유효성 사전 검사 (MariaDB 자동 재연결)
    pool_recycle=3600,    # 1시간마다 커넥션 재생성 (서버 timeout 방지)
    echo=False,           # True로 변경하면 SQL 로그 출력 (디버그용)
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,     # 명시적 commit/rollback 사용
    autoflush=False,      # flush 자동 실행 비활성화 (읽기 전용 용도)
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI Depends() 용 DB 세션 제너레이터.
    요청 처리 후 세션을 반드시 닫습니다.

    사용 예:
        @app.get("/...")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ──────────────────────────────────────────────
# 3. device_id 익명화(가명화) 관리
# ──────────────────────────────────────────────
_mask_table: dict[str, str] = {}   # 실제 ID → 익명 레이블
_mask_seq:   int            = 0    # 시퀀스 카운터 (A, B, …, Z, AA, …)


def _masked_label(real_id: str) -> str:
    """
    실제 device_id를 재현 가능한 익명 레이블로 변환합니다.
    동일 ID는 항상 동일 레이블로 매핑됩니다.

    예: "SSD-SN-001" → "Device_A",  "SSD-SN-002" → "Device_B"
    """
    global _mask_seq
    if real_id not in _mask_table:
        n = _mask_seq
        label = ""
        while True:
            label = chr(ord("A") + n % 26) + label
            n = n // 26 - 1
            if n < 0:
                break
        _mask_table[real_id] = f"Device_{label}"
        _mask_seq += 1
    return _mask_table[real_id]


def _anonymize(obj: Any, real_id: str) -> Any:
    """
    SQLAlchemy ORM 객체의 device_id 를 익명 레이블로 교체하고
    Pydantic 모델로 변환하여 반환합니다.

    Args:
        obj:     SsdLog 또는 SsdSmartStatus ORM 인스턴스
        real_id: 원본 device_id 문자열

    Returns:
        LogRow 또는 SmartRow Pydantic 인스턴스
    """
    # ORM 객체를 dict로 추출 (SQLAlchemy 2.x __dict__ 필터링)
    data = {
        k: v for k, v in obj.__dict__.items()
        if not k.startswith("_")
    }
    data["device_id"] = _masked_label(real_id)  # 익명화 적용

    if isinstance(obj, SsdLog):
        return LogRow(**data)
    elif isinstance(obj, SsdSmartStatus):
        return SmartRow(**data)
    raise TypeError(f"알 수 없는 ORM 타입: {type(obj)}")


# ──────────────────────────────────────────────
# 4. DB 스키마 (Text-to-SQL 프롬프트 주입용)
# ──────────────────────────────────────────────
DB_SCHEMA = """
테이블: ssd_logs
컬럼:
  id          BIGINT PK
  timestamp   DATETIME   (로그 발생 시각, 인덱스)
  device_id   VARCHAR(64)(SSD 장치 식별자, 인덱스)
  cmd_name    VARCHAR(128)(커맨드 이름: READ, WRITE, TRIM …)
  arg_name    VARCHAR(128)(커맨드 인자 이름)
  arg_value   VARCHAR(256)(커맨드 인자 값)

테이블: ssd_smart_status
컬럼:
  id           BIGINT PK
  timestamp    DATETIME   (S.M.A.R.T. 기록 시각, 인덱스)
  device_id    VARCHAR(64)(SSD 장치 식별자, 인덱스)
  temp         FLOAT      (온도, 섭씨)
  error_count  INT        (누적 에러 횟수)

관계: 두 테이블은 device_id + timestamp 기준으로 JOIN 가능.
"""

# ──────────────────────────────────────────────
# 5. Text-to-SQL 프롬프트 템플릿
# ──────────────────────────────────────────────
TEXT_TO_SQL_PROMPT = PromptTemplate.from_template(
    """당신은 MariaDB SQL 전문가입니다.
아래 스키마를 바탕으로 사용자 질문을 MariaDB SELECT 쿼리로 변환하세요.

[스키마]
{schema}

[규칙]
1. 반드시 SELECT 문만 출력하세요. 다른 DML/DDL은 절대 금지.
2. SQL 쿼리만 출력 — 설명, 마크다운 코드블록(```), 주석 포함 금지.
3. 두 테이블 JOIN 시 device_id 기준, 시간 범위 조건 추가.
4. LIMIT 200 으로 결과 수 제한.
5. MariaDB 문법(DATE_FORMAT, TIMESTAMPDIFF 등) 사용 가능.

[사용자 질문]
{question}

[이전 실행 오류 (없으면 '없음')]
{error}

SQL 쿼리:"""
)

# ──────────────────────────────────────────────
# 6. 보안 가드레일
# ──────────────────────────────────────────────
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE)\b",
    flags=re.IGNORECASE,
)

def validate_select_only(sql: str) -> str:
    """
    LLM 생성 SQL에 대해 두 가지 보안 검사를 수행합니다.

    1. SELECT 로 시작하는지 확인
    2. 위험 키워드(INSERT/UPDATE/DELETE 등)가 없는지 확인

    Args:
        sql: LLM이 생성한 원시 SQL 문자열

    Returns:
        정제된 SQL (마크다운 코드블록 제거 후)

    Raises:
        ValueError: 보안 규칙 위반 시
    """
    # 마크다운 코드블록 제거
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.IGNORECASE).strip()

    if not re.match(r"^\s*SELECT\b", sql, flags=re.IGNORECASE):
        raise ValueError(f"[보안] SELECT 문이 아닌 쿼리: {sql[:120]}")

    match = _FORBIDDEN.search(sql)
    if match:
        raise ValueError(f"[보안] 금지 키워드 '{match.group()}' 감지: {sql[:120]}")

    return sql


# ──────────────────────────────────────────────
# 7. Auto-Retry Text-to-SQL 에이전트
# ──────────────────────────────────────────────
MAX_RETRY = 3


def run_text_to_sql(
    question: str,
    llm: ChatOpenAI,
    db: Session,
) -> list[LogRow | SmartRow]:
    """
    자연어 질문을 SQL로 변환·실행하고 익명화된 Pydantic 객체 목록을 반환합니다.

    파이프라인:
      1. LCEL 체인으로 SQL 생성 (프롬프트 → ChatOpenAI → StrOutputParser)
      2. 보안 가드레일 통과 확인
      3. SQLAlchemy text() + Session.execute() 로 DB 실행
      4. SQLAlchemyError 발생 시 에러 메시지를 프롬프트에 피드백, 최대 MAX_RETRY 재시도
      5. 결과 Row → ORM 모델 판별 → Pydantic 변환 + 익명화

    Args:
        question: 사용자 자연어 질문
        llm:      ChatOpenAI 인스턴스
        db:       SQLAlchemy Session 인스턴스

    Returns:
        LogRow / SmartRow 의 혼합 목록 (JOIN 결과에 따라 다름)

    Raises:
        RuntimeError: MAX_RETRY 초과 시
    """
    chain = TEXT_TO_SQL_PROMPT | llm | StrOutputParser()

    error_msg   = ""
    last_exc    = None

    for attempt in range(1, MAX_RETRY + 1):
        logger.info(f"[Text-to-SQL] 시도 {attempt}/{MAX_RETRY}")

        # (1) SQL 생성
        raw_sql = chain.invoke({
            "schema":   DB_SCHEMA,
            "question": question,
            "error":    error_msg or "없음",
        })
        logger.info(f"[Text-to-SQL] 생성 SQL:\n{raw_sql}")

        # (2) 보안 가드레일
        try:
            clean_sql = validate_select_only(raw_sql)
        except ValueError as ve:
            error_msg = str(ve)
            last_exc  = ve
            logger.warning(f"[보안 위반] {ve}")
            continue

        # (3) SQLAlchemy text() 실행
        try:
            result = db.execute(text(clean_sql))
            rows   = result.fetchall()           # list[Row]
            cols   = list(result.keys())         # 컬럼 이름 목록
            logger.info(f"[Text-to-SQL] 성공: {len(rows)}행, 컬럼: {cols}")

            # (4) Row → Pydantic + 익명화
            return _convert_rows(rows, cols)

        except SQLAlchemyError as sa_err:
            # DB 실행 오류 → 에러를 LLM에 피드백
            error_msg = (
                f"SQLAlchemy 실행 오류: {sa_err}\n"
                f"실행 시도한 SQL: {clean_sql}"
            )
            last_exc  = sa_err
            logger.warning(f"[DB 오류] 시도 {attempt}: {sa_err}")

    raise RuntimeError(
        f"Text-to-SQL: {MAX_RETRY}회 재시도 후에도 실패.\n마지막 오류: {last_exc}"
    )


# ──────────────────────────────────────────────
# 8. Row 변환 헬퍼
# ──────────────────────────────────────────────
_LOG_COLS   = {"cmd_name", "arg_name", "arg_value"}  # ssd_logs 전용 컬럼
_SMART_COLS = {"temp", "error_count"}                 # ssd_smart_status 전용 컬럼


def _convert_rows(
    rows: list,
    cols: list[str],
) -> list[LogRow | SmartRow]:
    """
    SQLAlchemy Row 목록을 컬럼 구성에 따라 LogRow 또는 SmartRow로 변환하고
    device_id 를 익명화합니다.

    컬럼 판별 규칙:
      - cmd_name 포함 → ssd_logs → LogRow
      - temp / error_count 포함 → ssd_smart_status → SmartRow
      - 두 테이블 JOIN → 컬럼별로 분리 후 각각 변환
    """
    col_set = set(cols)
    has_log   = bool(col_set & _LOG_COLS)
    has_smart = bool(col_set & _SMART_COLS)

    results: list[LogRow | SmartRow] = []

    for row in rows:
        row_dict = dict(zip(cols, row))
        real_id  = str(row_dict.get("device_id", "unknown"))
        masked   = _masked_label(real_id)

        if has_log:
            # LogRow 생성 (없는 컬럼은 None)
            results.append(LogRow(
                id        = row_dict.get("id", 0),
                timestamp = row_dict.get("timestamp"),
                device_id = masked,
                cmd_name  = row_dict.get("cmd_name", ""),
                arg_name  = row_dict.get("arg_name"),
                arg_value = row_dict.get("arg_value"),
            ))

        if has_smart:
            # SmartRow 생성 (없는 컬럼은 None)
            results.append(SmartRow(
                id          = row_dict.get("id", 0),
                timestamp   = row_dict.get("timestamp"),
                device_id   = masked,
                temp        = row_dict.get("temp"),
                error_count = row_dict.get("error_count"),
            ))

        # JOIN 결과도 아닌 순수 단일 테이블인데 두 플래그가 모두 False면
        # dict 그대로 저장 (예외 방지)
        if not has_log and not has_smart:
            # fallback: LogRow 스키마로 최선 추정
            results.append(LogRow(
                id        = row_dict.get("id", 0),
                timestamp = row_dict.get("timestamp"),
                device_id = masked,
                cmd_name  = row_dict.get("cmd_name", "unknown"),
                arg_name  = row_dict.get("arg_name"),
                arg_value = row_dict.get("arg_value"),
            ))

    return results
