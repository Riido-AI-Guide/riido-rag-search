"""
api/schemas/common.py — 공통 응답 스키마

[DTO 설계 원칙]
dto/ 패키지의 dataclass(도메인 모델)와 API 스키마(Pydantic)를 분리한다.
  - dto/*        : 모듈 사이에서 주고받는 내부 표현. 검색 점수·임베딩 등 내부 값 포함
  - api/schemas/*: HTTP 경계의 계약. 외부에 노출할 필드만, 검증 규칙과 예시를 붙여서

분리하는 이유:
  1. RetrievedChunk.hits처럼 내부에서만 쓰는 필드를 응답에서 자연스럽게 제외할 수 있다
  2. 내부 리팩터링이 API 계약을 깨지 않는다 (from_domain()만 고치면 됨)
  3. OpenAPI 문서에 검증 규칙(min/max)과 예시가 그대로 반영된다

변환은 각 스키마의 from_domain() 클래스메서드 한 곳에서만 한다.
"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """오프셋 기반 페이지네이션 공통 응답"""
    total: int = Field(description="필터를 적용한 전체 행 수")
    limit: int
    offset: int
    items: List[T]
