"""
api/routers/search_units.py — 검색용 문장(search_units) 조회
"""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.deps import Pagination
from api.repositories import units_repository as repo
from api.schemas.common import Page
from api.schemas.coverage import (
    CoverageStatus, DocCoverage, DocCoverageDetail, DocSentenceSet, DraftRequest,
    DraftResponse, SentenceInput, SentenceSetRequest,
)
from api.schemas.units import SearchUnitOut, ViewTypeStat
from api.services import search_units_service as service

router = APIRouter(prefix="/search-units", tags=["search-units"])


@router.get("", response_model=Page[SearchUnitOut], summary="모든 검색용 문장 목록")
def list_search_units(
    page: Pagination = Depends(),
    view_type: Optional[str] = Query(default=None, description="hypo_q / real_q / contextual"),
    doc_id: Optional[str] = Query(default=None, description="특정 문서에 달린 문장만"),
    q: Optional[str] = Query(default=None, description="text 부분 일치"),
) -> Page[SearchUnitOut]:
    total, rows = repo.list_search_units(page.limit, page.offset, view_type, doc_id, q)
    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[SearchUnitOut.from_row(r) for r in rows],
    )


@router.get("/stats", response_model=List[ViewTypeStat], summary="view_type별 적재 통계")
def stats() -> List[ViewTypeStat]:
    return [ViewTypeStat(**row) for row in repo.view_type_stats()]


# ---------------------------------------------------------------------------
# 검색 문장 관리 (운영 콘솔)
#
# 검색 문장은 자동 생성 경로가 없다 — rag_view_sentences.json은 외부에서 만들어
# 커밋한 파일이라, 새 문서나 갱신된 문서의 문장은 사람이 채워야 한다.
# 아래 네 API가 그 작업 화면을 이룬다: 목록 → 상세 → (초안) → 저장.
# 저장은 전체 교체(PUT)다 — 삭제를 따로 두지 않고 화면의 목록을 그대로 보내면 된다.
# ---------------------------------------------------------------------------

# /coverage는 /{...} 형태의 경로보다 먼저 선언한다(경로 충돌 방지)
@router.get(
    "/coverage",
    response_model=Page[DocCoverage],
    summary="문서별 검색 문장 현황 목록",
    description=(
        "문서마다 검색 문장이 몇 개 붙어 있는지, 그리고 그 문장이 지금 본문 기준인지 준다. "
        "손볼 것이 위로 오도록 **missing → outdated → ok** 순으로 정렬한다.\n\n"
        "- `missing` — 문장이 하나도 없다. **벡터 검색에서 절대 안 걸린다**\n"
        "- `outdated` — 본문이 바뀐 뒤 문장을 손보지 않았다. 옛 내용으로 걸린다\n"
        "- `ok` — 지금 본문 기준의 문장이 있다\n\n"
        "`view_types`는 유형별 개수이고 고정 개수가 아니다 — 한 유형에 문장을 여러 개 "
        "달 수 있다."
    ),
)
def list_coverage(
    page: Pagination = Depends(),
    coverage_status: Optional[CoverageStatus] = Query(
        default=None, alias="status", description="missing / outdated / ok"
    ),
    q: Optional[str] = Query(default=None, description="doc_id·section 부분 일치"),
) -> Page[DocCoverage]:
    total, rows = repo.search_coverage(page.limit, page.offset, coverage_status, q)
    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[DocCoverage.from_row(r) for r in rows],
    )


# doc_id에 슬래시가 들어가므로(guide/팀/팀-관리) :path 컨버터가 필요하다
@router.get(
    "/coverage/{doc_id:path}",
    response_model=DocCoverageDetail,
    summary="문서 1건 — 전문 + 검색 문장 전체",
    description=(
        "콘솔 편집 화면이 이 응답 하나로 그려진다. 왼쪽에 `content`(문서 전문), "
        "오른쪽에 `sentences`(등록된 문장)를 놓으면 된다 — 문장은 원문을 보고 쓰는 것이라 "
        "두 값이 한 번에 와야 한다.\n\n"
        "문장마다 `outdated`가 붙는다. 본문이 바뀐 뒤 손보지 않은 문장이라는 뜻이고, "
        "그것만 골라 고쳐 쓰면 된다."
    ),
)
def get_coverage(doc_id: str) -> DocCoverageDetail:
    row = repo.search_coverage_detail(doc_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"문서를 찾을 수 없습니다: {doc_id}")
    return DocCoverageDetail.build(row, repo.list_doc_sentences(doc_id))


