"""
api/schemas/feedback.py — 사용자 피드백 × 자동 평가 대조 응답
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# app.message_feedbacks의 CHECK 제약과 같은 값들. 주인은 백엔드라 여기서는 문서화용으로만 쓴다
# (값이 늘어도 이쪽이 막지 않도록 응답 타입은 문자열로 둔다).
RATINGS = ("GOOD", "BAD")
GOOD_REASONS = ("ACCURATE", "EASY_TO_UNDERSTAND", "WANTED_ANSWER",
                "EASY_TO_FOLLOW", "SUFFICIENT", "USEFUL_LINK")
BAD_REASONS = ("OFF_TOPIC", "INACCURATE", "NOT_FOUND",
               "INSUFFICIENT", "TOO_DIFFICULT", "BROKEN_LINK")

Agreement = Literal["match", "mismatch", "unevaluated"]


class FeedbackOut(BaseModel):
    """사용자가 남긴 평가 1건 + 같은 턴에 대한 판정자의 평가"""
    qna_uuid: Optional[str] = Field(
        default=None, description="두 평가를 잇는 키. 백엔드가 저장하지 않았으면 null"
    )
    message_id: int = Field(description="백엔드의 메시지 id")
    conversation_id: Optional[str] = Field(default=None, description="우리 로그에 남은 대화 id")

    # 사용자 쪽
    rating: str = Field(description=" / ".join(RATINGS), examples=["BAD"])
    reason: Optional[str] = Field(
        default=None,
        description=f"사용자가 고른 항목. 좋아요: {', '.join(GOOD_REASONS)} / 싫어요: {', '.join(BAD_REASONS)}",
        examples=["BROKEN_LINK"],
    )
    feedback_created_at: datetime

    # 우리 로그 쪽 — 피드백은 있는데 로그가 없으면(백엔드가 qna_uuid를 남기기 전 메시지) null이다
    raw_query: Optional[str] = Field(default=None, description="사용자가 실제로 친 질문")
    answer_type: Optional[str] = Field(default=None, examples=["step"])
    answered_at: Optional[datetime] = Field(default=None, description="답변을 보낸 시각")

    # 판정자 쪽 — 아직 채점되지 않았으면 전부 null
    verdict: Optional[str] = Field(default=None, description="pass / fail")
    faithfulness: Optional[float] = None
    answer_relevance: Optional[float] = None
    context_relevance: Optional[float] = None
    issues: List[str] = Field(default_factory=list, description="판정자가 붙인 문제 유형")

    agreement: Agreement = Field(
        description=(
            "match=사용자와 판정자가 같은 방향 / mismatch=엇갈림 / unevaluated=아직 채점 안 됨. "
            "**BAD인데 pass**가 프롬프트를 고칠 1순위 표본이다"
        )
    )

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "FeedbackOut":
        return cls(
            qna_uuid=row["qna_uuid"],
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            rating=row["rating"],
            reason=row["feedback_reason"],
            feedback_created_at=row["feedback_created_at"],
            raw_query=row["raw_query"],
            answer_type=row["answer_type"],
            answered_at=row["answered_at"],
            verdict=row["verdict"],
            faithfulness=row["faithfulness"],
            answer_relevance=row["answer_relevance"],
            context_relevance=row["context_relevance"],
            issues=list(row["issues"] or []),
            agreement=row["agreement"],
        )


class FeedbackDetail(FeedbackOut):
    """단건 — 무엇에 대한 평가였는지 되짚을 수 있게 답변 본문까지"""
    cleaned_query: Optional[str] = Field(default=None, description="검색에 쓴 재작성 질문")
    answer_text: Optional[str] = Field(default=None, description="사용자가 본 답변 평문")
    retrieved_doc_ids: List[str] = Field(
        default_factory=list, description="그때 검색된 문서. BROKEN_LINK 같은 항목을 되짚을 때 본다"
    )
    eval_reason: Optional[str] = Field(default=None, description="판정자의 감점 사유")

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "FeedbackDetail":
        return cls(
            **FeedbackOut.from_row(row).model_dump(),
            cleaned_query=row["cleaned_query"],
            answer_text=row.get("answer_text"),
            retrieved_doc_ids=list(row["retrieved_doc_ids"] or []),
            eval_reason=row["eval_reason"],
        )


class AgreementStat(BaseModel):
    """rating × verdict 교차표 한 칸"""
    rating: str
    verdict: Optional[str] = Field(default=None, description="아직 채점 안 됐으면 null")
    agreement: Agreement
    count: int
