from dataclasses import dataclass, field
from typing import List


@dataclass
class RawChunk:
    title: str # 문서 제목
    section: str # 문서 경로
    content: str # 문서 내용
    source_type: str = field(default="guide") # 문서 유형: guide/qa
    doc_id: str = "" # 답변 단위 식별자 (answer_units PK)
    ord_idx: int = 0 # 원문 등장 순서
    source_hash: str = "" # content 해시 (증분 빌드 판정용)