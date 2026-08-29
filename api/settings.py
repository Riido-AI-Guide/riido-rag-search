"""
api/settings.py — HTTP 계층 정책

라우터·리포지토리에 주입할 값을 여기 한 곳에서 읽는다.

**DB나 API 키 같은 인프라 설정은 여기 없다.** 그건 core/config.py가 갖고,
core/·scripts/ 도 같은 값을 쓴다(계층상 그쪽에서 api/를 import할 수 없다).
받아서 그대로 넘기기만 하는 필드는 두지 않는다 — 통과만 하는 필드가 있으면
"이 설정의 주인이 누구인가"가 흐려진다.

여기 남는 것은 HTTP 경계에서만 의미가 있는 값들이다.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_prefix: str = "/api/v1"
    cors_origins: List[str] = ["*"]

    # 멀티턴 — 질문 재작성에 실제로 쓸 이전 턴 수.
    # 요청이 더 많이 보내와도 여기까지만 쓴다(최근 것부터). 늘려 쓰지는 않는다.
    # 오래된 턴까지 넣으면 지나간 주제가 재작성을 오염시키고 비용만 는다.
    history_turns: int = 5

    # 검색 기본값 (core.search.search의 기본 인자와 동일)
    default_top_k: int = 5
    default_vector_weight: float = 0.5
    max_top_k: int = 20

    # 목록 API 페이지 크기 상한 (한 번에 전체를 내려주지 않기 위한 안전장치)
    default_page_size: int = 50
    max_page_size: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
