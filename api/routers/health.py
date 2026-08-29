from fastapi import APIRouter

from api.repositories import units_repository as repo
from api.schemas.health import HealthResponse, TableStatus

router = APIRouter(tags=["health"])

WATCHED_TABLES = ["answer_units", "search_units"]


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

    missing = [s.table for s in statuses if not s.exists]
    empty = [s.table for s in statuses if s.exists and s.rows == 0]

    if missing or empty:
        hint = "python -m scripts.build_answer_units → python -m scripts.build_search_units 순으로 실행해 인덱스를 만드세요."
        if missing:
            hint = f"테이블 없음: {', '.join(missing)}. " + hint
        elif empty:
            hint = f"테이블이 비어 있음: {', '.join(empty)}. " + hint
        return HealthResponse(status="degraded", database=True, tables=statuses, hint=hint)

    return HealthResponse(status="ok", database=True, tables=statuses)
