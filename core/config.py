"""
core/config.py — 환경변수 단일 진입점

`.env`는 여기서 딱 한 번 읽는다. 환경변수를 쓰는 모듈은 `os.getenv()`를 직접
부르지 않고 이 모듈에서 값을 가져온다.

이유는 두 가지다.

1. **무엇이 필요한지 한 파일만 보면 안다.** `.env.example`이 이 파일의 사본이다.
   값이 여러 파일에 흩어져 있으면 새로 합류한 사람이 무슨 키를 채워야 하는지
   알아내려고 저장소 전체를 grep해야 한다.
2. **`load_dotenv()` 호출 순서에 기대지 않게 된다.** 예전에는 이 호출이 8곳에
   흩어져 있어서, 어떤 모듈은 "누가 먼저 import되느냐"에 따라 값이 있기도 없기도
   했다. 실제로 core/search.py의 `OpenAIEmbeddings`가 그 순서에 우연히 기대고
   있었다(자기 파일에는 `load_dotenv()`가 없다).

api/settings.py와는 다루는 것이 다르고, 겹치지 않는다.

    core/config.py   환경변수에서 오는 값 — DB 접속, 풀 크기, API 키
    api/settings.py  HTTP 계층 정책 — CORS, 경로 접두사, 페이지·검색 기본값

저쪽이 이 값들을 받아 그대로 넘기기만 하는 필드는 두지 않는다. 통과만 하는
필드가 있으면 "이 설정의 주인이 누구인가"가 흐려진다.
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

# 커넥션 풀 크기. 배포마다 다를 수 있어 환경변수로 연다.
# API 서버뿐 아니라 core.search를 직접 쓰는 스크립트에도 같이 적용된다.
DEFAULT_MIN_CONN = int(os.getenv("DB_POOL_MIN", "1"))
DEFAULT_MAX_CONN = int(os.getenv("DB_POOL_MAX", "10"))


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

# 기본값을 두지 않는다. 틀린 키로 조용히 굴러가는 것보다 없는 게 낫다.
# 없으면 None이고, 클라이언트를 만드는 쪽(core/generation.py, core/search.py 등)이
# import 시점에 바로 실패한다 — 첫 요청에서야 인증 오류로 터지는 것보다 낫다.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
