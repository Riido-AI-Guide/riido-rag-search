"""
api/schemas/chat.py — 질의응답(/ask)과 검색 전용(/search) 스키마
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from dto import AnswerEvaluation, SearchHit
from api.schemas.units import AnswerUnitOut


class SearchOptions(BaseModel):
    """검색 파라미터 (ask / search 공용)"""
    top_k: Optional[int] = Field(default=None, ge=1, le=20, description="가져올 검색 문장 수. 미지정 시 서버 기본값")
    vector_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="RRF 가중치. 1.0=벡터만, 0.0=키워드만",
    )


class SearchHitOut(BaseModel):
    """검색된 문장 + 점수. 튜닝·디버깅용이라 기본 응답에는 넣지 않는다"""
    id: int
    doc_id: str
    view_type: str
    text: str
    v_similarity: float = Field(description="벡터 유사도 (1 - cosine distance)")
    k_similarity: float = Field(description="키워드 ts_rank")
    rrf_score: float = Field(description="RRF 결합 점수 (정렬 기준)")

    @classmethod
    def from_domain(cls, hit: SearchHit) -> "SearchHitOut":
        return cls(
            id=hit.id,
            doc_id=hit.doc_id,
            view_type=hit.view_type,
            text=hit.text,
            v_similarity=hit.v_similarity,
            k_similarity=hit.k_similarity,
            rrf_score=hit.rrf_score,
        )


class EvaluationOut(BaseModel):
    faithfulness: float = Field(description="0.0~1.0. 낮을수록 환각 의심")
    answer_relevance: float
    context_relevance: float
    reason: str

    @classmethod
    def from_domain(cls, ev: AnswerEvaluation) -> "EvaluationOut":
        return cls(
            faithfulness=ev.faithfulness,
            answer_relevance=ev.answer_relevance,
            context_relevance=ev.context_relevance,
            reason=ev.reason,
        )


class AskRequest(SearchOptions):
    query: str = Field(min_length=1, max_length=1000, examples=["팀원을 어떻게 추가해?"])

    include_documents: bool = Field(default=True, description="근거 문서 본문 포함 여부 (false면 doc_id만)")
    include_hits: bool = Field(default=False, description="검색 문장과 점수 포함 여부")
    evaluate: bool = Field(
        default=False,
        description="LLM-as-a-Judge 평가 수행 여부. true면 LLM 호출이 1회 더 늘어난다",
    )


class AskResponse(BaseModel):
    raw_query: str = Field(description="사용자가 보낸 원문")
    cleaned_query: str = Field(description="query_transform이 정제한 검색어")
    search_queries: List[str] = Field(description="생성된 변형 검색어 (현재 검색에는 미사용)")
    needs_search: bool = Field(description="false면 검색·생성을 건너뛴다")

    answer: str
    doc_ids: List[str] = Field(description="답변의 근거가 된 answer_units 식별자")

    documents: Optional[List[AnswerUnitOut]] = None
    hits: Optional[List[SearchHitOut]] = None
    evaluation: Optional[EvaluationOut] = None


class SearchRequest(SearchOptions):
    """답변 생성 없이 검색만 — LLM 비용 0으로 vector_weight를 튜닝할 때 쓴다"""
    query: str = Field(min_length=1, max_length=1000, examples=["스프린트 기간"])
    transform: bool = Field(default=False, description="true면 query_transform으로 정제 후 검색")


class SearchResponse(BaseModel):
    query: str = Field(description="실제로 검색에 사용한 문자열")
    hits: List[SearchHitOut]
    documents: List[AnswerUnitOut]
