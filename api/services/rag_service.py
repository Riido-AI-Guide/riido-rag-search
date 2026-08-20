"""
api/services/rag_service.py — 질의 → 검색 → 답변 → (선택) 평가 오케스트레이션

test_rag.py가 스크립트로 하던 흐름을 그대로 함수로 옮긴 것이다.
기존 모듈은 수정하지 않고 호출만 한다.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from dto import AnswerEvaluation, RetrievedChunk, SearchHit
from evaluator import evaluate_faithfulness
from llm import generate_rag_answer
from query_transform import transform_user_query
from rag_search import search as rag_search

# llm.py는 예외를 잡아 이 문자열로 시작하는 메시지를 정상 반환한다.
# API에서는 200 OK로 나가면 안 되므로 여기서 걸러 502로 승격한다.
# (llm.py가 예외를 그대로 올리도록 바뀌면 이 분기는 지워도 된다)
LLM_ERROR_PREFIX = "답변 생성 중 오류가 발생했습니다"

NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."


class AnswerGenerationError(RuntimeError):
    """LLM 호출 실패 (llm.py가 문자열로 감싼 오류를 되살린 것)"""


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

    answer = generate_rag_answer(transformed.cleaned_query, documents)
    if answer.message.startswith(LLM_ERROR_PREFIX):
        raise AnswerGenerationError(answer.message)

    evaluation = None
    if evaluate:
        evaluation = evaluate_faithfulness(
            question=transformed.cleaned_query,
            context_documents=[d.content for d in documents],
            generated_answer=answer.message,
        )

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
