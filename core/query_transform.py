import json
from typing import List, Optional
from openai import OpenAI

from core.config import OPENAI_API_KEY
from domain import ConversationTurn, Query

client = OpenAI(api_key=OPENAI_API_KEY)

_ANSWER_PREVIEW_CHARS = 150
_QUESTION_PREVIEW_CHARS = 300

# 대화 제목 길이 상한
_TITLE_MAX_CHARS = 30

# 제목을 못 만든 경우의 값
_DEFAULT_TITLE = "새 대화"


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """
당신은 RAG 시스템의 검색 쿼리 최적화 전문가입니다.
사용자의 질문을 분석하여 아래 규칙에 맞춰 JSON 형식으로만 응답하세요.

[규칙]
1. `needs_search`: 단순 인사/감사/잡담이면 false, 사내 지식이나 정보 검색이 필요하면 true.
2. `cleaned_query`: 인사말, 이메일 주소, 서명, 후속 대화("감사합니다" 등), 마스킹 토큰을 제거하되,
   키워드 나열로 바꾸지 말고 핵심 의도 하나를 담은 자연스러운 한국어 질문형 문장으로 정리.
   여러 주제가 섞여 있으면 가장 중심이 되는 질문 하나만 남기기.
   - 예: "작업 어케만듦?" -> "작업은 어떻게 만드나요?", "이거 설치하다 에러남 ㅠㅠ" -> "설치 중 오류가 나면 어떻게 해결하나요?"
3. `search_queries`: `needs_search`가 true인 경우, 검색 재현율을 높이기 위해 `cleaned_query`를 포함한 유용한 변형 검색어 2~3개 생성. (false인 경우 빈 배열)

[JSON 응답 형식]
{
  "needs_search": true,
  "cleaned_query": "간결하게 정제된 대표 쿼리",
  "search_queries": ["정제된 쿼리 1", "변형 쿼리 2", "변형 쿼리 3"]
}
"""

# 멀티턴일 때 대화 맥락 추가
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

# 첫 대화 시 제목 생성
_TITLE_SYSTEM_PROMPT = """
당신은 고객 지원 챗봇의 대화 목록에 붙일 제목을 만드는 편집자입니다.
사용자의 첫 질문을 읽고, 그 대화가 무엇에 대한 것인지 한눈에 알 수 있는 제목을 JSON으로만 응답하세요.

[규칙]
1. 한국어 명사구로, 공백 포함 20자 이내. 마침표·물음표·따옴표를 붙이지 마세요.
2. 질문의 핵심 대상과 행위를 남기세요. "질문", "문의", "관련 문의"처럼 내용이 없는 말은 쓰지 마세요.
   - "팀원을 어떻게 추가해?" -> "팀원 추가 방법"
   - "스프린트 기간 최대 몇 주까지 돼?" -> "스프린트 최대 기간"
   - "PR 연동이 자꾸 끊겨요" -> "PR 연동 오류"
3. 여러 주제가 섞여 있으면 가장 중심이 되는 것 하나만 담으세요.
4. 인사·잡담이라 주제라고 할 것이 없으면 "새 대화"로 하세요.

[JSON 응답 형식]
{"title": "팀원 추가 방법"}
"""


def _format_history(history: List[ConversationTurn]) -> str:
    """이전 대화를 턴 번호가 붙은 텍스트로 직렬화"""
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
    """(system, user) 반환"""
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
    """
    history = history or []

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
        # LLM 오류 발생 시 원문으로 검색
        return Query(
            raw_query=raw_query,
            cleaned_query=raw_query,
            search_queries=[raw_query],
            needs_search=True
        )


# ---------------------------------------------------------------------------
# 대화 제목
# ---------------------------------------------------------------------------

def _fallback_title(raw_query: str) -> str:
    """LLM 없이 만드는 제목. 원문을 줄여 쓴다"""
    text = " ".join((raw_query or "").split())
    if not text:
        return _DEFAULT_TITLE
    if len(text) <= _TITLE_MAX_CHARS:
        return text
    return text[: _TITLE_MAX_CHARS - 1] + "…"


def generate_conversation_title(raw_query: str, model_name: str = "gpt-4o-mini") -> str:
    """
    첫 질문으로 대화 목록에 걸 제목을 만든다. 첫 턴에서만 부른다.
    """
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _TITLE_SYSTEM_PROMPT.strip()},
                {"role": "user", "content": f"사용자 질문: {raw_query}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        title = json.loads(response.choices[0].message.content).get("title", "")
        title = " ".join(str(title).split()).strip("\"'“”‘’ ")
        return title[:_TITLE_MAX_CHARS] if title else _fallback_title(raw_query)

    except Exception:
        return _fallback_title(raw_query)
