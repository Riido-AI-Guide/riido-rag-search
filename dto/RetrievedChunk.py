from dataclasses import dataclass

from dto import RawChunk


@dataclass
class RetrievedChunk(RawChunk):
    v_similarity: float = 0.0 # 벡터 검색 유사도
    s_similarity: float = 0.0 # 키워드 검색 유사도
    rrf_score: float = 0.0 # RRF 계산 값