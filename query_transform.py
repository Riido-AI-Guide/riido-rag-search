import os
import json
from typing import List, Optional
from dotenv import load_dotenv
from openai import OpenAI

from dto import ConversationTurn, Query

# 이전 답변은 "무슨 얘기였는지"를 알려주는 용도라 앞부분만 있으면 충분하다.
# 전문을 넣으면 재작성 한 번에 답변 N개가 통째로 들어가 비용이 턴 수에 비례해 늘고,
# 정작 중요한 마지막 질문이 긴 답변들에 묻힌다.
_ANSWER_PREVIEW_CHARS = 150
_QUESTION_PREVIEW_CHARS = 300


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """
당신은 RAG 시스템의 검색 쿼리 최적화 전문가입니다.
사용자의 질문을 분석하여 아래 규칙에 맞춰 JSON 형식으로만 응답하세요.

[규칙]
1. `needs_search`: 단순 인사/감사/잡담이면 false, 사내 지식이나 정보 검색이 필요하면 true.
2. `cleaned_query`: 구어체, 모호한 표현, 줄임말을 간결하고 명확한 표준 검색어(키워드/명사 위주)로 정리.
   - 예: "작업 어케만듦?" -> "작업 생성 방법", "이거 설치하다 에러남 ㅠㅠ" -> "설치 오류 원인 및 해결 방법"
3. `search_queries`: `needs_search`가 true인 경우, 검색 재현율을 높이기 위해 `cleaned_query`를 포함한 유용한 변형 검색어 2~3개 생성. (false인 경우 빈 배열)

[JSON 응답 형식]
{
  "needs_search": true,
  "cleaned_query": "간결하게 정제된 대표 쿼리",
  "search_queries": ["정제된 쿼리 1", "변형 쿼리 2", "변형 쿼리 3"]
}
"""

# 이전 대화가 있을 때만 덧붙인다.
# 히스토리가 없는 요청의 프롬프트는 예전과 100% 같아야 한다 —
# golden_set.json 기준선이 단일턴으로 잡혀 있어서, 맥락 규칙을 항상 붙이면
# 첫 질문의 재작성 결과까지 같이 흔들린다.
_CONTEXT_SYSTEM_PROMPT = """

[대화 맥락 처리]
아래에 [이전 대화]가 주어집니다. 마지막 질문이 그 대화에 이어지는 후속 질문일 수 있습니다.

4. `cleaned_query`는 반드시 **그 문장만 읽어도 뜻이 통하도록** 만드세요.
   검색 엔진은 이전 대화를 볼 수 없으므로, 대명사와 생략된 주어를 이전 대화에서 찾아 채워야 합니다.
   - 이전 대화가 "작업 생성 방법"이었고 이번 질문이 "그럼 그거 삭제는?" -> "작업 삭제 방법"
   - 이전 대화가 "스프린트 기간 설정"이었고 이번 질문이 "최대 몇 주까지 돼?" -> "스프린트 기간 최대 설정 값"
5. 이번 질문이 이전 대화와 상관없는 새 주제라면 이전 대화를 무시하고 그 질문만으로 정리하세요.
   맥락을 억지로 끌어와 없는 주제를 만들어 붙이지 마세요.
6. `needs_search` 판단에도 맥락을 반영하세요.
   "응 알려줘", "더 자세히", "그럼 두 번째 방법은?"처럼 그 문장만 보면 잡담 같아도,
   이전 대화에 이어 정보를 더 요구하는 것이면 true입니다.
   맥락과 무관한 순수한 인사/감사("고마워", "잘 되네요")만 false입니다.
"""


def _format_history(history: List[ConversationTurn]) -> str:
    """이전 대화를 턴 번호가 붙은 텍스트로 직렬화한다."""
    lines = []
    for idx, turn in enumerate(history, start=1):
        question = turn.question.strip()[:_QUESTION_PREVIEW_CHARS]
        lines.append(f"{idx}. 사용자: {question}")

        answer = (turn.answer or "").strip().replace("\n", " ")
        if answer:
            preview = answer[:_ANSWER_PREVIEW_CHARS]
            if len(answer) > _ANSWER_PREVIEW_CHARS:
                preview += "..."
            lines.append(f"   챗봇: {preview}")
    return "\n".join(lines)


def _build_prompts(raw_query: str, history: List[ConversationTurn]):
    """(system, user) 반환. history가 비면 예전 프롬프트와 완전히 동일하다."""
    if not history:
        return _BASE_SYSTEM_PROMPT.strip(), f"사용자 질문: {raw_query}"

    system_prompt = (_BASE_SYSTEM_PROMPT + _CONTEXT_SYSTEM_PROMPT).strip()
    user_prompt = (
        f"[이전 대화]\n{_format_history(history)}\n\n"
        f"사용자 질문: {raw_query}"
    )
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# 변환
# ---------------------------------------------------------------------------

def transform_user_query(
    raw_query: str,
    history: Optional[List[ConversationTurn]] = None,
    model_name: str = "gpt-4o-mini",
) -> Query:
    """
    사용자 질문을 분석하여 검색 필요 여부, 정제된 쿼리, 변형 쿼리를 반환하는 함수.

    history를 주면 후속 질문의 대명사·생략을 앞 턴에서 풀어 독립적인 검색어로 만든다.
    호출자가 넘길 턴 수를 이미 잘라서 준다고 가정한다(여기서 다시 자르지 않는다).
    """
    history = history or []

    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt, user_prompt = _build_prompts(raw_query, history)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}, # JSON 응답 강제
            temperature=0.1
        )

        res_json = json.loads(response.choices[0].message.content)
        return Query(
            raw_query= raw_query,
            cleaned_query=res_json.get("cleaned_query", raw_query),
            search_queries=res_json.get("search_queries", [raw_query]),
            needs_search=res_json.get("needs_search", True)
        )

    except Exception:
        # LLM 오류 발생 시.
        # 맥락 해소에 실패했으므로 후속 질문이면 검색이 빗나갈 수 있지만,
        # 원문으로라도 검색하는 편이 요청을 통째로 실패시키는 것보다 낫다.
        return Query(
            raw_query=raw_query,
            cleaned_query=raw_query,
            search_queries=[raw_query],
            needs_search=True
        )
