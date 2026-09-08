"""
api/routers/evaluations.py — 답변 자동 평가(answer_evaluations) 조회
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from api.deps import Pagination
from api.repositories import qna_repository as repo
from api.services import rag_service
from api.schemas.common import Page
from api.schemas.evaluations import (
    AnswerEvaluationOut, EvaluationRunRequest, EvaluationRunResponse,
)
from api.settings import Settings, get_settings
from core.prompts import EVAL_ISSUE_CODES, EVAL_VERDICTS

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _uuid_strings(qna_uuid: Optional[List[UUID]], settings: Settings) -> Optional[List[str]]:
    """
    id 묶음 조회의 상한은 페이지 크기 상한과 같다. 더 받아봐야 limit에 잘려 나가고,
    URL에 uuid 수백 개를 붙이면 프록시가 먼저 끊는다.

    SQL에는 문자열로 넘긴다 — insert_log와 같다.
    """
    if not qna_uuid:
        return None
    if len(qna_uuid) > settings.max_page_size:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"qna_uuid는 한 번에 {settings.max_page_size}개까지 넘길 수 있습니다.",
        )
    return [str(u) for u in qna_uuid]


@router.get(
    "",
    response_model=Page[AnswerEvaluationOut],
    summary="답변 평가 전체 목록 (id 여러 개 한 번에 조회)",
    description=(
        "필터 없이 부르면 최근 채점된 것부터 전부 준다.\n\n"
        "화면에 뜬 메시지 여러 개의 평가가 필요하면 **단건 API를 반복 호출하지 말고** "
        "`?qna_uuid=A&qna_uuid=B&qna_uuid=C`로 한 번에 가져온다. 한 대화 전체면 "
        "`?conversation_id=...`가 더 간단하다.\n\n"
        "채점되지 않은 id는 결과에 그냥 빠진다 — 응답을 qna_uuid로 맵에 담고, 맵에 없는 "
        "메시지는 '평가 없음'으로 그리면 된다. 왜 없는지(미평가/채점 대상 아님)는 "
        "`GET /qna`의 status가 알려준다."
    ),
)
def list_evaluations(
    page: Pagination = Depends(),
    qna_uuid: Optional[List[UUID]] = Query(
        default=None, description="이 턴들의 평가만. 여러 번 반복해 넘긴다"
    ),
    conversation_id: Optional[str] = Query(default=None, description="한 대화의 평가만"),
    verdict: Optional[str] = Query(default=None, description=f"{' / '.join(EVAL_VERDICTS)}"),
    issue: Optional[str] = Query(
        default=None, description=f"이 문제 유형이 달린 평가만. {' / '.join(EVAL_ISSUE_CODES)}"
    ),
    answer_type: Optional[str] = Query(default=None, description="채점 대상 답변의 유형"),
    q: Optional[str] = Query(default=None, description="raw_query·cleaned_query 부분 일치"),
    settings: Settings = Depends(get_settings),
) -> Page[AnswerEvaluationOut]:
    """
    최근 채점된 것부터 준다.

    채점에 실패한 답변은 여기 없다 — 평가가 실패하면 행을 남기지 않기 때문이다
    (행이 없어야 "아직 평가 안 함"으로 다시 잡혀 재실행 대상이 된다).
    """
    ids = _uuid_strings(qna_uuid, settings)
    total, rows = repo.list_evaluations(
        page.limit, page.offset, ids, conversation_id, verdict, issue, answer_type, q
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


@router.post(
    "/run",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="평가 일괄 실행 / 재실행",
    description=(
        "여러 턴을 한 번에 채점한다. **단건 API를 반복 호출하지 말 것** — 한 건마다 "
        "판정자 LLM이 1회 돌아 수 초씩 걸리므로, 20건이면 요청도 20번에 응답도 수 분이다.\n\n"
        "무엇을 채점할지는 둘 중 하나로 고른다.\n"
        "- `qna_uuids` — 그 턴들을 채점한다. 이미 채점된 턴도 다시 매긴다(프롬프트를 고친 경우)\n"
        "- 생략 — **미평가에서 최근 `limit`건**을 자동으로 고른다. 재실행 버튼 하나가 이것이다\n\n"
        "**202로 먼저 답하고 채점은 그 뒤에 돈다.** 몇 분짜리 작업이라 응답 안에서 끝낼 수 없다. "
        "진행 상황은 `GET /qna?status=pending`의 건수가 줄어드는 것으로 본다.\n\n"
        "같은 턴이 이미 채점 중이면 그 예약은 조용히 버려진다 — 버튼을 두 번 눌러도 "
        "판정자 비용이 두 배가 되지 않는다."
    ),
)
def run_evaluations(
    req: EvaluationRunRequest,
    background: BackgroundTasks,
) -> EvaluationRunResponse:
    skipped: List[str] = []
    not_found: List[str] = []

    if req.qna_uuids:
        ids = [str(u) for u in req.qna_uuids]
        _, rows = repo.list_logs(len(ids), 0, qna_uuids=ids)
        found = {str(r["qna_uuid"]): r["status"] for r in rows}

        not_found = [i for i in ids if i not in found]
        skipped = [i for i in ids if found.get(i) == "skipped"]
        queued = [i for i in ids if i in found and found[i] != "skipped"]
    else:
        # 미평가만 고른다. skipped(인사·잡담)는 status 필터가 이미 걸러 낸다
        _, rows = repo.list_logs(req.limit, 0, status="pending")
        queued = [str(r["qna_uuid"]) for r in rows]

    for qna_uuid in queued:
        background.add_task(rag_service.evaluate_and_store, qna_uuid)

    return EvaluationRunResponse(
        queued=queued,
        skipped=skipped,
        not_found=not_found,
        hint=(
            "채점은 응답을 보낸 뒤에 돕니다. GET /api/v1/qna?status=pending 의 건수가 줄어드는 것으로 "
            "진행을 확인하고, 결과는 GET /api/v1/evaluations 에서 봅니다."
        ),
    )


@router.post(
    "/{qna_uuid}",
    response_model=AnswerEvaluationOut,
    summary="평가 실행 / 재실행",
    description=(
        "그 턴을 지금 채점하고 결과를 돌려준다. `GET /qna?status=pending`으로 찾은 "
        "미평가 턴을 다시 돌리는 경로이고, 프롬프트를 고친 뒤 이미 채점된 턴을 "
        "다시 매기는 데도 같은 API를 쓴다(답변 1건에 평가 1건이라 덮어쓴다).\n\n"
        "**판정자 LLM을 1회 호출하므로 수 초 걸린다.** 응답을 기다렸다가 결과를 그대로 "
        "화면에 반영하면 된다.\n\n"
        "404는 로그가 없는 경우(대화가 지워졌거나 로그 저장이 실패한 턴), 409는 "
        "인사·잡담이라 채점 대상이 아닌 경우다 — 둘 다 다시 눌러도 결과가 달라지지 않는다. "
        "판정자가 실패하면 502이고, 그때는 아무것도 저장되지 않아 미평가로 남는다."
    ),
)
def run_evaluation(qna_uuid: UUID) -> AnswerEvaluationOut:
    # EvaluationError는 main.py의 예외 핸들러가 502로 변환한다
    try:
        rag_service.run_evaluation(str(qna_uuid))
    except rag_service.QnaLogNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"채점할 로그가 없습니다: {qna_uuid}",
        )
    except rag_service.NotEvaluableError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="인사·잡담(no_search) 턴이라 채점 대상이 아닙니다.",
        )

    # 저장된 행을 다시 읽어 돌려준다. 방금 만든 AnswerEvaluation을 직접 변환하면
    # 질문·유형(qna_logs 쪽 값)을 또 조합해야 해서 응답 모양이 GET과 갈라진다.
    row = repo.get_evaluation(str(qna_uuid))
    return AnswerEvaluationOut.from_row(row)
