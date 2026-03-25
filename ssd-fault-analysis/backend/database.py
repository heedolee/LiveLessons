"""
backend/database.py
===================
역할:
  1. MariaDB 연결 풀 초기화 (SELECT 전용 'ssd_reader' 계정)
  2. Text-to-SQL: 자연어 → LLM이 SQL 생성
  3. 보안 가드레일: SELECT 문 여부 검증
  4. Auto-Retry (Self-Correction): DB 오류 발생 시 LLM이 쿼리를 스스로 수정하고 재시도 (최대 3회)
"""

import os
import re
import logging
from typing import Any

import pymysql
import pymysql.cursors
from langchain_community.llms import Ollama
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 로거 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ──────────────────────────────────────────────
# 1. DB 접속 설정 (환경 변수 우선, 없으면 기본값)
# ──────────────────────────────────────────────
DB_CONFIG: dict[str, Any] = {
    "host":     os.getenv("DB_HOST",     "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", "3306")),
    "user":     os.getenv("DB_USER",     "ssd_reader"),   # SELECT 전용 계정
    "password": os.getenv("DB_PASSWORD", "reader_pass"),
    "database": os.getenv("DB_NAME",     "ssd_db"),
    "charset":  "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,             # 결과를 dict 형태로 반환
    "autocommit": True,                                    # 읽기 전용이므로 autocommit 허용
    "connect_timeout": 10,
}

# ──────────────────────────────────────────────
# 2. DB 연결 헬퍼 (매 쿼리마다 새 연결 생성 → 간단·안전)
# ──────────────────────────────────────────────
def get_connection() -> pymysql.connections.Connection:
    """
    DB 연결 객체를 반환합니다.
    실제 프로덕션에서는 connection pool(DBUtils 등)을 사용하는 것을 권장합니다.
    """
    return pymysql.connect(**DB_CONFIG)


# ──────────────────────────────────────────────
# 3. DB 스키마 정의 (Text-to-SQL 프롬프트에 주입)
# ──────────────────────────────────────────────
DB_SCHEMA = """
테이블: ssd_logs
컬럼:
  - timestamp    DATETIME      (로그 발생 시각)
  - device_id    VARCHAR(64)   (SSD 장치 식별자)
  - cmd_name     VARCHAR(128)  (SSD 커맨드 이름, 예: READ, WRITE, TRIM)
  - arg_name     VARCHAR(128)  (커맨드 인자 이름)
  - arg_value    VARCHAR(256)  (커맨드 인자 값)

테이블: ssd_smart_status
컬럼:
  - timestamp    DATETIME      (S.M.A.R.T 기록 시각)
  - device_id    VARCHAR(64)   (SSD 장치 식별자)
  - temp         FLOAT         (온도, 섭씨)
  - error_count  INT           (누적 에러 횟수)

관계:
  - 두 테이블은 device_id 와 timestamp 를 기준으로 JOIN 가능합니다.
  - ssd_logs.timestamp ≈ ssd_smart_status.timestamp (동일 구간 레코드)
"""

# ──────────────────────────────────────────────
# 4. Text-to-SQL 프롬프트 템플릿
# ──────────────────────────────────────────────
TEXT_TO_SQL_PROMPT = PromptTemplate.from_template(
    """당신은 MariaDB SQL 전문가입니다.
아래 스키마 정보를 바탕으로 사용자의 자연어 질문을 MariaDB SELECT 쿼리로 변환하세요.

[스키마]
{schema}

[규칙]
1. 반드시 SELECT 문으로만 응답하세요. INSERT, UPDATE, DELETE, DROP 등은 절대 사용 금지.
2. 쿼리만 출력하세요. 설명, 마크다운 코드블록(```), 주석은 포함하지 마세요.
3. 두 테이블을 JOIN 할 때는 device_id 를 기준으로 하고, 시간 범위 조건도 함께 사용하세요.
4. 결과는 최대 200행으로 제한하세요 (LIMIT 200).
5. MariaDB 문법을 사용하세요 (DATE_FORMAT, TIMESTAMPDIFF 등 MariaDB 함수 사용 가능).

[사용자 질문]
{question}

[이전 실행 오류 (있는 경우)]
{error}

SQL 쿼리:"""
)

