"""
api/services/search_units_service.py — 검색 문장 등록·초안 오케스트레이션

운영 콘솔이 쓰는 쓰기 경로다. 라우터는 HTTP만 알고, SQL은 리포지토리가 알고,
임베딩·LLM을 언제 부르는지는 여기가 정한다.
"""

import logging
from typing import Any, Dict, List, Optional

from api.repositories import units_repository as repo
from core.search import embeddings, extract_keywords
from core.view_sentences import draft_view_sentences

logger = logging.getLogger(__name__)


class DocNotFound(Exception):
    """등록·초안 대상 문서가 없다. 재색인으로 사라졌거나 doc_id를 잘못 보낸 경우다."""


def replace(doc_id: str, items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    그 문서의 검색 문장을 items와 같은 상태로 만들고, 그 결과를 돌려준다.
    추가·수정·삭제가 이 하나로 끝난다 — 콘솔은 화면의 목록을 통째로 보내면 된다.

    임베딩은 **바뀐 문장만, 묶음으로 한 번**에 만든다. 문장 텍스트가 그대로면 임베딩도
    그대로라 다시 만들 이유가 없다(유형만 고친 경우가 여기 걸린다).
    저장이 끝나면 곧바로 검색된다 — 재빌드를 기다릴 필요가 없다.

    지금 본문의 해시를 함께 새긴다. 나중에 문서가 갱신되면 이 문장들이
    '옛 내용 기준'으로 잡혀 재검토 목록에 뜬다.
    """
    doc = repo.get_answer_unit_for_draft(doc_id)
    if doc is None:
        raise DocNotFound(doc_id)

    known = {s["text"]: s for s in repo.list_doc_sentences(doc_id)}
    fresh = [item["text"] for item in items if item["text"] not in known]

    # 임베딩(외부 API)을 먼저 끝내고 DB에 쓴다 — 커넥션을 네트워크 대기 동안 잡지 않는다
    vectors = repo.get_sentence_vectors(doc_id)
    if fresh:
        for text, vector in zip(fresh, embeddings.embed_documents(fresh)):
            vectors[text] = str(vector)

    rows = [
        {
            "doc_id": doc_id,
            "view_type": item["view_type"],
            "text": item["text"],
            "text_tsv": extract_keywords(item["text"]),  # 검색 때와 같은 토크나이저여야 한다
            "embedding": vectors[item["text"]],
            "source_hash": doc["source_hash"],
        }
        for item in items
    ]
    repo.replace_doc_sentences(doc_id, rows)
    logger.info(
        "검색 문장 저장: %s — %d건(신규·수정 %d건, 삭제 %d건)",
        doc_id, len(rows), len(fresh), len(known) - (len(rows) - len(fresh)),
    )

    return repo.list_doc_sentences(doc_id)


def draft(doc_id: str, hypo_count: int = 3, exclude_existing: bool = True) -> List[Dict[str, str]]:
    """
    LLM 초안. **저장하지 않는다** — 사람이 고쳐서 register로 다시 보내는 것이 전제다.

    이미 등록된 문장을 프롬프트에 함께 넣어 겹치는 초안이 나오지 않게 한다.
    """
    doc = repo.get_answer_unit_for_draft(doc_id)
    if doc is None:
        raise DocNotFound(doc_id)

    existing: Optional[List[str]] = None
    if exclude_existing:
        existing = [s["text"] for s in repo.list_doc_sentences(doc_id)]

    # LlmError는 main.py의 예외 핸들러가 502로 변환한다
    return draft_view_sentences(
        section=doc["section"],
        content=doc["content"],
        hypo_count=hypo_count,
        existing=existing,
    )
