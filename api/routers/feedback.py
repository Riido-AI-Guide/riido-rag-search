"""
api/routers/feedback.py — 사용자 피드백(좋아요/싫어요) × 자동 평가 대조

백엔드가 app 스키마에 쌓는 피드백과 이쪽의 판정자 점수를 qna_uuid로 맞춰 본다.
읽기 전용이고, 답변 경로와는 무관한 운영 콘솔용이다.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import Pagination
from api.repositories import feedback_repository as repo
from api.schemas.common import Page
from api.schemas.feedback import AgreementStat, FeedbackDetail, FeedbackOut

router = APIRouter(prefix="/feedback", tags=["feedback"])

SCHEMA_MISSING_DETAIL = (
    "백엔드 스키마(app.message_feedbacks)가 이 데이터베이스에 없습니다. "
    "백엔드와 같은 DB를 보고 있는지 확인하세요."
)


@router.get(
    "",
    response_model=Page[FeedbackOut],
    summary="사용자 피드백 목록 (자동 평가와 대조)",
    description=(
        "사용자가 남긴 좋아요/싫어요를 최근 것부터 주고, **같은 턴에 대한 판정자의 점수를 "
        "나란히** 붙인다. 둘을 잇는 키는 `qna_uuid`다.\n\n"
        "- `agreement=mismatch` + `rating=BAD` — **사용자는 나쁘다는데 판정자는 pass.** "
        "프롬프트나 판정 기준을 고칠 1순위 표본이다\n"
        "- `agreement=unevaluated` — 아직 채점되지 않은 턴. "
        "`POST /evaluations/run`으로 돌리면 대조에 들어온다\n\n"
        "피드백은 있는데 우리 로그가 없는 행도 빼지 않고 준다(백엔드가 `qna_uuid`를 남기기 "
        "전의 메시지). 그런 행은 `raw_query`가 null이다 — 조용히 빠지면 피드백 수가 맞지 않는다."
    ),
)
def list_feedback(
    page: Pagination = Depends(),
    rating: Optional[str] = Query(default=None, description="GOOD / BAD"),
    reason: Optional[str] = Query(default=None, description="사용자가 고른 항목 (BROKEN_LINK 등)"),
    agreement: Optional[str] = Query(default=None, description="match / mismatch / unevaluated"),
    q: Optional[str] = Query(default=None, description="질문 부분 일치"),
) -> Page[FeedbackOut]:
    try:
        total, rows = repo.list_feedback(page.limit, page.offset, rating, reason, agreement, q)
    except repo.BackendSchemaMissing:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=SCHEMA_MISSING_DETAIL)

    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[FeedbackOut.from_row(r) for r in rows],
    )


@router.get(
    "/stats",
    response_model=List[AgreementStat],
    summary="rating × verdict 교차표",
    description="사용자 평가와 판정자 평가가 얼마나 맞는지. 콘솔 상단 요약용이다.",
)
def stats() -> List[AgreementStat]:
    try:
        return [AgreementStat(**row) for row in repo.agreement_stats()]
    except repo.BackendSchemaMissing:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=SCHEMA_MISSING_DETAIL)


@router.get(
    "/{qna_uuid}",
    response_model=FeedbackDetail,
    summary="피드백 단건 (답변 본문 포함)",
    description=(
        "그 턴에 대한 사용자 평가와 판정자 평가를 함께 준다. 목록과 달리 **답변 본문과 "
        "검색된 문서 id**까지 실어, 사용자가 왜 그렇게 눌렀는지 되짚을 수 있게 한다.\n\n"
        "피드백이 없는 턴이면 404다 — 채점 결과만 보려면 `GET /evaluations/{qna_uuid}`를 쓴다."
    ),
)
def get_feedback(qna_uuid: str) -> FeedbackDetail:
    try:
        row = repo.get_feedback(qna_uuid)
    except repo.BackendSchemaMissing:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=SCHEMA_MISSING_DETAIL)

    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"이 턴에 대한 사용자 피드백이 없습니다: {qna_uuid}"
        )
    return FeedbackDetail.from_row(row)
