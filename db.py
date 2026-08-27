"""
db.py — 공용 PostgreSQL 커넥션 풀

rag_search와 API 계층이 같은 풀을 공유한다.
- FastAPI: lifespan에서 init_pool() / close_pool()로 명시적으로 관리
- 스크립트(python rag_search.py 등): 첫 사용 시 지연 초기화되므로 별도 준비가 필요 없다
"""

import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2.extras
from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "dbname=riido user=postgres password=postgres host=localhost port=5432",
)

DEFAULT_MIN_CONN = 1
DEFAULT_MAX_CONN = 10

_pool: Optional[ThreadedConnectionPool] = None
_lock = threading.Lock()


def init_pool(
    dsn: Optional[str] = None,
    minconn: int = DEFAULT_MIN_CONN,
    maxconn: int = DEFAULT_MAX_CONN,
) -> None:
    """풀을 미리 만든다. 이미 있으면 아무것도 하지 않는다."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(minconn, maxconn, dsn or DATABASE_URL)


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def _require_pool() -> ThreadedConnectionPool:
    if _pool is None:
        init_pool()  # 스크립트에서 직접 호출한 경우
    return _pool


@contextmanager
def get_connection() -> Iterator["psycopg2.extensions.connection"]:
    """풀에서 커넥션을 빌리고 블록을 벗어나면 반납한다 (닫지 않는다)."""
    pool = _require_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(conn=None) -> Iterator[psycopg2.extras.RealDictCursor]:
    """
    RealDictCursor 컨텍스트.

    conn을 주면 그 커넥션에서 커서만 연다(호출자가 반납을 책임진다).
    주지 않으면 풀에서 하나 빌려 쓰고 바로 반납한다.
    """
    if conn is not None:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        return

    with get_connection() as owned:
        with owned.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
