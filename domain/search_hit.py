from dataclasses import dataclass


@dataclass
class SearchHit:
    """search_units 한 행 = 검색 단위 1건"""
    id: int # 검색 단위 ID
    doc_id: str # 답변 단위 식별자 (answer_units PK)
    view_type: str # 문장 유형: hypo_q/real_q/contextual
    text: str # 검색 대상 문장
    v_similarity: float = 0.0 # 벡터 검색 유사도
    k_similarity: float = 0.0 # 키워드 검색 유사도
    rrf_score: float = 0.0 # RRF 계산 값
