"""
api/schemas/health.py — 인덱스 적재 상태
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TableStatus(BaseModel):
    table: str
    exists: bool
    rows: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"] = Field(
        description="ok=조회 가능 / degraded=테이블이 없거나 비어 있음 / down=DB 연결 실패"
    )
    database: bool
    tables: List[TableStatus] = Field(default_factory=list)
    hint: Optional[str] = None
