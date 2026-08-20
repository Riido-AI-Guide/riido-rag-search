"""
api/routers/search_units.py — 검색용 문장(search_units) 조회
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import Pagination
from api.repositories import units_repository as repo
from api.schemas.common import Page
from api.schemas.units import SearchUnitOut, ViewTypeStat

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
