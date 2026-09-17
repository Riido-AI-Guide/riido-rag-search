"""
api/schemas/index_status.py — 인덱스 건강 상태 응답
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class StaleGroup(BaseModel):
    """낡은 문서 묶음. 목록은 표본이고 개수가 전체다"""
    count: int
    doc_ids: List[str] = Field(default_factory=list, description="앞에서 20개까지의 표본")

    @classmethod
    def from_pair(cls, pair: Tuple[int, List[str]]) -> "StaleGroup":
        return cls(count=pair[0], doc_ids=pair[1])


class IndexStatus(BaseModel):
    """
    답변은 최신인데 검색만 과거인 상태를 잡아내기 위한 화면.
    행 수만 보는 /health로는 이 셋이 보이지 않는다.
    """
    status: Literal["ok", "stale"] = Field(
        description="stale이면 재빌드가 필요하다. 오류가 아니라 '검색이 최신이 아니다'라는 뜻"
    )
    answer_units: int = Field(description="적재된 답변 문서 수")
    built_at: Optional[datetime] = Field(
        default=None, description="answer_units가 마지막으로 바뀐 시각(빌드 시각)"
    )

    no_search_units: StaleGroup = Field(
        description="검색 문장이 없어 **벡터 검색에서 안 걸리는** 문서. "
                    "rag_view_sentences.json에 문장을 넣어야 해결된다(사람 손이 필요)"
    )
    no_content_vector: StaleGroup = Field(
        description="원문 인덱스가 없어 **키워드 검색에서 안 걸리는** 문서"
    )
    outdated_content_vector: StaleGroup = Field(
        description="원문 인덱스가 **옛 본문으로 색인된** 문서. 답변은 최신인데 검색만 과거다"
    )

    hint: Optional[str] = Field(default=None, description="무엇을 실행하면 되는지")

    rebuilding: bool = Field(
        default=False,
        description="지금 재빌드가 도는 중인지. 콘솔이 이 값이 false가 될 때까지 폴링한다",
    )

    @classmethod
    def from_row(cls, row: Dict[str, Any], rebuilding: bool = False) -> "IndexStatus":
        no_search = StaleGroup.from_pair(row["no_search_units"])
        no_vector = StaleGroup.from_pair(row["no_content_vector"])
        outdated = StaleGroup.from_pair(row["outdated_content_vector"])

        hints: List[str] = []
        if no_vector.count or outdated.count:
            # 둘 다 build_search_units 한 번으로 해결된다 (없는 것과 낡은 것을 함께 다시 만든다)
            hints.append("python -m scripts.build_search_units 로 원문 검색 인덱스를 갱신하세요.")
        if no_search.count:
            hints.append(
                f"검색 문장이 없는 문서 {no_search.count}건은 자동으로 채워지지 않습니다 — "
                "data/rag_view_sentences.json에 문장을 추가한 뒤 같은 스크립트를 실행하세요."
            )

        stale = bool(no_search.count or no_vector.count or outdated.count)
        return cls(
            status="stale" if stale else "ok",
            answer_units=row["answer_units"],
            built_at=row["built_at"],
            no_search_units=no_search,
            no_content_vector=no_vector,
            outdated_content_vector=outdated,
            hint=" ".join(hints) or None,
            rebuilding=rebuilding,
        )
