"""
api/routers/chat.py — 질의응답 및 검색 전용 엔드포인트

동기 함수(def)로 선언한 이유: psycopg2·openai·langchain이 모두 동기 라이브러리라
async def로 두면 이벤트 루프가 막힌다. FastAPI가 스레드풀에서 실행해 준다.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.config import Settings, get_settings
from api.deps import resolve_search_params
from api.schemas.chat import (
    AskRequest,
    AskResponse,
    EvaluationOut,
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
    description="질문 정제 → 하이브리드 검색 → 답변 생성 → (선택) 환각 평가",
)
def ask(req: AskRequest, settings: Settings = Depends(get_settings)) -> AskResponse:
    top_k, vector_weight = resolve_search_params(req.top_k, req.vector_weight, settings)

    try:
        result = rag_service.ask(
            query=req.query, top_k=top_k, vector_weight=vector_weight, evaluate=req.evaluate
        )
    except rag_service.AnswerGenerationError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return AskResponse(
        raw_query=result.raw_query,
        cleaned_query=result.cleaned_query,
        search_queries=result.search_queries,
        needs_search=result.needs_search,
        answer=result.answer,
        doc_ids=result.doc_ids,
        documents=(
            [AnswerUnitOut.from_domain(d) for d in result.documents]
            if req.include_documents else None
        ),
        hits=(
            [SearchHitOut.from_domain(h) for h in result.hits]
            if req.include_hits else None
        ),
        evaluation=EvaluationOut.from_domain(result.evaluation) if result.evaluation else None,
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
