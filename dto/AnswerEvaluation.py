from dataclasses import dataclass


@dataclass
class AnswerEvaluation:
    faithfulness: float = 0.0       # 충실도 (0.0 ~ 1.0, 1.0일수록 환각 없음)
    answer_relevance: float = 0.0   # 답변 관련성 (0.0 ~ 1.0)
    context_relevance: float = 0.0  # 문서 관련성 (0.0 ~ 1.0)
    reason: str = ""                # 평가 이유 및 감점 사유
    error: bool = False             # True = 평가 자체가 실패(API 오류 등). 점수 집계(평균)에서 반드시 제외할 것
