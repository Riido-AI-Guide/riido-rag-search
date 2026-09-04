"""
api/services/rag_service.py — 질의 → 검색 → 답변 → (선택) 평가 오케스트레이션
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from domain import AnswerEvaluation, AnswerSection, ConversationTurn, RetrievedChunk
from core.evaluation import EvaluationError, evaluate_answer
from core.generation import generate_rag_answer
from core.query_transform import normalize_title, transform_user_query
from core.search import search as rag_search

logger = logging.getLogger(__name__)

# 미검색 시 메시지
NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."

# 대화 도중 미검색 시 메시지
NO_SEARCH_FOLLOW_UP_MESSAGE = "더 궁금한 점이 있으면 말씀해 주세요."

# 검색을 건너뛴 경로의 answer_type. Answer가 만들어지지 않는 유일한 경로라
# core.generation의 유형 목록에 없고 여기서만 붙는다.
NO_SEARCH_ANSWER_TYPE = "no_search"

# 인사·잡담으로 대화를 시작했을 때의 제목. 그 경로엔 답변이 없어 뽑아 쓸 제목이 없다.
NO_SEARCH_TITLE = "새 대화"


@dataclass
class AskResult:
    raw_query: str
    cleaned_query: str
    needs_search: bool

    # 답변의 유형. Answer.answer_type을 그대로 실어 나르되, 검색을 건너뛴 경로만
    # 여기서 no_search로 채운다(그 경로엔 Answer가 아예 없다).
    answer_type: str

    # 이 턴의 제목. Answer.title을 다듬어 쓴다 — 제목 전용 LLM 호출은 하지 않는다.
    # 첫 턴이면 이 값이 곧 대화 제목이고, 후속 턴이면 말풍선 제목이다.
    # 첫 턴에서는 비지 않는다(비면 질문 원문이나 "새 대화"로 채운다).
    title: str

    # 답변 그 자체. Answer.sections를 그대로 실어 나른다.
    # 평문 필드를 따로 두지 않는다 — 같은 내용을 두 번 들고 있으면 반드시 어긋난다.
    # 어느 경로에서도 비지 않는다: 인사·잡담과 형식 깨짐(parse_error)도
    # label과 sources가 빈 섹션 하나로 들어온다.
    answers: List[AnswerSection]

    documents: List[RetrievedChunk] = field(default_factory=list)
    evaluation: Optional[AnswerEvaluation] = None

    # 답변 생성에는 쓰지 않고 응답에 그대로 돌려보내기만 한다(호출자의 로그 대조용)
    conversation_id: Optional[str] = None

    @property
    def doc_ids(self) -> List[str]:
        """
        답변이 실제로 인용한 문서. 섹션에 처음 등장한 순서대로 주고 중복은 없앤다.

        검색으로 가져온 문서 전체가 아니다 — 검색에는 걸렸지만 답변에 쓰이지 않은
        문서는 빠진다(그쪽은 documents에 그대로 있다). 인용이 하나도 없으면 빈 배열이며,
        답변 형식이 깨진 경우(parse_error)도 여기에 해당한다.
        """
        seen = set()
        ordered: List[str] = []
        for section in self.answers:
            for ref in section.sources:
                if ref.doc_id not in seen:
                    seen.add(ref.doc_id)
                    ordered.append(ref.doc_id)
        return ordered


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
    사용자 질문 하나를 끝까지 처리한다. LLM 호출 2회 + 임베딩 1회
    (질문 재작성 1회 + 답변 생성 1회. 인사·잡담이면 재작성 1회로 끝난다).

    history를 주면 후속 질문의 대명사·생략을 앞 턴에서 풀어 검색어를 만든다.
    비어 있으면(첫 대화) 예전과 완전히 같은 단일턴 경로로 흐른다.

    conversation_id는 처리에 쓰지 않는다. 대화의 소유자는 백엔드이고 여기서는
    응답·로그에 실어 보내기만 한다(운영 콘솔에서 평가와 대화를 잇는 조인 키).

    제목(title)은 매 턴 함께 돌려준다. 답변 생성이 이미 만든 Answer.title을 그대로
    쓰므로 제목 때문에 LLM을 더 부르지 않는다. 첫 턴의 title을 대화 제목으로 삼을지는
    호출자가 정한다 — 대화의 소유자는 여기가 아니다.
    """
    # 최근 턴만 남긴다. 요청이 더 많이 보내와도 서버 정책이 상한이다.
    # (오래된 턴은 지나간 주제로 재작성을 오염시키고 비용만 늘린다)
    recent_history = (history or [])[-max_history_turns:] if max_history_turns > 0 else []

    # 첫 턴 판정(history, conversation_id가 둘 다 없을 때).
    # 제목을 뽑을 답변이 없는 인사·잡담 경로에서만 쓴다.
    is_first_turn = not history and not conversation_id

    transformed = transform_user_query(query, history=recent_history)

    # 인사·잡담이면 검색, 생성 x
    if not transformed.needs_search:
        return AskResult(
            raw_query=query,
            cleaned_query=transformed.cleaned_query,
            needs_search=False,
            answer_type=NO_SEARCH_ANSWER_TYPE,
            # 첫 턴이면 대화 목록에 걸 이름이 필요하지만, 후속 턴의 "고마워"에까지
            # "새 대화"를 붙일 이유는 없다 — 그쪽은 제목 없는 말풍선으로 둔다.
            title=NO_SEARCH_TITLE if is_first_turn else "",
            answers=[AnswerSection(
                label="",
                text=NO_SEARCH_FOLLOW_UP_MESSAGE if recent_history else NO_SEARCH_MESSAGE,
            )],
            conversation_id=conversation_id,
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
            evaluation = evaluate_answer(
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
        answer_type=answer.answer_type,
        title=normalize_title(answer.title, query),
        answers=answer.sections,
        documents=documents,
        evaluation=evaluation,
        conversation_id=conversation_id,
    )
