"""
api/config.py — API 계층 설정

라우터·리포지토리에 주입할 값을 여기 한 곳에서 읽는다.

DB 접속 설정만은 여기서 정의하지 않고 core/db.py에서 가져온다. 실제로 접속을
여는 쪽이 core/db.py이고, scripts/ 도 같은 값을 써야 하는데 api/config.py는
계층상 그쪽에서 import할 수 없기 때문이다. 여기서 하는 일은 그 기본값을
Settings의 기본값으로 얹어 .env·환경변수로 덮어쓸 수 있게 하는 것뿐이다.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.db import DATABASE_URL, DEFAULT_MAX_CONN, DEFAULT_MIN_CONN


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 기본값의 출처는 core/db.py 하나뿐이다 (여기에 리터럴을 다시 적지 않는다)
    database_url: str = DATABASE_URL

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

    # DB 커넥션 풀 — 기본값은 core/db.py와 같다
    db_pool_min: int = DEFAULT_MIN_CONN
    db_pool_max: int = DEFAULT_MAX_CONN


@lru_cache
def get_settings() -> Settings:
    return Settings()
