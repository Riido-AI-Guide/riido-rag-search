from typing import List

from fastapi import APIRouter

from api.repositories import units_repository as repo
from api.schemas.health import BackendSchemaStatus, HealthResponse, TableStatus

router = APIRouter(tags=["health"])

# 비어 있으면 검색이 되지 않는다 — 존재 여부와 적재량을 함께 본다
INDEX_TABLES = ["answer_units", "search_units"]

# 로그·평가 테이블. 배포 직후에는 비어 있는 것이 정상이라 존재 여부만 본다
LOG_TABLES = ["qna_logs", "answer_evaluations"]

WATCHED_TABLES = INDEX_TABLES + LOG_TABLES

# 백엔드 소유 테이블. 있는지만 알려주고 status 판정에는 넣지 않는다 —
# 없어도 이 서비스는 정상이고 GET /feedback만 못 쓴다.
BACKEND_TABLE = "app.message_feedbacks"

INDEX_HINT = (
    "python -m scripts.build_answer_units → python -m scripts.build_search_units 순으로 "
    "실행해 인덱스를 만드세요."
)
LOG_HINT = "python -m scripts.build_qna_logs 로 로그·평가 테이블을 만드세요."


@router.get("/health", response_model=HealthResponse, summary="DB·인덱스 적재 상태")
def health() -> HealthResponse:
    try:
        statuses = [
            TableStatus(table=t, exists=exists, rows=rows)
            for t in WATCHED_TABLES
            for exists, rows in [repo.table_status(t)]
        ]
    except Exception as e:
        return HealthResponse(status="down", database=False, hint=f"DB 연결 실패: {e}")

    # 판정과 분리해서 따로 읽는다. 실패해도 헬스 응답 자체가 죽지는 않게 한다
    try:
        exists, rows = repo.table_status(BACKEND_TABLE)
        backend = BackendSchemaStatus(table=BACKEND_TABLE, available=exists, rows=rows)
    except Exception:
        backend = BackendSchemaStatus(table=BACKEND_TABLE, available=False)

    missing = [s.table for s in statuses if not s.exists]
    empty = [s.table for s in statuses if s.exists and s.rows == 0 and s.table in INDEX_TABLES]

    if not missing and not empty:
        return HealthResponse(status="ok", database=True, tables=statuses, backend=backend)

    hints: List[str] = []
    if missing:
        hints.append(f"테이블 없음: {', '.join(missing)}.")
    if empty:
        hints.append(f"테이블이 비어 있음: {', '.join(empty)}.")
    if empty or any(t in INDEX_TABLES for t in missing):
        hints.append(INDEX_HINT)
    if any(t in LOG_TABLES for t in missing):
        hints.append(LOG_HINT)

    return HealthResponse(
        status="degraded", database=True, tables=statuses, backend=backend, hint=" ".join(hints)
    )
