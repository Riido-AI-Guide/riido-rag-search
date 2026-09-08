"""
api/schemas/health.py — 인덱스 적재 상태
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TableStatus(BaseModel):
    table: str
    exists: bool
    rows: int


class BackendSchemaStatus(BaseModel):
    """
    백엔드 소유 스키마(app)가 이 DB에 있는지. **status 판정에는 넣지 않는다** —
    없어도 답변·검색·채점은 정상이고 GET /feedback 하나만 못 쓴다.
    남의 스키마 때문에 이 서비스가 unhealthy로 뜨면 헬스체크의 뜻이 흐려진다.
    """
    table: str = Field(description="확인한 테이블", examples=["app.message_feedbacks"])
    available: bool = Field(description="false면 GET /feedback만 503이 된다")
    rows: int = Field(default=0, description="쌓인 사용자 피드백 수")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"] = Field(
        description="ok=조회 가능 / degraded=테이블이 없거나 비어 있음 / down=DB 연결 실패"
    )
    database: bool
    tables: List[TableStatus] = Field(default_factory=list)
    backend: Optional[BackendSchemaStatus] = Field(
        default=None, description="백엔드 스키마 연결 상태(정보). status에 영향을 주지 않는다"
    )
    hint: Optional[str] = None
