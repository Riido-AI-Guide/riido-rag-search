"""
api/services/rag_service.py — 질의 → 검색 → 답변 → (선택) 평가 오케스트레이션

test_rag.py가 스크립트로 하던 흐름을 그대로 함수로 옮긴 것이다.
기존 모듈은 수정하지 않고 호출만 한다.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from dto import AnswerEvaluation, RetrievedChunk, SearchHit
from evaluator import EvaluationError, evaluate_faithfulness
from llm import generate_rag_answer
from query_transform import transform_user_query
from rag_search import search as rag_search

logger = logging.getLogger(__name__)

NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."


@dataclass
class AskResult:
    raw_query: str
    cleaned_query: str
    search_queries: List[str]
    needs_search: bool
    answer: str
    documents: List[RetrievedChunk] = field(default_factory=list)
    hits: List[SearchHit] = field(default_factory=list)
    evaluation: Optional[AnswerEvaluation] = None

    @property
    def doc_ids(self) -> List[str]:
        return [d.doc_id for d in self.documents]


def ask(
    query: str,
    top_k: int,
    vector_weight: float,
    evaluate: bool = False,
) -> AskResult:
    """사용자 질문 하나를 끝까지 처리한다. LLM 호출 2~3회 + 임베딩 1회."""
    transformed = transform_user_query(query)

    # 인사·잡담이면 검색도 생성도 하지 않는다 (test_rag.py와 동일한 판단)
    if not transformed.needs_search:
        return AskResult(
            raw_query=query,
            cleaned_query=transformed.cleaned_query,
            search_queries=transformed.search_queries,
            needs_search=False,
            answer=NO_SEARCH_MESSAGE,
        )

    hits, documents = rag_search(
        transformed.cleaned_query, top_k=top_k, vector_weight=vector_weight
    )

    # LlmError는 잡지 않는다. 답변 생성 실패는 요청 실패이므로 그대로 올려보낸다.
    answer = generate_rag_answer(transformed.cleaned_query, documents)

    evaluation = None
    if evaluate:
        # 평가는 부가 정보다. 실패해도 이미 만들어진 답변까지 버릴 이유는 없으므로
        # 로그만 남기고 evaluation=None으로 둔다(0.0으로 채우면 환각 판정과 구분되지 않는다).
        try:
            evaluation = evaluate_faithfulness(
                question=transformed.cleaned_query,
                context_documents=[d.content for d in documents],
                generated_answer=answer.message,
            )
        except EvaluationError as e:
            logger.warning("답변 평가 실패 (답변은 정상 반환): %s", e)

    return AskResult(
        raw_query=query,
        cleaned_query=transformed.cleaned_query,
        search_queries=transformed.search_queries,
        needs_search=True,
        answer=answer.message,
        documents=documents,
        hits=hits,
        evaluation=evaluation,
    )


def search_only(query: str, top_k: int, vector_weight: float, transform: bool = False):
    """답변 생성 없이 검색만. (사용한 검색어, hits, documents)"""
    search_query = transform_user_query(query).cleaned_query if transform else query
    hits, documents = rag_search(search_query, top_k=top_k, vector_weight=vector_weight)
    return search_query, hits, documents
