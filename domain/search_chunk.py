from dataclasses import dataclass, field
from typing import List


@dataclass
class SearchChunk:
    doc_id: str # 답변 단위 식별자 (answer_units PK)
    view_type: str # 문장 유형: hypo_q/real_q/contextual
    text: str # 검색 대상 문장 (가설질문/실제질문/맥락요약)
    text_tsv: str = "" # 형태소 분해한 값
    embedding: List[float] = field(default_factory=list) # 임베딩된 벡터