@router.put(
    "/coverage/{doc_id:path}",
    response_model=DocSentenceSet,
    summary="검색 문장 저장 (전체 교체)",
    description=(
        "그 문서의 문장을 보낸 목록과 **같은 상태로 만든다.** 추가·수정·삭제가 이 하나로 "
        "끝난다 — 콘솔은 `GET`으로 읽은 목록을 사용자가 고친 그대로 다시 보내면 되고, "
        "삭제 API를 따로 부를 필요가 없다.\n\n"
        "- 보낸 목록에 **없는** 문장은 지워진다 (빈 배열이면 이 문서의 문장이 전부 사라진다)\n"
        "- **같은** 문장은 임베딩을 다시 만들지 않는다. 유형만 고친 경우가 여기 걸린다\n"
        "- **새** 문장만 임베딩한다. 저장 즉시 검색된다 — 재빌드를 기다릴 필요가 없다\n\n"
        "삭제와 저장은 한 트랜잭션이라 중간에 실패해도 반쯤 지워진 상태로 남지 않는다.\n\n"
        "저장한 문장은 `source=console`이 되어 **다음 빌드가 지우지 않는다**(JSON 기준으로 "
        "정리되는 것은 `source=file` 문장뿐이다). 다만 JSON에 있는 문장을 지운 경우에는 "
        "다음 빌드가 그 문장을 다시 넣는다 — 파일이 아직 갖고 있기 때문이다. 완전히 "
        "없애려면 `rag_view_sentences.json`에서도 빼야 한다."
    ),
)
def save_sentences(doc_id: str, req: SentenceSetRequest) -> DocSentenceSet:
    try:
        sentences = service.replace(doc_id, [item.model_dump() for item in req.items])
    except service.DocNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"문서를 찾을 수 없습니다: {doc_id}"
        )
    return DocSentenceSet.build(repo.search_coverage_detail(doc_id), sentences)


@router.post(
    "/draft",
    response_model=DraftResponse,
    summary="LLM으로 검색 문장 초안 생성",
    description=(
        "문서 원문을 LLM에 주고 가설질문·실제질문·맥락요약 초안을 만든다. "
        "**저장하지 않는다** — 입력창에 채워 넣을 값을 돌려줄 뿐이고, 사람이 고른 것만 "
        "`PUT /search-units/coverage/{doc_id}`로 저장한다.\n\n"
        "빈 칸에서 시작하면 아무도 채우지 않는다는 것이 이 API의 이유다. "
        "이미 등록된 문장은 프롬프트에 함께 넣어 겹치는 초안이 나오지 않게 한다"
        "(`exclude_existing`).\n\n"
        "LLM을 1회 호출하므로 수 초 걸리고, 판정자·답변 생성과 마찬가지로 실패하면 502다."
    ),
)
def draft_sentences(req: DraftRequest) -> DraftResponse:
    # LlmError는 main.py의 예외 핸들러가 502로 변환한다
    try:
        items = service.draft(req.doc_id, req.hypo_count, req.exclude_existing)
    except service.DocNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"문서를 찾을 수 없습니다: {req.doc_id}"
        )
    return DraftResponse(doc_id=req.doc_id, items=[SentenceInput(**i) for i in items])


@router.get(
    "/export",
    summary="검색 문장 전체를 rag_view_sentences.json 형식으로 내보내기",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}, "description": "파일 내용 그대로"}},
    description=(
        "DB의 검색 문장 전체를 [data/rag_view_sentences.json](../data/rag_view_sentences.json)과 "
        "**같은 모양·같은 순서**로 내보낸다. 받은 내용으로 그 파일을 덮어쓰고 커밋하면 된다.\n\n"
        "이 고리가 있어야 콘솔 작업이 저장소에 남는다 — 그러지 않으면 콘솔에서 넣은 문장은 "
        "이 DB에만 있고, 파일에서 온 문장을 지운 것도 다음 빌드가 되살린다.\n\n"
        "**바뀐 것이 없으면 diff도 없다.** 정렬을 파일과 맞춰 두었으므로, 커밋 전 diff에 뜨는 "
        "것이 곧 콘솔에서 손댄 내용이다."
    ),
)
def export_sentences() -> Response:
    # 파일과 같은 직렬화 규칙(2칸 들여쓰기, 한글 그대로). 다르면 diff가 파일 전체로 번진다
    body = json.dumps(repo.export_view_sentences(), ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="rag_view_sentences.json"'},
    )
