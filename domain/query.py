from dataclasses import dataclass
from typing import List


@dataclass
class Query:
	raw_query: str # 질문 원문
	cleaned_query: str # 전처리된 질문
	search_queries: List[str] # 유사한 질문 리스트
	needs_search: bool # 문서 검색 필요 여부

	# 대화 목록에 걸 제목. 첫 턴에서만 채우고 후속 턴은 빈 문자열이다.
	#
	# 대화 단위 값을 "질문" DTO에 두는 이유: /ask는 요청마다 질문 1개 + 히스토리만
	# 받는 무상태 서비스라 Conversation에 해당하는 클래스가 이쪽에 없다(대화의 소유자는
	# 백엔드다). 첫 턴에서는 이 질문이 곧 대화 전체이고 제목도 질문 전처리 단계에서
	# 만들어지므로, 전처리 결과를 담는 이 자리가 제자리다. 영속 저장은 백엔드가 한다.
	#
	# Answer.title과는 다른 값이다 — 그쪽은 답변 카드 하나의 제목이고 매 턴 바뀐다.
	# 이름을 title로 겹쳐 두면 나중에 반드시 섞이므로 conversation_title로 구분한다.
	conversation_title: str = ""
