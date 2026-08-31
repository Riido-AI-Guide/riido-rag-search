"""
api/deps.py — 공통 의존성 (목록 API 페이지네이션)
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
