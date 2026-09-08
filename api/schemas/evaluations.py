"""
api/schemas/evaluations.py — answer_evaluations 조회 응답
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AnswerEvaluationOut(BaseModel):
    """
    LLM-as-a-Judge가 매긴 점수 1건 + 무엇을 채점한 것인지 되짚을 질문·유형.

    답변 본문(answer_text)은 목록에 싣지 않는다 — 수천 자짜리 필드가 페이지마다
    붙으면 목록 응답이 커진다. 필요하면 qna_uuid로 로그를 따로 읽는다.
    """
    qna_uuid: str = Field(
        description="채점 대상 턴의 식별자. /ask 응답의 qna_uuid와 같은 값",
        examples=["3f2b9c14-8a51-4e77-9d2c-6b0f5a1e7c84"],
    )
    conversation_id: Optional[str] = Field(
        default=None, description="백엔드의 대화 id. /ask에 함께 오지 않았으면 null"
    )

    raw_query: str = Field(description="사용자가 실제로 친 질문")
    cleaned_query: str = Field(description="검색에 쓴 재작성 질문")
    answer_type: str = Field(
        description="채점 대상 답변의 유형(concept/step/... , no_answer/parse_error)",
        examples=["step"],
    )

    faithfulness: float = Field(description="충실도 0.0~1.0. 1.0일수록 환각이 없다")
    answer_relevance: float = Field(description="답변 관련성 0.0~1.0")
    context_relevance: float = Field(description="검색된 문서의 관련성 0.0~1.0")
    verdict: str = Field(
        description="종합 판정. pass / fail — 사용자 good/bad와 그대로 대조하는 축",
        examples=["pass"],
    )
    issues: List[str] = Field(
        default_factory=list,
        description="factual_error / insufficient / irrelevant / retrieval_miss 중 해당하는 것",
    )
    reason: str = Field(default="", description="감점 사유")

    created_at: datetime = Field(description="처음 채점된 시각")
    updated_at: datetime = Field(description="재평가로 덮어쓴 시각. 한 번만 채점했으면 created_at과 같다")

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "AnswerEvaluationOut":
        return cls(
            qna_uuid=str(row["qna_uuid"]),        # psycopg2가 UUID 객체로 준다
            conversation_id=row["conversation_id"],
            raw_query=row["raw_query"],
            cleaned_query=row["cleaned_query"],
            answer_type=row["answer_type"],
            faithfulness=row["faithfulness"],
            answer_relevance=row["answer_relevance"],
            context_relevance=row["context_relevance"],
            verdict=row["verdict"],
            issues=list(row["issues"] or []),
            reason=row["reason"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# 한 번에 예약할 수 있는 채점 수. 한 건마다 판정자 LLM이 1회 도므로 상한이 곧 비용 상한이다.
MAX_RUN_BATCH = 50


class EvaluationRunRequest(BaseModel):
    """
    무엇을 채점할지. 둘 중 하나로 고른다.

    - `qna_uuids`를 주면 그 턴들을 채점한다(이미 채점된 것도 다시 매긴다)
    - 주지 않으면 **미평가에서 최근 `limit`건**을 가져다 채점한다
    """
    qna_uuids: Optional[List[UUID]] = Field(
        default=None, max_length=MAX_RUN_BATCH,
        description=f"채점할 턴. 최대 {MAX_RUN_BATCH}개. 생략하면 미평가에서 자동으로 고른다",
    )
    limit: int = Field(
        default=20, ge=1, le=MAX_RUN_BATCH,
        description="qna_uuids를 주지 않았을 때 미평가에서 가져올 건수",
    )


class EvaluationRunResponse(BaseModel):
    """예약 결과. 채점 자체는 응답을 보낸 뒤에 돈다"""
    queued: List[str] = Field(description="채점을 예약한 턴")
    skipped: List[str] = Field(
        default_factory=list, description="인사·잡담(no_search)이라 채점 대상이 아닌 턴"
    )
    not_found: List[str] = Field(default_factory=list, description="로그가 없는 턴")
    hint: str = Field(description="진행 상황을 어떻게 보는지")
