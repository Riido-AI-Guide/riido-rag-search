"""
api/routers/evaluations.py — 답변 자동 평가(answer_evaluations) 조회
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import Pagination
from api.repositories import qna_repository as repo
from api.schemas.common import Page
from api.schemas.evaluations import AnswerEvaluationOut
from core.prompts import EVAL_ISSUE_CODES, EVAL_VERDICTS

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get("", response_model=Page[AnswerEvaluationOut], summary="답변 평가 전체 목록")
def list_evaluations(
    page: Pagination = Depends(),
    verdict: Optional[str] = Query(default=None, description=f"{' / '.join(EVAL_VERDICTS)}"),
    issue: Optional[str] = Query(
        default=None, description=f"이 문제 유형이 달린 평가만. {' / '.join(EVAL_ISSUE_CODES)}"
    ),
    answer_type: Optional[str] = Query(default=None, description="채점 대상 답변의 유형"),
    q: Optional[str] = Query(default=None, description="raw_query·cleaned_query 부분 일치"),
) -> Page[AnswerEvaluationOut]:
    """
    최근 채점된 것부터 준다.

    채점에 실패한 답변은 여기 없다 — 평가가 실패하면 행을 남기지 않기 때문이다
    (행이 없어야 "아직 평가 안 함"으로 다시 잡혀 재실행 대상이 된다).
    """
    total, rows = repo.list_evaluations(
        page.limit, page.offset, verdict, issue, answer_type, q
    )
    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[AnswerEvaluationOut.from_row(r) for r in rows],
    )


# uuid로 받아 형식이 깨진 값은 라우터에 들어오기 전에 422로 막는다
# (문자열로 두면 DB까지 내려가 500이 된다). SQL에는 문자열로 넘긴다 — insert_log와 같다.
@router.get("/{qna_uuid}", response_model=AnswerEvaluationOut, summary="평가 단건")
def get_evaluation(qna_uuid: UUID) -> AnswerEvaluationOut:
    row = repo.get_evaluation(str(qna_uuid))
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"평가를 찾을 수 없습니다(아직 채점되지 않았거나 채점에 실패했습니다): {qna_uuid}",
        )
    return AnswerEvaluationOut.from_row(row)
