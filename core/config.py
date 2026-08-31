"""
core/config.py — 환경변수 단일 진입점

`.env`는 여기서 딱 한 번 읽는다. 환경변수를 쓰는 모듈은 `os.getenv()`를 직접
부르지 않고 이 모듈에서 값을 가져온다.
"""

import os

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "dbname=riido user=postgres password=postgres host=localhost port=5432",
)

# 커넥션 풀 크기
DEFAULT_MIN_CONN = int(os.getenv("DB_POOL_MIN", "1"))
DEFAULT_MAX_CONN = int(os.getenv("DB_POOL_MAX", "10"))


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

# 기본값 x
# 없으면 None이고, 클라이언트를 만드는 쪽이 import 시점에 바로 실패한다.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
