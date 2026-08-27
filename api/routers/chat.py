"""
api/routers/chat.py — 질의응답 및 검색 전용 엔드포인트

동기 함수(def)로 선언한 이유: psycopg2·openai·langchain이 모두 동기 라이브러리라
async def로 두면 이벤트 루프가 막힌다. FastAPI가 스레드풀에서 실행해 준다.
"""

from fastapi import APIRouter, Depends

from api.config import Settings, get_settings
from api.deps import resolve_search_params
from api.schemas.chat import (
    AskRequest,
    AskResponse,
    SearchHitOut,
    SearchRequest,
    SearchResponse,
)
from api.schemas.units import AnswerUnitOut
from api.services import rag_service

router = APIRouter(tags=["chat"])


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="질문에 대한 답변과 근거 문서 id 반환",
    description="질문 정제 → 하이브리드 검색 → 답변 생성. 근거 문서는 항상 함께 반환한다.",
)
def ask(req: AskRequest, settings: Settings = Depends(get_settings)) -> AskResponse:
    # 요청은 query만 받는다. 검색 파라미터는 서버 기본값을 쓰고, 평가는 하지 않는다.
    # LlmError는 api/main.py의 예외 핸들러가 502로 변환한다
    result = rag_service.ask(
        query=req.query,
        top_k=settings.default_top_k,
        vector_weight=settings.default_vector_weight,
    )

    return AskResponse(
        raw_query=result.raw_query,
        cleaned_query=result.cleaned_query,
        needs_search=result.needs_search,
        answer=result.answer,
        doc_ids=result.doc_ids,
        documents=[AnswerUnitOut.from_domain(d) for d in result.documents],
    )


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="답변 생성 없이 검색만 수행",
    description="LLM 호출 없이 검색 품질과 vector_weight를 확인할 때 사용한다.",
)
def search(req: SearchRequest, settings: Settings = Depends(get_settings)) -> SearchResponse:
    top_k, vector_weight = resolve_search_params(req.top_k, req.vector_weight, settings)

    used_query, hits, documents = rag_service.search_only(
        query=req.query, top_k=top_k, vector_weight=vector_weight, transform=req.transform
    )

    return SearchResponse(
        query=used_query,
        hits=[SearchHitOut.from_domain(h) for h in hits],
        documents=[AnswerUnitOut.from_domain(d) for d in documents],
    )
