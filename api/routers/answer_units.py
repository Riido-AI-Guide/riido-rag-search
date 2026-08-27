"""
api/routers/answer_units.py — 근거 문서(answer_units) 조회
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import Pagination
from api.repositories import units_repository as repo
from api.schemas.common import Page
from api.schemas.units import AnswerUnitDetail, AnswerUnitOut, SearchUnitOut

router = APIRouter(prefix="/answer-units", tags=["answer-units"])


@router.get("", response_model=Page[AnswerUnitOut], summary="모든 근거 문서 목록")
def list_answer_units(
    page: Pagination = Depends(),
    source_type: Optional[str] = Query(default=None, description="guide 등"),
    q: Optional[str] = Query(default=None, description="section·content 부분 일치"),
    include_content: bool = Query(default=False, description="본문 포함 여부. 목록에서는 기본 제외"),
) -> Page[AnswerUnitOut]:
    total, rows = repo.list_answer_units(page.limit, page.offset, source_type, q)
    return Page(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[AnswerUnitOut.from_row(r, include_content) for r in rows],
    )


# doc_id에 슬래시가 들어가므로(guide/팀/팀-관리) :path 컨버터가 필요하다
@router.get("/{doc_id:path}", response_model=AnswerUnitDetail, summary="근거 문서 단건 + 연결된 검색 문장")
def get_answer_unit(doc_id: str) -> AnswerUnitDetail:
    row = repo.get_answer_unit(doc_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"문서를 찾을 수 없습니다: {doc_id}")

    base = AnswerUnitOut.from_row(row, include_content=True)
    return AnswerUnitDetail(
        **base.model_dump(),
        search_units=[SearchUnitOut.from_row(r) for r in repo.list_search_units_by_doc(doc_id)],
    )
