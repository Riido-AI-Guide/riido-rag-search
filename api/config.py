"""
api/config.py — 환경 변수 기반 설정

기존 모듈들은 각자 os.getenv("DATABASE_URL")을 읽는다. API 계층은 같은 값을
여기 한 곳에서 읽어 라우터·리포지토리에 주입한다(기존 코드는 그대로 둔다).
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # rag_search.py / answer_builder.py와 같은 기본값
    database_url: str = "dbname=riido user=postgres password=postgres host=localhost port=5432"

    api_prefix: str = "/api/v1"
    cors_origins: List[str] = ["*"]

    # 검색 기본값 (rag_search.search의 기본 인자와 동일)
    default_top_k: int = 5
    default_vector_weight: float = 0.5
    max_top_k: int = 20

    # 목록 API 페이지 크기 상한 (한 번에 전체를 내려주지 않기 위한 안전장치)
    default_page_size: int = 50
    max_page_size: int = 500

    # DB 커넥션 풀
    db_pool_min: int = 1
    db_pool_max: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
