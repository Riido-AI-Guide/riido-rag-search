from dataclasses import dataclass, field
from typing import List

from domain import AnswerSection, sections_to_text

# 검색을 건너뛴 인사·잡담 경로의 answer_type.
# Answer가 만들어지지 않는 유일한 경로라 core.generation의 유형 목록에는 없고,
# api 쪽에서만 붙는다. 채점 대상에서도 빠지므로 로그 조회가 이 값을 다시 본다.
NO_SEARCH_ANSWER_TYPE = "no_search"


@dataclass
class Answer:
    title: str = ""                 # 답변 카드 제목 (예: "대기 VS 백로그")
    answer_type: str = "concept"    # concept/step/judgement/troubleshoot/explore/no_answer/parse_error
    sections: List[AnswerSection] = field(default_factory=list)
    raw: str = ""                   # LLM 원본 응답. 파싱 실패·오류 시 여기에만 값이 있다

    @property
    def message(self) -> str:
        """
        평가·복사·로깅용 평문(마크다운). sections가 없으면 raw를 그대로 준다.
        """
        if not self.sections:
            return self.raw
        return sections_to_text(self.sections)

    @property
    def is_answered(self) -> bool:
        """근거를 찾아 실제로 답한 경우에만 True"""
        return self.answer_type not in ("no_answer", "error", "parse_error") and bool(self.sections)
