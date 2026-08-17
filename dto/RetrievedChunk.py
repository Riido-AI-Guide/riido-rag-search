from dataclasses import dataclass, field


@dataclass
class RetrievedChunk():
    id: int # 문서 청크 ID
    title: str # 문서 제목
    section: str # 문서 경로
    content: str # 문서 내용
    source_type: str = field(default="guide") # 문서 유형: guide/qa
    v_similarity: float = 0.0 # 벡터 검색 유사도
    k_similarity: float = 0.0 # 키워드 검색 유사도
    rrf_score: float = 0.0 # RRF 계산 값