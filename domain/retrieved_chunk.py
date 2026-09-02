from dataclasses import dataclass, field
from typing import List

from domain import SearchHit


@dataclass
class RetrievedChunk():
    """answer_units 한 행 = 검색으로 가져온 답변 본문 1건"""
    doc_id: str # 답변 단위 식별자 (answer_units PK)
    title: str # 문서 제목
    section: str # 문서 경로
    content: str # 문서 내용
    source_type: str = field(default="guide") # 문서 유형: guide/qa
    ord_idx: int = 0 # 원문 등장 순서
    source_url: str = "" # 원문 링크. 못 붙인 문서는 빈 문자열
    hits: List[SearchHit] = field(default_factory=list) # 이 문서를 끌어온 검색 단위들
