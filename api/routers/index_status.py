"""
api/routers/index_status.py — 인덱스 건강 상태와 재빌드

/health는 테이블이 있는지·비었는지만 본다. 여기는 그 다음 질문에 답한다:
**적재는 됐는데 검색이 최신인가.** 그리고 낡았을 때 다시 만드는 것까지 여기서 한다.
"""

import logging
import threading

from fastapi import APIRouter, BackgroundTasks, HTTPException, status as http_status

from api.repositories import units_repository as repo
from api.schemas.index_status import IndexStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["index"])


# ---------------------------------------------------------------------------
# 재빌드 진행 표시
# ---------------------------------------------------------------------------
# 프로세스 안에서만 유효한 플래그다. 목적은 두 가지 —
#   1) 콘솔이 "언제 끝났는지" 알 수 있게 (index-status의 rebuilding)
#   2) 같은 인스턴스에서의 연타 막기
# 인스턴스가 여러 개로 늘면 각자 자기 플래그만 보므로 동시에 돌 수 있다. 그때는
# pg_advisory_lock으로 바꿔야 한다(→ api/README.md의 "앞으로" 항목).
_rebuild_lock = threading.Lock()


def is_rebuilding() -> bool:
    return _rebuild_lock.locked()


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
        "`count`가 전체 건수다.\n\n"
        "`rebuilding`이 true면 `POST /index/rebuild`가 아직 도는 중이다."
    ),
)
def index_status() -> IndexStatus:
    return IndexStatus.from_row(repo.index_status(), rebuilding=is_rebuilding())


@router.post(
    "/index/rebuild",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="가이드 재수집 + 검색 인덱스 재생성",
    description=(
        "지금까지 서버에서 손으로 돌리던 두 스크립트를 순서대로 실행한다.\n\n"
        "1. `build_guide_answer_units` — docs.riido.io에서 가이드를 다시 받아 답변 본문을 갱신\n"
        "2. `build_search_units` — 검색 문장 인덱스\n"
        "3. `build_content_vectors` — 원문 인덱스(키워드+벡터). `outdated_content_vector`가 이걸로 풀린다\n\n"
        "**순서가 중요하다.** 1번만 돌리면 답변은 최신인데 검색은 옛 문서 기준으로 남는다.\n\n"
        "수 분 걸릴 수 있어 **202를 먼저 주고 뒤에서 돈다.** 진행 상황은 `GET /index-status`의 "
        "`rebuilding`과 낡은 문서 수가 줄어드는 것으로 본다.\n\n"
        "이미 도는 중이면 409. 두 번 돌리면 같은 임베딩을 두 번 사게 되기 때문이다.\n\n"
        "`no_search_units`는 이 API로 풀리지 않는다 — 검색 문장은 사람이 채워야 한다."
    ),
)
def rebuild_index(background: BackgroundTasks) -> dict:
    # 획득만 하고 여기서 풀지 않는다. 백그라운드 작업이 끝날 때 푼다.
    if not _rebuild_lock.acquire(blocking=False):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            detail="이미 재빌드가 진행 중입니다.",
        )

    background.add_task(_run_rebuild)
    return {"status": "started"}


def _run_rebuild() -> None:
    """
    빌드 스크립트를 그대로 호출한다. 로직은 이미 함수로 나와 있어 옮겨 적을 게 없다.

    예외를 밖으로 올리지 않는다 — 응답은 이미 202로 나갔고, 여기서 터져 봐야
    받아 줄 곳이 없다. 실패는 로그로 남고, 화면에서는 낡은 문서 수가 그대로인
    것으로 드러난다.
    """
    # 임포트를 함수 안에 두는 이유: 이 스크립트들은 로드 시점에 임베딩 클라이언트를
    # 만든다. 모듈 최상단에 두면 재빌드를 쓰지 않는 서버도 그 비용을 부팅 때 문다.
    from scripts.build_answer_units import build_guide_answer_units
    from scripts.build_search_units import build_content_vectors, build_search_units

    try:
        report = build_guide_answer_units()
        logger.info(
            "재빌드 — 본문 갱신: 신규 %d / 수정 %d / 삭제 %d / 재색인 대상 %d",
            len(report.created), len(report.updated), len(report.deleted), len(report.dirty),
        )
        build_search_units()
        build_content_vectors()
        logger.info("재빌드 완료")
    except Exception:
        logger.exception("재빌드 실패")
    finally:
        # 실패해도 반드시 푼다. 안 풀면 다음부터 영영 409가 뜬다.
        _rebuild_lock.release()
