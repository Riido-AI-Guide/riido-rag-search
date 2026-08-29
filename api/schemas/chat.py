"""
api/schemas/chat.py — 질의응답(/ask)과 검색 전용(/search) 스키마
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from dto import AnswerEvaluation, ConversationTurn, SearchHit
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


# 요청 하나에 실어 보낼 수 있는 이전 턴의 상한.
# 정책값이 아니라 비정상 페이로드를 막는 안전장치다. 실제로 몇 턴을 쓸지는
# Settings.history_turns가 정하고, 서버가 최근 것부터 잘라 쓴다.
MAX_HISTORY_TURNS = 50


class ConversationTurnIn(BaseModel):
    """이미 끝난 대화 한 턴. 대화의 소유자는 백엔드이고, 이 서비스는 받은 것만 본다"""
    question: str = Field(
        min_length=1, max_length=1000,
        description="그 턴의 사용자 질문 원문",
        examples=["작업 어떻게 만들어?"],
    )
    answer: str = Field(
        default="", max_length=4000,
        description="그 턴의 답변 또는 요약. 답변에 실패한 턴이면 빈 문자열로 둔다",
        examples=["작업은 보드 화면 상단의 새 작업 버튼으로 만들 수 있습니다."],
    )

    def to_domain(self) -> ConversationTurn:
        return ConversationTurn(question=self.question, answer=self.answer)


class AskRequest(BaseModel):
    """
    질문 + 이전 대화. top_k·vector_weight 등 검색 파라미터는 서버 기본값(Settings)을 쓴다.

    첫 대화면 history와 conversation_id를 둘 다 생략하면 된다(빈 값이 곧 첫 턴이다).
    "첫 턴인지" 여부를 별도 플래그로 받지 않는다 — 같은 사실을 두 군데서 표현하면 반드시 어긋난다.
    """
    query: str = Field(min_length=1, max_length=1000, examples=["팀원을 어떻게 추가해?"])

    history: List[ConversationTurnIn] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_TURNS,
        description=(
            "이전 대화를 오래된 순으로. 첫 대화면 생략하거나 빈 배열. "
            "서버는 최근 몇 턴만 사용하므로 전부 보낼 필요는 없다."
        ),
    )

    conversation_id: Optional[str] = Field(
        default=None, max_length=100,
        description=(
            "백엔드가 발급한 대화 식별자. 첫 대화면 생략한다. "
            "답변 생성에는 쓰지 않고 품질 로그에만 기록한다(나중에 운영 콘솔에서 "
            "어떤 대화에 대한 평가인지 되짚을 조인 키). 없어도 답변은 정상 동작한다."
        ),
        examples=["conv_01H8XK..."],
    )


class AskResponse(BaseModel):
    raw_query: str = Field(description="사용자가 보낸 원문")
    cleaned_query: str = Field(
        description=(
            "query_transform이 정제한 검색어. 이전 대화가 있으면 대명사·생략을 "
            "풀어낸 결과다. 재작성 실패와 검색 실패를 구분하는 단서이므로 로그에 남긴다"
        )
    )
    needs_search: bool = Field(description="false면 검색·생성을 건너뛴다")

    conversation_id: Optional[str] = Field(
        default=None, description="요청에 담겨 온 값을 그대로 돌려준다(로그 대조용)"
    )
    history_turns_used: int = Field(
        default=0, description="재작성에 실제로 사용한 이전 턴 수. 0이면 첫 턴처럼 처리했다는 뜻"
    )

    answer: str
    doc_ids: List[str] = Field(description="답변의 근거가 된 answer_units 식별자")

    documents: List[AnswerUnitOut] = Field(
        default_factory=list, description="근거 문서 본문. 항상 포함한다"
    )


class SearchRequest(SearchOptions):
    """답변 생성 없이 검색만 — LLM 비용 0으로 vector_weight를 튜닝할 때 쓴다"""
    query: str = Field(min_length=1, max_length=1000, examples=["스프린트 기간"])
    transform: bool = Field(default=False, description="true면 query_transform으로 정제 후 검색")


class SearchResponse(BaseModel):
    query: str = Field(description="실제로 검색에 사용한 문자열")
    hits: List[SearchHitOut]
    documents: List[AnswerUnitOut]
