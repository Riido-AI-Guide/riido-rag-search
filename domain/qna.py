from dataclasses import dataclass
from typing import List, Optional

from domain import Answer, Query, RetrievedChunk

@dataclass
class QnA:
    query: Query # 질문
    documents: List[RetrievedChunk] # 문서 청크
    answer: Optional[Answer] # 질문에 대한 답변
    is_good: Optional[bool] = None # 사용자가 질문에 대해 평가(good/bad)
		