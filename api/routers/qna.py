"""
api/routers/qna.py — 질의응답 로그(qna_logs) 조회

평가 점수 목록은 /evaluations에 있다. 이쪽은 "무엇이 오갔는가"가 주인이고,
채점 여부(status)가 거기 붙는 값이다 — 미평가는 평가 행이 아예 없어서
/evaluations로는 볼 수 없고 여기서만 보인다.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import Pagination
from api.repositories import qna_repository as repo
from api.schemas.common import Page
from api.schemas.qna import QnaLogOut, QnaStatus

router = APIRouter(prefix="/qna", tags=["qna"])


@router.get(
    "",
    response_model=Page[QnaLogOut],
    summary="질의응답 로그 목록 (미평가 조회 포함)",
    description=(
        "최근 턴부터 준다. 각 행의 `status`로 채점 여부를 구분한다.\n\n"
        "- `pending` — **미평가.** 채점을 놓쳤거나 실패한 턴이라 재실행 대상이다\n"
        "- `evaluated` — 채점됨. 점수 전체는 `GET /evaluations`에 있다\n"
        "- `skipped` — 인사·잡담(`no_search`)이라 애초에 채점 대상이 아니다\n\n"
        "`?status=pending` 하나로 미평가 목록이 된다. skipped를 따로 두는 이유는 "
        "그 턴들이 영원히 채점되지 않아서다 — 미평가에 섞이면 목록이 인사말로 찬다."
    ),
)
def list_qna_logs(
    page: Pagination = Depends(),
    status: Optional[QnaStatus] = Query(default=None, description="pending / evaluated / skipped"),
    answer_type: Optional[str] = Query(default=None, description="step, no_answer 등"),
    conversation_id: Optional[str] = Query(default=None, description="한 대화의 턴만"),
    q: Optional[str] = Query(default=None, description="raw_query·cleaned_query 부분 일치"),
    include_answer: bool = Query(default=False, description="답변 본문 포함 여부. 목록에서는 기본 제외"),
) -> Page[QnaLogOut]:
    total, rows = repo.list_logs(page.limit, page.offset, status, answer_type, conversation_id, q)
    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[QnaLogOut.from_row(r, include_answer) for r in rows],
    )
