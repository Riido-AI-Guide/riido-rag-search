from dataclasses import dataclass, field
from typing import List

from domain import AnswerSection


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
        return "\n\n".join(
            f"**{s.label}**  \n{s.text}" if s.label else s.text
            for s in self.sections
        )

    @property
    def is_answered(self) -> bool:
        """근거를 찾아 실제로 답한 경우에만 True"""
        return self.answer_type not in ("no_answer", "error", "parse_error") and bool(self.sections)
