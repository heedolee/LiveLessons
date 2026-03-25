"""
backend/database.py  (OpenAI 버전)
====================================
역할:
  1. MariaDB 연결 (SELECT 전용 'ssd_reader' 계정)
  2. device_id 익명화(마스킹) 테이블 관리
  3. Text-to-SQL: 자연어 → ChatOpenAI → SQL 생성
  4. 보안 가드레일: SELECT 문 여부 검증
  5. Auto-Retry (Self-Correction): DB 오류 시 LLM이 쿼리를 수정하고 재시도 (최대 3회)
  6. 조회 결과에 마스킹 적용 후 반환

환경 변수: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME (.env 파일 참조)
"""

import os
import re
import logging
from typing import Any

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

# .env 파일 로드 (backend/ 기준 상위 디렉터리에 위치)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ──────────────────────────────────────────────
# 1. DB 접속 설정
# ──────────────────────────────────────────────
DB_CONFIG: dict[str, Any] = {
    "host":        os.getenv("DB_HOST",     "127.0.0.1"),
    "port":        int(os.getenv("DB_PORT", "3306")),
    "user":        os.getenv("DB_USER",     "ssd_reader"),
    "password":    os.getenv("DB_PASSWORD", "reader_pass"),
    "database":    os.getenv("DB_NAME",     "ssd_db"),
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,  # 결과를 dict로 반환
    "autocommit":  True,
    "connect_timeout": 10,
}


def get_connection() -> pymysql.connections.Connection:
    """MariaDB 연결 객체를 반환합니다."""
    return pymysql.connect(**DB_CONFIG)


# ──────────────────────────────────────────────
# 2. device_id 익명화(마스킹) 관리
# ──────────────────────────────────────────────
# 실행 중 생성된 마스킹 테이블 (device_id → 익명 레이블)
# 예: {"SSD-001-XYZ": "Device_A", "SSD-002-ABC": "Device_B"}
_device_mask_table: dict[str, str] = {}
_mask_counter = 0  # 익명 레이블 시퀀스 카운터 (A, B, C, ...)


def _get_masked_device_id(real_id: str) -> str:
    """
    실제 device_id를 익명 레이블로 변환합니다.
    동일한 실제 ID는 항상 동일한 익명 레이블로 매핑됩니다.
    OpenAI API로 전송되는 데이터에서 민감 식별자를 제거하는 역할을 합니다.

    예:
        "SSD-S/N-1234" → "Device_A"
        "SSD-S/N-5678" → "Device_B"
    """
    global _mask_counter
    if real_id not in _device_mask_table:
        # 알파벳 시퀀스 생성: A, B, ..., Z, AA, AB, ...
        idx = _mask_counter
        label = ""
        while True:
            label = chr(ord("A") + idx % 26) + label
            idx = idx // 26 - 1
            if idx < 0:
                break
        _device_mask_table[real_id] = f"Device_{label}"
        _mask_counter += 1
        logger.debug(f"[마스킹] '{real_id}' → '{_device_mask_table[real_id]}'")
    return _device_mask_table[real_id]


def mask_rows(rows: list[dict]) -> list[dict]:
    """
    DB 조회 결과 행 목록에서 device_id를 익명 레이블로 치환합니다.
    원본 행은 변경하지 않고 새 dict를 생성합니다.

    Args:
        rows: DB 쿼리 결과 행 리스트

    Returns:
        device_id가 마스킹된 새 행 리스트
    """
    masked = []
    for row in rows:
        new_row = dict(row)  # 원본 복사
        if "device_id" in new_row and new_row["device_id"] is not None:
            new_row["device_id"] = _get_masked_device_id(str(new_row["device_id"]))
        masked.append(new_row)
    return masked


# ──────────────────────────────────────────────
# 3. DB 스키마 정의 (Text-to-SQL 프롬프트 주입용)
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
  - timestamp    DATETIME      (S.M.A.R.T. 기록 시각)
  - device_id    VARCHAR(64)   (SSD 장치 식별자)
  - temp         FLOAT         (온도, 섭씨)
  - error_count  INT           (누적 에러 횟수)

관계:
  - 두 테이블은 device_id 와 timestamp 를 기준으로 JOIN 가능합니다.
  - 시간 범위가 겹치는 레코드를 LEFT JOIN 하거나, 서브쿼리를 활용하세요.
"""

# ──────────────────────────────────────────────
# 4. Text-to-SQL 프롬프트 템플릿
# ──────────────────────────────────────────────
TEXT_TO_SQL_PROMPT = PromptTemplate.from_template(
    """당신은 MariaDB SQL 전문가입니다.
