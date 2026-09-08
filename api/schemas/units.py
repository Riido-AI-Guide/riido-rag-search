"""
api/schemas/units.py — answer_units / search_units 조회 응답
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from domain import RetrievedChunk


class AnswerUnitOut(BaseModel):
    doc_id: str = Field(description="답변 단위 식별자", examples=["guide/팀/팀-관리"])
    title: str
    section: str = Field(examples=["팀 > 팀 관리"])
    source_type: str = Field(examples=["guide"])
    ord_idx: int = Field(description="원문 등장 순서")
    url: str = Field(
        default="",
        description=(
            "이 문서의 원문 주소(docs.riido.io). 가능하면 섹션 앵커까지 붙는다. "
            "링크를 못 붙인 문서는 빈 문자열"
        ),
        examples=["https://docs.riido.io/data/trash#undefined-2"],
    )
    content: Optional[str] = Field(default=None, description="본문. include_content=false면 생략")

    @classmethod
    def from_row(cls, row: Dict[str, Any], include_content: bool = True) -> "AnswerUnitOut":
        return cls(
            doc_id=row["doc_id"],
            title=row["title"],
            section=row["section"],
            source_type=row["source_type"],
            ord_idx=row["ord_idx"],
            url=row["source_url"] or "",
            content=row["content"] if include_content else None,
        )

    @classmethod
    def from_domain(cls, chunk: RetrievedChunk, include_content: bool = True) -> "AnswerUnitOut":
        return cls(
            doc_id=chunk.doc_id,
            title=chunk.title,
            section=chunk.section,
            source_type=chunk.source_type,
            ord_idx=chunk.ord_idx,
            url=chunk.source_url,
            content=chunk.content if include_content else None,
        )


class AnswerUnitListItem(AnswerUnitOut):
    """
    목록 행 — 이 문서가 **어떻게 검색되는지**를 함께 준다.

    유형을 고정 필드로 두지 않는다. 한 유형에 문장이 여러 개일 수 있고 유형 자체도
    늘어날 수 있어서(→ domain/search_chunk.py의 VIEW_TYPES), 있는 것만 맵으로 준다.
    빈 맵이면 검색 문장이 하나도 없다는 뜻이고, 그 문서는 벡터 검색에서 걸리지 않는다.
    """
    view_types: Dict[str, int] = Field(
        default_factory=dict,
        description="이 문서에 달린 검색 문장 수(유형별). 비어 있으면 벡터 검색에서 빠진 문서다",
        examples=[{"hypo_q": 2, "real_q": 1, "contextual": 1}],
    )

    @classmethod
    def from_row(cls, row: Dict[str, Any], include_content: bool = True) -> "AnswerUnitListItem":
        base = AnswerUnitOut.from_row(row, include_content)
        return cls(**base.model_dump(), view_types=dict(row["view_types"] or {}))


class SearchUnitOut(BaseModel):
    id: int
    doc_id: str = Field(description="이 문장이 가리키는 answer_units 문서")
    view_type: str = Field(description="hypo_q(가설질문) / real_q(실제질문) / contextual(맥락요약)")
    text: str

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "SearchUnitOut":
        return cls(id=row["id"], doc_id=row["doc_id"], view_type=row["view_type"], text=row["text"])


class ViewTypeStat(BaseModel):
    view_type: str
    units: int
    docs: int


class AnswerUnitDetail(AnswerUnitOut):
    """단건 조회 — 이 문서를 가리키는 검색 문장까지 함께 준다"""
    search_units: List[SearchUnitOut] = Field(default_factory=list)
