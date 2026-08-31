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
    content: Optional[str] = Field(default=None, description="본문. include_content=false면 생략")

    @classmethod
    def from_row(cls, row: Dict[str, Any], include_content: bool = True) -> "AnswerUnitOut":
        return cls(
            doc_id=row["doc_id"],
            title=row["title"],
            section=row["section"],
            source_type=row["source_type"],
            ord_idx=row["ord_idx"],
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
            content=chunk.content if include_content else None,
        )


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
