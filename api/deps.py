"""
api/deps.py — 공통 의존성 (페이지네이션, 검색 파라미터 기본값 해석)
"""

from typing import Optional

from fastapi import Depends, Query as QueryParam

from api.settings import Settings, get_settings


class Pagination:
    def __init__(
        self,
        limit: Optional[int] = QueryParam(default=None, ge=1, description="기본값은 서버 설정"),
        offset: int = QueryParam(default=0, ge=0),
        settings: Settings = Depends(get_settings),
    ):
        self.limit = min(limit or settings.default_page_size, settings.max_page_size)
        self.offset = offset


def resolve_search_params(
    top_k: Optional[int],
    vector_weight: Optional[float],
    settings: Settings,
):
    """요청에 값이 없으면 서버 기본값으로 채우기"""
    return (
        min(top_k or settings.default_top_k, settings.max_top_k),
        settings.default_vector_weight if vector_weight is None else vector_weight,
    )
