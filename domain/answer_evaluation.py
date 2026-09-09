from dataclasses import dataclass, field
from typing import List


@dataclass
class AnswerEvaluation:
    """
    LLM-as-a-Judge가 답변 하나를 채점한 결과
    평가에 실패하면 이 객체를 만들지 않고 EvaluationError를 올린다.
    """
    faithfulness: float             # 충실도 (0.0 ~ 1.0, 1.0일수록 환각 없음)
    answer_relevance: float         # 답변 관련성 (0.0 ~ 1.0)
    context_relevance: float        # 문서 관련성 (0.0 ~ 1.0). 검색된 문서 전체를 보고 매긴다
    verdict: str                    # "pass" / "fail". 사용자 good/bad와 그대로 대조하는 축

    # 문제 유형 리스트
    issues: List[str] = field(default_factory=list)
    
    reason: str = ""                # 감점 사유
