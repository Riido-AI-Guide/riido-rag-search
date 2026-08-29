"""
core/db.py — PostgreSQL 접속 설정과 커넥션 풀

접속 설정(DSN, 풀 크기)은 core/config.py에서 가져온다. 이 파일이 하는 일은
그 값으로 커넥션을 만들고 관리하는 것뿐이다.

커넥션을 얻는 방법은 두 가지이고, 용도가 다르다.
- get_connection() / get_cursor(): 풀에서 빌려 쓴다. 요청 처리용.
  FastAPI는 lifespan에서 init_pool()/close_pool()로 관리하고,
  스크립트에서 core.search를 직접 부르면 첫 사용 시 지연 초기화된다.
- connect(): 풀을 거치지 않는 독립 커넥션. 인덱스 빌드 같은 배치용.
"""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Optional

import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from core.config import DATABASE_URL, DEFAULT_MAX_CONN, DEFAULT_MIN_CONN


def connect() -> "psycopg2.extensions.connection":
    """
    풀을 거치지 않는 독립 커넥션. 호출자가 close()를 책임진다.

    인덱스 빌드처럼 수 분짜리 트랜잭션을 여는 배치 작업용이다. 그런 작업이
    풀에서 커넥션을 빌리면 그동안 요청 처리 쪽이 굶는다.
    """
    return psycopg2.connect(DATABASE_URL)


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
def get_connection() -> Generator["psycopg2.extensions.connection", None, None]:
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
def get_cursor(conn=None) -> Generator[psycopg2.extras.RealDictCursor, None, None]:
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
