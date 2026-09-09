from dataclasses import dataclass, field
from typing import List

# 검색 문장의 유형. 늘리려면 여기에 추가한다 — API 검증과 프롬프트가 이 목록을 본다.
#   hypo_q     : 이 문서로 답할 수 있는 가설 질문 (문서당 여러 개가 정상)
#   real_q     : 사용자가 실제로 칠 법한 질문
#   contextual : 이 문서가 무엇을 다루는지 요약한 문장
VIEW_TYPES = ("hypo_q", "real_q", "contextual")


@dataclass
class SearchChunk:
    doc_id: str # 답변 단위 식별자 (answer_units PK)
    view_type: str # 문장 유형: hypo_q/real_q/contextual
    text: str # 검색 대상 문장 (가설질문/실제질문/맥락요약)
    text_tsv: str = "" # 형태소 분해한 값
    embedding: List[float] = field(default_factory=list) # 임베딩된 벡터
