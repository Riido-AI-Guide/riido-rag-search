import os
import json
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

from dto import AnswerEvaluation


class EvaluationError(RuntimeError):
    """
    평가 실패.

    실패 시 0.0을 반환하면 이 시스템에서 "완전한 환각" 판정과 값이 같아진다.
    평가가 죽은 것과 답변이 나쁜 것을 구분하려면 예외로 올려야 한다.
    """


def evaluate_faithfulness(
    question: str,
    context_documents: List[str],
    generated_answer: str,
    model_name: str = "gpt-4o"
) -> AnswerEvaluation:
    """
    LLM-as-a-Judge 기법을 사용하여 생성된 답변이 검색된 문서에만 근거하는지(환각 여부) 검증합니다.
    """
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    context_text = "\n---\n".join(context_documents) if context_documents else "참고 문서 없음"

    EVAL_SYSTEM_PROMPT = """
당신은 RAG 시스템의 답변 환각(Hallucination) 및 품질을 엄격하게 검수하는 전문 평가자입니다.

주어진 [참고 문서]만을 바탕으로 [생성된 답변]을 검증하여 결과를 반드시 JSON 형식으로 출력하세요.

[검증 기준]
1. `faithfulness` (0.0 ~ 1.0): 
   - [생성된 답변]의 모든 주장과 사실이 [참고 문서]에 명확히 언급되어 있으면 1.0.
   - [참고 문서]에 없는 내용을 지어내거나 추측해서 썼다면(환각 현상) 0.0 ~ 0.5로 감점.
2. `answer_relevance` (0.0 ~ 1.0):
   - [생성된 답변]이 [사용자 질문]의 의도에 직결되는 답변이면 1.0, 딴소리면 감점.
3. `context_relevance` (0.0 ~ 1.0):
   - [참고 문서]가 [사용자 질문]과 관련성이 있으면 1.0, 그렇지 않으면 감점.
4. `reason`:
   - 감점 요소 및 환각으로 판단된 문장을 간결하게 설명.
   - [생성된 답변]이 [참고 문서]의 어느 부분으로부터 근거를 얻었는지 해당 문장 명시적으로 발췌.

[JSON 응답 형식]
{{
  "faithfulness": 1.0,
  "answer_relevance": 1.0,
  "context_relevance": 1.0,
  "reason": "답변의 '팀 관리 메뉴'는 [참고 문서 2]의 '팀 관리' 섹션에서 근거를 찾을 수 있습니다. '이메일 주소' 관련 내용은 [참고 문서 1]에 명시되어 있습니다."
}}
"""
    
    EVAL_USER_PROMPT = f"""
[데이터]
- 사용자 질문: {question}
- 참고 문서:
{context_text}
- 생성된 답변: {generated_answer}
"""

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": EVAL_USER_PROMPT.strip()}
            ],
            response_format={"type": "json_object"},
            temperature=0.0  # 일관된 평가를 위해 0으로 설정
        )
    except Exception as e:
        raise EvaluationError(f"평가 호출 실패: {e}") from e

    try:
        res_json = json.loads(response.choices[0].message.content or "")
    except (TypeError, json.JSONDecodeError) as e:
        raise EvaluationError(f"평가 응답을 JSON으로 해석할 수 없습니다: {e}") from e

    # 점수 키가 빠졌을 때 0.0으로 채우면 환각 판정과 구분되지 않으므로 필수로 요구한다
    try:
        return AnswerEvaluation(
            faithfulness=float(res_json["faithfulness"]),
            answer_relevance=float(res_json["answer_relevance"]),
            context_relevance=float(res_json["context_relevance"]),
            reason=res_json.get("reason", "평가 완료")
        )
    except (KeyError, TypeError, ValueError) as e:
        raise EvaluationError(f"평가 응답 형식이 올바르지 않습니다: {e}") from e