아래 스키마를 바탕으로 사용자의 자연어 질문을 MariaDB SELECT 쿼리로 변환하세요.

[스키마]
{schema}

[규칙]
1. 반드시 SELECT 문만 출력하세요. INSERT/UPDATE/DELETE/DROP 등 절대 금지.
2. SQL 쿼리만 출력하세요. 설명, 마크다운 코드블록(```), 주석 제외.
3. 두 테이블을 JOIN 할 때는 device_id 를 기준으로 하고 시간 범위 조건을 추가하세요.
4. 결과는 LIMIT 200 으로 제한하세요.
5. MariaDB 문법을 사용하세요.

[사용자 질문]
{question}

[이전 실행 오류 (없으면 '없음')]
{error}

SQL 쿼리:"""
)

# ──────────────────────────────────────────────
# 5. 보안 가드레일: SELECT 문 검증
# ──────────────────────────────────────────────
def validate_select_query(sql: str) -> str:
    """
    LLM 생성 SQL이 SELECT 로 시작하는지 검증합니다.
    마크다운 코드블록(```sql ... ```)을 자동 제거한 뒤 검증합니다.

    Returns:
        정제된 SQL 문자열
    Raises:
        ValueError: SELECT 문이 아닐 경우 보안 위반으로 예외 발생
    """
    # 마크다운 코드블록 제거
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.IGNORECASE).strip()
    sql = sql.strip()

    if not re.match(r"^\s*SELECT\b", sql, flags=re.IGNORECASE):
        raise ValueError(
            f"보안 위반: SELECT 문이 아닌 쿼리가 생성되었습니다.\n내용: {sql[:200]}"
        )
    return sql


# ──────────────────────────────────────────────
# 6. Auto-Retry Text-to-SQL 실행기
# ──────────────────────────────────────────────
MAX_RETRY = 3  # 최대 재시도 횟수


def run_text_to_sql(question: str, llm: ChatOpenAI) -> list[dict]:
    """
    자연어 질문을 SQL로 변환·실행하고 마스킹된 결과를 반환합니다.

    파이프라인:
      1. LLM으로 SQL 생성 (LCEL 체인)
      2. 보안 가드레일 통과 확인
      3. MariaDB 실행
      4. 오류 발생 시 에러 메시지를 LLM에 피드백하여 재시도 (최대 MAX_RETRY 회)
      5. device_id 마스킹 적용

    Args:
        question: 사용자 자연어 질문
        llm:      초기화된 ChatOpenAI 인스턴스

    Returns:
        마스킹된 쿼리 결과 행 목록

    Raises:
        RuntimeError: MAX_RETRY 초과 시
    """
    # LCEL 체인: 프롬프트 → ChatOpenAI → 문자열 파서
    chain = TEXT_TO_SQL_PROMPT | llm | StrOutputParser()

    error_message = ""
    last_exception = None

    for attempt in range(1, MAX_RETRY + 1):
        logger.info(f"[Text-to-SQL] 시도 {attempt}/{MAX_RETRY} | 질문: {question[:60]}")

        # (1) LLM SQL 생성
        raw_sql = chain.invoke({
            "schema":   DB_SCHEMA,
            "question": question,
            "error":    error_message or "없음",
        })
        logger.info(f"[Text-to-SQL] 생성된 SQL:\n{raw_sql}")

        # (2) 보안 가드레일
        try:
            clean_sql = validate_select_query(raw_sql)
        except ValueError as ve:
            error_message = str(ve)
            last_exception = ve
            logger.warning(f"[Text-to-SQL] 가드레일 실패: {ve}")
            continue

        # (3) DB 실행
        try:
            conn = get_connection()
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute(clean_sql)
                    rows = cursor.fetchall()

            logger.info(f"[Text-to-SQL] 성공: {len(rows)}행 반환")

            # (4) device_id 마스킹 적용 후 반환
            return mask_rows(rows)

        except pymysql.MySQLError as db_err:
            # DB 오류를 에러 메시지로 저장 → 다음 시도에 LLM 피드백
            error_message = (
                f"DB 실행 오류 (MariaDB Error): {db_err}\n"
                f"실행 시도한 SQL: {clean_sql}"
            )
            last_exception = db_err
            logger.warning(f"[Text-to-SQL] DB 오류 (시도 {attempt}): {db_err}")

    raise RuntimeError(
        f"Text-to-SQL: {MAX_RETRY}회 재시도 후에도 실패했습니다.\n"
        f"마지막 오류: {last_exception}"
    )
