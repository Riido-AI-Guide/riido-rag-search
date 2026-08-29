"""
api/services/rag_service.py — 질의 → 검색 → 답변 → (선택) 평가 오케스트레이션
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from domain import AnswerEvaluation, ConversationTurn, RetrievedChunk
from core.evaluation import EvaluationError, evaluate_faithfulness
from core.generation import generate_rag_answer
from core.query_transform import generate_conversation_title, transform_user_query
from core.search import search as rag_search

logger = logging.getLogger(__name__)

# 미검색 시 메시지
NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."

# 대화 도중 미검색 시 메시지
NO_SEARCH_FOLLOW_UP_MESSAGE = "더 궁금한 점이 있으면 말씀해 주세요."


@dataclass
class AskResult:
    raw_query: str
    cleaned_query: str
    needs_search: bool
    answer: str
    documents: List[RetrievedChunk] = field(default_factory=list)
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

    # 제목은 첫 턴에서만 만든다. (history, conversation_id가 없을 경우)
    # 인사·잡담(needs_search=False)이어도 만든다.
    if not history and not conversation_id:
        transformed.conversation_title = generate_conversation_title(query)

    # 인사·잡담이면 검색, 생성 x
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

    # 검색과 생성에는 재작성(전처리)된 질문만 넘긴다. -> 이후 단계는 단일턴과 동일
    # hits(문장 단위 점수)는 응답에 싣지 않는다 — 근거는 문서 단위로만 준다
    _, documents = rag_search(
        transformed.cleaned_query, top_k=top_k, vector_weight=vector_weight
    )

    # LlmError는 잡지 않는다. 답변 생성 실패는 요청 실패이므로 그대로 올려보낸다.
    answer = generate_rag_answer(transformed.cleaned_query, documents)

    # 추후 평가 기능 추가를 위한 placeholder
    evaluation = None
    if evaluate:
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
        evaluation=evaluation,
        conversation_id=conversation_id,
        history_turns_used=len(recent_history),
        title=transformed.conversation_title,
    )
