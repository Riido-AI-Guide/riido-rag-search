"""
core/evaluation.py — 답변 평가 (LLM-as-a-Judge)

생성된 답변이 검색된 문서에만 근거하는지(환각 여부)와 질문에 제대로 답했는지를
채점한다. 프롬프트는 core/prompts.py가 갖는다 — 생성 프롬프트와 같은 자리다.
"""

import json
import logging
from typing import Any, List

from openai import OpenAI

from core.config import OPENAI_API_KEY
from core.prompts import EVAL_ISSUE_CODES, EVAL_VERDICTS, build_eval_prompts
from domain import AnswerEvaluation

logger = logging.getLogger(__name__)

# 호출마다 만들면 매번 새 HTTP 커넥션 풀이 생겨 keep-alive를 못 쓴다.
client = OpenAI(api_key=OPENAI_API_KEY)


class EvaluationError(RuntimeError):
    """
    평가 실패.

    실패 시 0.0을 반환하면 이 시스템에서 "완전한 환각" 판정과 값이 같아진다.
    평가가 죽은 것과 답변이 나쁜 것을 구분하려면 예외로 올려야 한다.

    호출자는 이 예외를 받으면 **아무것도 저장하지 않는다.** 실패한 행을 남기면
    "아직 평가 안 함"과 구분되지 않는다. 행이 없어야 미평가 목록에 다시 잡히고,
    운영 콘솔에서 재실행할 수 있다.
    """


# ---------------------------------------------------------------------------
# 응답 파싱 — LLM이 준 값은 전부 화이트리스트로 검증한다
# ---------------------------------------------------------------------------

def _score(raw: Any, key: str) -> float:
    """0.0 ~ 1.0 밖의 값은 잘라낸다. 아예 못 읽으면 평가 실패로 본다."""
    try:
        score = float(raw)
    except (TypeError, ValueError) as e:
        raise EvaluationError(f"{key} 점수를 읽을 수 없습니다: {raw!r}") from e

    if not 0.0 <= score <= 1.0:
        logger.warning("판정자가 범위 밖 %s 점수를 냈다 (잘라냄): %r", key, raw)
        return min(max(score, 0.0), 1.0)
    return score


def _verdict(raw: Any) -> str:
    """
    verdict는 콘솔의 기본 필터이자 사용자 good/bad와 대조하는 축이라,
    읽을 수 없으면 점수를 유추해 채우지 않고 평가 자체를 실패로 본다.
    """
    verdict = str(raw).strip().lower()
    if verdict not in EVAL_VERDICTS:
        raise EvaluationError(f"verdict가 {EVAL_VERDICTS} 중 하나가 아닙니다: {raw!r}")
    return verdict


def _issues(raw: Any) -> List[str]:
    """
    목록 밖의 코드는 버린다. 판정자가 코드를 지어내도 통계가 오염되지 않아야 한다
    (core.generation이 없는 [문서 N] 번호를 버리는 것과 같은 이유).
    """
    if not isinstance(raw, list):
        if raw:
            logger.warning("판정자의 issues가 배열이 아니다 (무시): %r", raw)
        return []

    codes: List[str] = []
    for item in raw:
        code = str(item).strip()
        if code in codes:
            continue
        if code not in EVAL_ISSUE_CODES:
            logger.warning("판정자가 모르는 issue 코드를 냈다 (버림): %r", item)
            continue
        codes.append(code)
    return codes


# ---------------------------------------------------------------------------
# 평가
# ---------------------------------------------------------------------------

def evaluate_answer(
    question: str,
    context_documents: List[str],
    generated_answer: str,
    model_name: str = "gpt-4o"
) -> AnswerEvaluation:
    """
    답변 1건을 채점한다. LLM 1회.

    question에는 검색에 실제로 쓴 정제된 질문(cleaned_query)을 넘긴다 —
    판정자가 보는 질문과 문서를 끌어온 질문이 달라지면 context_relevance가 뜻을 잃는다.

    context_documents는 **검색된 top_k 전체**다. 인용된 문서만 넘기면
    "엉뚱한 문서가 검색됐다"(retrieval_miss)를 영영 잡아낼 수 없다.
    """
    system_prompt, user_prompt = build_eval_prompts(
        question=question,
        context_documents=context_documents,
        generated_answer=generated_answer,
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,  # 일관된 평가를 위해 0으로 설정
        )
    except Exception as e:
        raise EvaluationError(f"평가 호출 실패: {e}") from e

    try:
        data = json.loads(response.choices[0].message.content or "")
    except (TypeError, json.JSONDecodeError) as e:
        raise EvaluationError(f"평가 응답을 JSON으로 해석할 수 없습니다: {e}") from e

    if not isinstance(data, dict):
        raise EvaluationError(f"평가 응답이 객체가 아닙니다: {type(data).__name__}")

    # 점수 키가 빠졌을 때 0.0으로 채우면 환각 판정과 구분되지 않으므로 필수로 요구한다
    for key in ("faithfulness", "answer_relevance", "context_relevance"):
        if key not in data:
            raise EvaluationError(f"평가 응답에 {key}가 없습니다")

    return AnswerEvaluation(
        faithfulness=_score(data["faithfulness"], "faithfulness"),
        answer_relevance=_score(data["answer_relevance"], "answer_relevance"),
        context_relevance=_score(data["context_relevance"], "context_relevance"),
        verdict=_verdict(data.get("verdict")),
        issues=_issues(data.get("issues")),
        reason=str(data.get("reason", "")).strip(),
    )
