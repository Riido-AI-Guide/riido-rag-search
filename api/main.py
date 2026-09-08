"""
api/main.py — FastAPI 진입점

실행:  uvicorn api.main:app --reload      (프로젝트 루트에서)
문서:  http://127.0.0.1:8000/docs
"""

import logging
import time
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.settings import get_settings
from core.db import close_pool, init_pool
from api.routers import (
    answer_units, chat, evaluations, health, index_status, qna, search_units,
)
from core.evaluation import EvaluationError
from core.generation import LlmError

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 첫 요청이 풀 생성 비용을 떠안지 않도록 미리 풀을 만든다
    init_pool()

    # core.search는 로드 시점에 Kiwi와 임베딩 클라이언트를 만든다(수 초 소요).
    # 첫 요청이 이 비용을 떠안지 않도록 부팅 때 미리 끌어올린다.
    started = time.perf_counter()
    from core.search import warmup  # 최상단에 두면 로드 비용이 부팅 전으로 앞당겨진다
    warmup()
    logger.info("검색 모듈 로드 완료 (%.1fs)", time.perf_counter() - started)

    yield
    close_pool()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Riido RAG Search API",
        version="0.1.0",
        description="뤼이도 이용 가이드 기반 RAG 검색·답변 API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        health.router, chat.router, answer_units.router,
        search_units.router, evaluations.router, qna.router, index_status.router,
    ):
        app.include_router(router, prefix=settings.api_prefix)

    @app.exception_handler(psycopg2.errors.UndefinedTable)
    @app.exception_handler(psycopg2.errors.UndefinedColumn)
    async def undefined_table_handler(request: Request, exc: psycopg2.Error):
        """인덱스가 아직 없거나, 스키마가 코드보다 오래됐을 때(빌드 스크립트가 스키마를 맞춘다)"""
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "인덱스 테이블이 없습니다.",
                "hint": "python -m scripts.build_answer_units → python -m scripts.build_search_units 순으로 실행하세요.",
            },
        )

    @app.exception_handler(LlmError)
    async def llm_error_handler(request: Request, exc: LlmError):
        """답변 생성 실패"""
        logger.exception("답변 생성 실패")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc), "hint": "잠시 후 다시 시도해 주세요."},
        )

    @app.exception_handler(EvaluationError)
    async def evaluation_error_handler(request: Request, exc: EvaluationError):
        """
        채점 실패. /ask 뒤 백그라운드로 도는 평가는 여기 오지 않는다(그쪽은 예외를 삼키고
        미평가로 남긴다) — 결과를 기다리는 재실행 API만 이 경로를 탄다.
        """
        logger.warning("평가 재실행 실패: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": f"판정자가 평가에 실패했습니다: {exc}",
                "hint": "아무것도 저장되지 않아 미평가로 남습니다. 잠시 후 다시 시도해 주세요.",
            },
        )

    @app.exception_handler(psycopg2.OperationalError)
    async def db_down_handler(request: Request, exc: psycopg2.OperationalError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "데이터베이스에 연결할 수 없습니다.", "hint": str(exc).strip()},
        )

    return app


app = create_app()
