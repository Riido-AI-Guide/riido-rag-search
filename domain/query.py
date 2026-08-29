from dataclasses import dataclass
from typing import List


@dataclass
class Query:
	raw_query: str # 질문 원문
	cleaned_query: str # 전처리된 질문
	search_queries: List[str] # 유사한 질문 리스트
	needs_search: bool # 문서 검색 필요 여부

	# 대화 목록에 걸 제목. 첫 턴에서만 채우고 후속 턴은 빈 문자열
	conversation_title: str = ""
