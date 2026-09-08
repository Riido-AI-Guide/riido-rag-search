"""
api/routers/index_status.py — 인덱스 건강 상태

/health는 테이블이 있는지·비었는지만 본다. 여기는 그 다음 질문에 답한다:
**적재는 됐는데 검색이 최신인가.**
"""

from fastapi import APIRouter

from api.repositories import units_repository as repo
from api.schemas.index_status import IndexStatus

router = APIRouter(tags=["index"])


@router.get(
    "/index-status",
    response_model=IndexStatus,
    summary="인덱스가 본문을 따라잡았는지",
    description=(
        "가이드를 다시 빌드한 뒤 **검색 인덱스가 따라왔는지** 확인한다. "
        "`build_answer_units`는 답변 본문만 갱신하고 검색 인덱스는 건드리지 않아서, "
        "그것만 돌리면 답변은 최신인데 검색은 옛 문서 기준으로 남는다.\n\n"
        "- `outdated_content_vector` — 옛 본문으로 색인된 문서. **에러 없이 조용히 나빠지는 쪽이다**\n"
        "- `no_content_vector` — 키워드 검색에서 빠진 문서\n"
        "- `no_search_units` — 벡터 검색에서 빠진 문서. 검색 문장은 자동 생성 경로가 없어 "
        "사람이 `rag_view_sentences.json`에 넣어야 한다\n\n"
        "`status`가 `stale`이면 `hint`가 실행할 명령을 알려준다. 문서 목록은 앞 20건 표본이고 "
        "`count`가 전체 건수다."
    ),
)
def index_status() -> IndexStatus:
    return IndexStatus.from_row(repo.index_status())
