"""
backend/models.py
=================
SQLAlchemy ORM 모델 정의.

포함 내용:
  - Base: DeclarativeBase 싱글톤 (모든 모델이 공유)
  - SsdLog: ssd_logs 테이블 매핑
  - SsdSmartStatus: ssd_smart_status 테이블 매핑
  - LogRow / SmartRow: 직렬화·익명화용 Pydantic 출력 모델
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import BigInteger, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ──────────────────────────────────────────────
# description.json 로드 (컬럼 레이블 매핑 사전)
# ──────────────────────────────────────────────
_DESC_PATH = Path(__file__).parent.parent / "description.json"

def load_description() -> dict[str, str]:
    """description.json을 읽어 컬럼명 → 한국어 레이블 딕셔너리를 반환합니다."""
    if not _DESC_PATH.exists():
        return {}
    with open(_DESC_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}

# 전역 레이블 사전 (llm_service.py 등에서 import 가능)
COLUMN_LABELS: dict[str, str] = load_description()


# ──────────────────────────────────────────────
# SQLAlchemy Declarative Base
# ──────────────────────────────────────────────
class Base(DeclarativeBase):
    """
    모든 ORM 모델이 상속받는 공통 Base 클래스.
    SQLAlchemy 2.x 스타일의 DeclarativeBase를 사용합니다.
    """
    pass


# ──────────────────────────────────────────────
# ORM 모델 1: ssd_logs
# ──────────────────────────────────────────────
class SsdLog(Base):
    """
    ssd_logs 테이블 ORM 매핑.

    MariaDB 컬럼:
      id          BIGINT AUTO_INCREMENT PK
      timestamp   DATETIME
      device_id   VARCHAR(64)
      cmd_name    VARCHAR(128)   — SSD 커맨드 (READ, WRITE, TRIM …)
      arg_name    VARCHAR(128)   — 커맨드 인자 이름
      arg_value   VARCHAR(256)   — 커맨드 인자 값
    """
    __tablename__ = "ssd_logs"

    id:        Mapped[int]           = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime]      = mapped_column(DateTime,   nullable=False, index=True)
    device_id: Mapped[str]           = mapped_column(String(64), nullable=False, index=True)
    cmd_name:  Mapped[str]           = mapped_column(String(128), nullable=False)
    arg_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    arg_value: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SsdLog id={self.id} device={self.device_id} "
            f"cmd={self.cmd_name} ts={self.timestamp}>"
        )


# ──────────────────────────────────────────────
# ORM 모델 2: ssd_smart_status
# ──────────────────────────────────────────────
class SsdSmartStatus(Base):
    """
    ssd_smart_status 테이블 ORM 매핑.

    MariaDB 컬럼:
      id           BIGINT AUTO_INCREMENT PK
      timestamp    DATETIME
      device_id    VARCHAR(64)
      temp         FLOAT          — 온도 (섭씨)
      error_count  INT            — 누적 에러 횟수
    """
    __tablename__ = "ssd_smart_status"

    id:          Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp:   Mapped[datetime] = mapped_column(DateTime,  nullable=False, index=True)
    device_id:   Mapped[str]      = mapped_column(String(64), nullable=False, index=True)
    temp:        Mapped[float]    = mapped_column(Float,      nullable=True)
    error_count: Mapped[int]      = mapped_column(Integer,    nullable=True, default=0)

    def __repr__(self) -> str:
        return (
            f"<SsdSmartStatus id={self.id} device={self.device_id} "
            f"temp={self.temp} errors={self.error_count}>"
        )


# ──────────────────────────────────────────────
# Pydantic 출력 모델 (직렬화 + 익명화 계층)
# ──────────────────────────────────────────────
class LogRow(BaseModel):
    """
    SsdLog ORM 객체를 API 응답 / LLM 입력용으로 변환하는 Pydantic 모델.
    device_id 는 익명화 처리 후 주입됩니다 (database.py 참조).
    """
    model_config = ConfigDict(from_attributes=True)  # ORM 객체 직접 변환 허용

    id:        int
    timestamp: datetime
    device_id: str = Field(description="익명화된 장치 ID (예: Device_A)")
    cmd_name:  str
    arg_name:  Optional[str] = None
    arg_value: Optional[str] = None


class SmartRow(BaseModel):
    """
    SsdSmartStatus ORM 객체를 API 응답 / LLM 입력용으로 변환하는 Pydantic 모델.
    """
    model_config = ConfigDict(from_attributes=True)

    id:          int
    timestamp:   datetime
    device_id:   str = Field(description="익명화된 장치 ID")
    temp:        Optional[float] = None
    error_count: Optional[int]  = None
