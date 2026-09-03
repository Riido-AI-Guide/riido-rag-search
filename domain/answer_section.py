from dataclasses import dataclass, field
from typing import List


@dataclass
class SourceRef:
    """답변 섹션이 근거로 삼은 문서 1건"""
    doc_id: str   # 답변 단위 식별자 (answer_units PK)
    section: str  # 문서 경로 (예: "팀 > 팀 관리") — 화면에 그대로 표시
    url: str = "" # 원문 링크. 근거 버튼을 걸 주소이고, 없으면 빈 문자열


@dataclass
class AnswerSection:
    """답변을 이루는 한 덩어리 (핵심답변 / 단계별방법 / 주의사항 …)"""
    label: str   # 섹션 이름. 프론트가 이 값으로 스타일을 정한다
    text: str    # 섹션 본문
    sources: List[SourceRef] = field(default_factory=list)  # 이 섹션의 근거