# ──────────────────────────────────────────────
# 5. 보안 가드레일: SELECT 문 검증
# ──────────────────────────────────────────────
def validate_select_query(sql: str) -> str:
    """
    LLM이 생성한 SQL이 SELECT 로 시작하는지 검증합니다.
    코드블록 마크다운(```sql ... ```)이 포함된 경우 제거 후 검증합니다.

    Returns:
        정제된 SQL 문자열
    Raises:
        ValueError: SELECT 문이 아닐 경우
    """
    # 마크다운 코드 블록 제거
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.IGNORECASE).strip()
    # 앞뒤 공백·개행 정리
    sql = sql.strip()
    # SELECT로 시작하는지 검사 (대소문자 무관)
    if not re.match(r"^\s*SELECT\b", sql, flags=re.IGNORECASE):
        raise ValueError(
            f"보안 위반: LLM이 SELECT 가 아닌 쿼리를 생성했습니다.\n생성된 내용: {sql[:200]}"
        )
    return sql


# ──────────────────────────────────────────────
# 6. Auto-Retry (Self-Correction) Text-to-SQL 실행기
# ──────────────────────────────────────────────
MAX_RETRY = 3  # 최대 재시도 횟수

def run_text_to_sql(question: str, llm: Ollama) -> list[dict]:
    """
    자연어 질문을 받아 SQL을 생성·실행하고 결과를 반환합니다.
    실행 중 오류 발생 시 LLM에 에러를 피드백하여 최대 MAX_RETRY 회 재시도합니다.

    Args:
        question: 사용자 자연어 질문
        llm:      초기화된 Ollama LLM 인스턴스

    Returns:
        쿼리 결과 행 목록 (list of dict)

    Raises:
        RuntimeError: MAX_RETRY 초과 시
    """
    # LCEL 체인: 프롬프트 → LLM → 문자열 파서
    chain = TEXT_TO_SQL_PROMPT | llm | StrOutputParser()

    error_message = ""      # 이전 오류 메시지 (없으면 빈 문자열)
    last_exception = None   # 마지막으로 발생한 예외

    for attempt in range(1, MAX_RETRY + 1):
        logger.info(f"[Text-to-SQL] 시도 {attempt}/{MAX_RETRY} | 질문: {question[:60]}")

        # (1) LLM에게 SQL 생성 요청
        raw_sql = chain.invoke({
            "schema":   DB_SCHEMA,
            "question": question,
            "error":    error_message if error_message else "없음",
        })
        logger.info(f"[Text-to-SQL] LLM 생성 SQL:\n{raw_sql}")

        # (2) 보안 가드레일 통과 여부 확인
        try:
            clean_sql = validate_select_query(raw_sql)
        except ValueError as ve:
            error_message = str(ve)
            last_exception = ve
            logger.warning(f"[Text-to-SQL] 가드레일 실패: {ve}")
            continue  # LLM에 오류 피드백 후 재시도

        # (3) DB 실행
        try:
            conn = get_connection()
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute(clean_sql)
                    rows = cursor.fetchall()
            logger.info(f"[Text-to-SQL] 쿼리 성공: {len(rows)}행 반환")
            return rows  # 성공 시 즉시 반환

        except pymysql.MySQLError as db_err:
            # DB 에러를 에러 메시지로 저장 → 다음 시도에 LLM 피드백
            error_message = f"DB 실행 오류: {db_err}\n실행한 SQL: {clean_sql}"
            last_exception = db_err
            logger.warning(f"[Text-to-SQL] DB 오류 (시도 {attempt}): {db_err}")

    # MAX_RETRY 초과
    raise RuntimeError(
        f"Text-to-SQL: {MAX_RETRY}회 재시도 후에도 실패했습니다.\n"
        f"마지막 오류: {last_exception}"
    )
