"""
api/services/rag_service.py — 질의 → 검색 → 답변 → (선택) 평가 오케스트레이션

test_rag.py가 스크립트로 하던 흐름을 그대로 함수로 옮긴 것이다.
기존 모듈은 수정하지 않고 호출만 한다.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from domain import AnswerEvaluation, ConversationTurn, RetrievedChunk, SearchHit
from core.evaluation import EvaluationError, evaluate_faithfulness
from core.generation import generate_rag_answer
from core.query_transform import generate_conversation_title, transform_user_query
from core.search import search as rag_search

logger = logging.getLogger(__name__)

NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."

# 대화 도중의 인사·감사에 첫 인사말을 돌려주면 대화가 처음으로 되감긴 것처럼 보인다.
NO_SEARCH_FOLLOW_UP_MESSAGE = "더 궁금한 점이 있으면 말씀해 주세요."


@dataclass
class AskResult:
    raw_query: str
    cleaned_query: str
    needs_search: bool
    answer: str
    documents: List[RetrievedChunk] = field(default_factory=list)
    hits: List[SearchHit] = field(default_factory=list)
    evaluation: Optional[AnswerEvaluation] = None

    # 대화 맥락 — 답변 생성에는 쓰지 않고 응답·로그에 그대로 실어 보낸다
    conversation_id: Optional[str] = None
    history_turns_used: int = 0

    # 대화 목록에 걸 제목. 첫 턴에서만 채우고 후속 턴은 빈 문자열이다.
    # Query.conversation_title을 그대로 실어 나른다(값의 주인은 그쪽이다).
    title: str = ""

    @property
    def doc_ids(self) -> List[str]:
        return [d.doc_id for d in self.documents]


def ask(
    query: str,
    top_k: int,
    vector_weight: float,
    history: Optional[List[ConversationTurn]] = None,
    conversation_id: Optional[str] = None,
    max_history_turns: int = 5,
    evaluate: bool = False,
) -> AskResult:
    """
    사용자 질문 하나를 끝까지 처리한다. LLM 호출 2~3회 + 임베딩 1회.

    history를 주면 후속 질문의 대명사·생략을 앞 턴에서 풀어 검색어를 만든다.
    비어 있으면(첫 대화) 예전과 완전히 같은 단일턴 경로로 흐른다.

    conversation_id는 처리에 쓰지 않는다. 대화의 소유자는 백엔드이고 여기서는
    응답·로그에 실어 보내기만 한다(운영 콘솔에서 평가와 대화를 잇는 조인 키).

    첫 턴이면 대화 제목을 하나 만들어 함께 돌려준다(LLM 호출 1회 추가).
    """
    # 최근 턴만 남긴다. 요청이 더 많이 보내와도 서버 정책이 상한이다.
    # (오래된 턴은 지나간 주제로 재작성을 오염시키고 비용만 늘린다)
    recent_history = (history or [])[-max_history_turns:] if max_history_turns > 0 else []

    transformed = transform_user_query(query, history=recent_history)

    # 제목은 첫 턴에서만 만든다. history도 conversation_id도 없는 요청이 곧 첫 턴이라는
    # 게 /ask의 계약이다(AskRequest 참고). 자르기 전 history로 판단한다 —
    # max_history_turns=0으로 recent_history가 비어도 그건 첫 턴이 아니다.
    #
    # 후속 턴에 다시 만들지 않는 이유: 백엔드가 이미 저장한 제목을 매 턴 덮어쓸지
    # 말지 판단해야 하고, 대화가 길어질수록 제목이 흔들린다. 빈 문자열을 내려
    # "바꿀 것 없음"을 뜻하게 하는 편이 계약이 단순하다.
    #
    # 인사·잡담(needs_search=False)이어도 만든다 — 백엔드는 그 턴에도 대화를 만들고,
    # 제목 없는 대화가 목록에 남는다. 그런 질문에는 "새 대화"가 돌아온다.
    if not history and not conversation_id:
        transformed.conversation_title = generate_conversation_title(query)

    # 인사·잡담이면 검색도 생성도 하지 않는다
    if not transformed.needs_search:
        return AskResult(
            raw_query=query,
            cleaned_query=transformed.cleaned_query,
            needs_search=False,
            answer=NO_SEARCH_FOLLOW_UP_MESSAGE if recent_history else NO_SEARCH_MESSAGE,
            conversation_id=conversation_id,
            history_turns_used=len(recent_history),
            title=transformed.conversation_title,
        )

    # 검색과 생성에는 재작성된 질문만 넘긴다.
    # cleaned_query가 이미 맥락이 풀린 자립적인 문장이므로 이후 단계는 단일턴과 동일하다.
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
        needs_search=True,
        answer=answer.message,
        documents=documents,
        hits=hits,
        evaluation=evaluation,
        conversation_id=conversation_id,
        history_turns_used=len(recent_history),
        title=transformed.conversation_title,
    )


def search_only(query: str, top_k: int, vector_weight: float, transform: bool = False):
    """답변 생성 없이 검색만. (사용한 검색어, hits, documents)"""
    search_query = transform_user_query(query).cleaned_query if transform else query
    hits, documents = rag_search(search_query, top_k=top_k, vector_weight=vector_weight)
    return search_query, hits, documents
