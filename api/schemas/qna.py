"""
api/schemas/qna.py — 질의응답 로그(qna_logs) 조회 응답
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# 평가 상태. 계산 규칙은 qna_repository.LOG_STATUS_SQL 하나뿐이고 여기서는 어휘만 정한다.
QnaStatus = Literal["pending", "evaluated", "skipped"]


class QnaLogOut(BaseModel):
    """
    /ask 한 턴의 기록 + 그 턴이 채점되었는지.

    답변 본문(answer_text)은 include_answer=true일 때만 준다 — 수천 자짜리 필드가
    페이지마다 붙으면 목록 응답이 커진다.
    """
    qna_uuid: str = Field(
        description="이 턴의 식별자. /ask 응답의 qna_uuid와 같은 값",
        examples=["3f2b9c14-8a51-4e77-9d2c-6b0f5a1e7c84"],
    )
    conversation_id: Optional[str] = Field(
        default=None, description="백엔드의 대화 id. /ask에 함께 오지 않았으면 null"
    )

    raw_query: str = Field(description="사용자가 실제로 친 질문")
    cleaned_query: str = Field(description="검색에 쓴 재작성 질문")
    answer_type: str = Field(description="답변 유형", examples=["step"])
    retrieved_doc_ids: List[str] = Field(
        default_factory=list, description="검색으로 가져온 문서 전체. 인용된 것만이 아니다"
    )

    status: QnaStatus = Field(
        description=(
            "evaluated=채점됨 / pending=미평가(재실행 대상) / "
            "skipped=인사·잡담이라 채점 대상이 아님"
        ),
        examples=["pending"],
    )
    verdict: Optional[str] = Field(
        default=None, description="채점됐다면 pass/fail. 아니면 null. 점수 전체는 /evaluations에 있다"
    )

    created_at: datetime = Field(description="답변을 보낸 시각")
    answer_text: Optional[str] = Field(
        default=None, description="답변 평문. include_answer=false면 생략"
    )

    @classmethod
    def from_row(cls, row: Dict[str, Any], include_answer: bool = False) -> "QnaLogOut":
        return cls(
            qna_uuid=str(row["qna_uuid"]),        # psycopg2가 UUID 객체로 준다
            conversation_id=row["conversation_id"],
            raw_query=row["raw_query"],
            cleaned_query=row["cleaned_query"],
            answer_type=row["answer_type"],
            retrieved_doc_ids=list(row["retrieved_doc_ids"] or []),
            status=row["status"],
            verdict=row["verdict"],
            created_at=row["created_at"],
            answer_text=row["answer_text"] if include_answer else None,
        )
