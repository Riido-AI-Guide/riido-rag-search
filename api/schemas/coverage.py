"""
api/schemas/coverage.py — 검색 문장 커버리지·등록 스키마 (운영 콘솔)
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from domain import VIEW_TYPES

# view_type은 domain이 어휘의 주인이다. 늘리려면 domain/search_chunk.py에 추가한다.
ViewType = Literal[VIEW_TYPES]  # type: ignore[valid-type]

CoverageStatus = Literal["missing", "outdated", "ok"]

# 콘솔 입력 한 줄의 상한. 검색 문장은 질문 한 줄이지 문단이 아니다.
MAX_SENTENCE_CHARS = 300

# 한 문서에 달 수 있는 문장 수. 임베딩을 한 번에 만들기도 하고,
# 사람이 한 화면에서 검토할 수 있는 양이기도 하다.
MAX_SENTENCES_PER_DOC = 30


class DocCoverage(BaseModel):
    """문서 1건의 검색 문장 현황 (목록 행)"""
    doc_id: str = Field(examples=["guide/팀/팀-관리"])
    title: str
    section: str = Field(examples=["팀 > 팀 관리"])
    status: CoverageStatus = Field(
        description=(
            "missing=문장이 없어 벡터 검색에서 안 걸림 / "
            "outdated=본문이 바뀐 뒤 문장을 손보지 않음 / ok=지금 본문 기준"
        )
    )
    units: int = Field(description="이 문서에 달린 검색 문장 수")
    outdated: int = Field(description="그중 옛 본문 기준인 문장 수")
    view_types: Dict[str, int] = Field(
        default_factory=dict,
        description="유형별 개수. 유형은 고정 개수가 아니다 — 한 유형에 여러 문장이 올 수 있다",
        examples=[{"hypo_q": 2, "real_q": 1, "contextual": 1}],
    )
    content_updated_at: Optional[datetime] = Field(
        default=None, description="본문이 마지막으로 바뀐 시각"
    )

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "DocCoverage":
        return cls(
            doc_id=row["doc_id"],
            title=row["title"],
            section=row["section"],
            status=row["status"],
            units=row["units"],
            outdated=row["outdated"],
            view_types=dict(row["view_types"] or {}),
            content_updated_at=row["updated_at"],
        )


class DocSentence(BaseModel):
    """등록된 검색 문장 1건"""
    id: int
    view_type: str
    text: str
    source: str = Field(description="file=rag_view_sentences.json / console=이 API로 등록")
    outdated: bool = Field(description="본문이 바뀐 뒤 손보지 않은 문장이면 true")
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "DocSentence":
        return cls(
            id=row["id"],
            view_type=row["view_type"],
            text=row["text"],
            source=row["source"],
            outdated=bool(row["outdated"]),
            updated_at=row["updated_at"],
        )


class DocSentenceSet(DocCoverage):
    """문서 1건의 문장 전체 + 그 결과 상태. 수정(PUT)의 응답이다"""
    sentences: List[DocSentence] = Field(default_factory=list)

    @classmethod
    def build(cls, row: Dict[str, Any], sentences: List[Dict[str, Any]]) -> "DocSentenceSet":
        return cls(
            **DocCoverage.from_row(row).model_dump(),
            sentences=[DocSentence.from_row(s) for s in sentences],
        )


class DocCoverageDetail(DocSentenceSet):
    """+ 본문 전문. 콘솔 편집 화면이 이 응답 하나로 그려진다"""
    url: str = Field(default="", description="원문 주소. 없으면 빈 문자열")
    content: str = Field(description="문서 전문. 문장은 이걸 보고 쓴다")

    @classmethod
    def build(cls, row: Dict[str, Any], sentences: List[Dict[str, Any]]) -> "DocCoverageDetail":
        return cls(
            **DocSentenceSet.build(row, sentences).model_dump(),
            url=row["source_url"] or "",
            content=row["content"],
        )


class SentenceInput(BaseModel):
    """콘솔 입력 한 줄 — 유형과 문장의 짝"""
    view_type: ViewType = Field(description=" / ".join(VIEW_TYPES))
    text: str = Field(min_length=2, max_length=MAX_SENTENCE_CHARS, examples=["팀원 추가 어떻게 해?"])


class SentenceSetRequest(BaseModel):
    """
    그 문서의 문장 **전체**. 화면에 보이는 목록을 그대로 보낸다 —
    빠진 문장은 삭제되고, 새 문장은 추가되고, 유형만 바뀐 문장은 갱신된다.
    """
    items: List[SentenceInput] = Field(
        max_length=MAX_SENTENCES_PER_DOC,
        description=(
            f"이 문서의 문장 전체. 한 문서에 {MAX_SENTENCES_PER_DOC}개까지. "
            "빈 배열을 보내면 이 문서의 문장이 모두 지워진다(검색에서 빠지므로 주의)"
        ),
    )


class DraftRequest(BaseModel):
    doc_id: str = Field(min_length=1, examples=["guide/팀/팀-관리"])
    hypo_count: int = Field(
        default=3, ge=1, le=10, description="만들 가설질문 수. real_q·contextual은 각 1개"
    )
    exclude_existing: bool = Field(
        default=True, description="이미 등록된 문장과 겹치지 않게 한다"
    )


class DraftResponse(BaseModel):
    """초안. **저장되지 않았다** — 사람이 고른 것만 등록 API로 다시 보낸다"""
    doc_id: str
    items: List[SentenceInput]
