"""
api/routers/chat.py — 질의응답 엔드포인트
"""

from fastapi import APIRouter, Depends

from api.settings import Settings, get_settings
from api.schemas.chat import AnswerSectionOut, AskRequest, AskResponse
from api.schemas.units import AnswerUnitOut
from api.services import rag_service

router = APIRouter(tags=["chat"])


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="질문에 대한 답변과 근거 문서 id 반환",
    description=(
        "질문 정제 → 하이브리드 검색 → 답변 생성. 근거 문서는 항상 함께 반환한다.\n\n"
        "답변은 answers(덩어리별 본문 + 그 덩어리의 근거 doc_id)로만 준다 — 평문 필드는 없다. "
        "answers는 어느 경로에서도 비지 않는다.\n\n"
        "이전 대화를 history로 함께 보내면 후속 질문의 대명사·생략을 앞 턴에서 풀어 검색한다. "
        "첫 대화면 history와 conversation_id를 생략하면 되고, 그때는 단일턴과 동일하게 동작한다.\n\n"
        "title은 매 턴 온다. 첫 턴이면 대화 제목으로 저장하고, 후속 턴이면 말풍선 제목으로만 "
        "쓴다 — 후속 턴의 title로 대화 제목을 덮어쓰지 말 것."
    ),
)
def ask(req: AskRequest, settings: Settings = Depends(get_settings)) -> AskResponse:
    # LlmError는 api/main.py의 예외 핸들러가 502로 변환한다
    result = rag_service.ask(
        query=req.query,
        top_k=settings.default_top_k,
        vector_weight=settings.default_vector_weight,
        history=[turn.to_domain() for turn in req.history],
        conversation_id=req.conversation_id,
        max_history_turns=settings.history_turns,
    )

    return AskResponse(
        raw_query=result.raw_query,
        cleaned_query=result.cleaned_query,
        needs_search=result.needs_search,
        conversation_id=result.conversation_id,
        title=result.title,
        answer_type=result.answer_type,
        answers=[AnswerSectionOut.from_domain(s) for s in result.answers],
        doc_ids=result.doc_ids,
        documents=[AnswerUnitOut.from_domain(d) for d in result.documents],
    )
