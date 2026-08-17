from dataclasses import dataclass, field
from typing import List


@dataclass
class RawChunk:
    title: str # 문서 제목
    section: str # 문서 경로
    content: str # 문서 내용
    content_keywords: str = ""  # 문서의 불용어, 어근 등을 제거
    embedding: List[float] = field(default_factory=list) # 임베딩된 벡터
    source_type: str = field(default="guide") # 문서 유형: guide/qa