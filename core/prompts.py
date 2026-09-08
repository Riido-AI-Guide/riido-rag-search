"""
core/prompts.py — 답변 생성 프롬프트 조립
"""

from typing import List, Tuple

from domain import RetrievedChunk


# ---------------------------------------------------------------------------
# 섹션 라벨
# ---------------------------------------------------------------------------

SECTION_LABELS = {
    "concept":      ["핵심답변", "개념설명", "특징", "관련정보"],
    "step":         ["핵심답변", "준비사항", "단계별방법", "완료결과"],
    "judgement":    ["핵심답변", "조건", "방법", "제한사항"],
    "troubleshoot": ["핵심답변", "상황확인", "원인", "해결방법", "해결확인"],
    "explore":      ["기능요약", "주요기능", "추천", "활용방법", "관련질문"],
    "no_answer":    ["안내"],
}

# 유형과 무관하게 어디서든 붙을 수 있는 라벨
COMMON_LABELS = ["참고", "주의사항"]

NO_ANSWER_TEXT = "제공된 문서에서 관련 정보를 찾을 수 없습니다."


def allowed_labels(answer_type: str):
    """해당 유형에서 허용되는 라벨 전체"""
    return SECTION_LABELS.get(answer_type, []) + COMMON_LABELS


RAG_SYSTEM_PROMPT = """\
당신은 뤼이도(riido) 이용 가이드를 바탕으로 답변하는 AI 챗봇입니다.
반드시 JSON으로만 응답하세요.

[작성 규칙]
1. [참고 문서] 내용만을 바탕으로 답변하세요. 추측하거나 지어내지 마세요.
2. 참고 문서에 질문과 관련된 내용이 하나도 없을 때만 answerType을 "no_answer"로 하세요.
   부분적으로라도 답이 있으면 그 범위 안에서 답하고, 문서에 없는 부분은 쓰지 않으면 됩니다.
   no_answer일 때 title은 빈 문자열, sections는
   [{"label":"안내","text":"제공된 문서에서 관련 정보를 찾을 수 없습니다.","sourceIds":[]}]
3. sourceIds에는 그 섹션의 근거가 된 [문서 N]의 N을 숫자로 넣으세요.
   그 섹션을 실제로 뒷받침하는 문서만 넣고, 없는 번호를 지어내지 마세요.

[섹션 구성]
질문 유형에 맞는 answerType을 하나 고르고, 그 유형의 섹션을 순서대로 채우세요.
label은 아래 표기를 그대로 쓰고, 새로 만들거나 바꾸지 마세요.

  concept       핵심답변 / 개념설명 / 특징 / 관련정보
  step          핵심답변 / 준비사항 / 단계별방법 / 완료결과
  judgement     핵심답변 / 조건 / 방법 / 제한사항
  troubleshoot  핵심답변 / 상황확인 / 원인 / 해결방법 / 해결확인
  explore       기능요약 / 주요기능 / 추천 / 활용방법 / 관련질문
  no_answer     안내

★ 참고 문서에 근거가 있는 섹션은 모두 채우세요. 핵심답변 하나만 쓰고 끝내지 마세요.
★ 다만 섹션을 채우는 것보다 정확한 것이 훨씬 중요합니다.
   - 참고 문서에 없는 내용은 절대 쓰지 마세요. 섹션을 비우고 생략하는 편이 낫습니다.
   - 특히 화면 이름, 버튼 이름, 메뉴 경로, 클릭 순서를 지어내지 마세요.
   - "단계별방법"은 참고 문서에 실제 절차가 문장으로 적혀 있을 때만 씁니다.
     문서가 "~할 수 있습니다" 수준으로만 언급하고 절차를 적지 않았다면 이 섹션은 생략하세요.
★ "참고"와 "주의사항"은 어느 유형에서든 필요하면 마지막에 덧붙일 수 있습니다.

[예시]
질문: "휴지통에서 삭제한 항목을 되돌릴 수 있나요?"
응답:
{
  "title": "휴지통 복구 방법",
  "answerType": "step",
  "sections": [
    {"label": "핵심답변",
     "text": "휴지통에서 복구 버튼을 누르면 원래 위치로 되돌아가며, 30일이 지나면 복원할 수 없습니다.",
     "sourceIds": [1]},
    {"label": "준비사항",
     "text": "복구는 삭제 후 30일 이내에만 가능합니다.",
     "sourceIds": [1]},
    {"label": "단계별방법",
     "text": "1. 사이드바에서 휴지통을 엽니다.\\n2. 복구할 항목을 선택합니다.\\n3. 복구 버튼을 누릅니다.",
     "sourceIds": [1, 3]},
    {"label": "완료결과",
     "text": "항목이 삭제 전 위치로 돌아가고 하위 항목도 함께 복구됩니다.",
     "sourceIds": [3]},
    {"label": "주의사항",
     "text": "영구 삭제 버튼을 누르거나 30일이 지나면 시스템에서도 복원할 수 없습니다.",
     "sourceIds": [1]}
  ]
}

[문장 쓰기]
- 각 섹션의 문장 길이는 위 예시 정도로 유지하세요. 한 섹션에 여러 내용을 욱여넣지 마세요.
- 섹션마다 sourceIds는 실제로 그 내용을 담은 문서만 넣습니다. 위 예시처럼 섹션마다 달라집니다.
- 볼드(**)나 이탤릭 같은 강조 표기는 쓰지 마세요.
- 문서에 있는 내용이라도 질문과 직접 관련이 없으면 넣지 마세요.
"""


RAG_USER_PROMPT_TEMPLATE = """\
[참고 문서]
{context}

[사용자 질문]
{question}
"""


def _format_documents(documents: List[RetrievedChunk]) -> str:
    """검색된 문서를 [문서 N] 형식으로 직렬화"""
    if not documents:
        return "참고할 수 있는 관련 문서가 없습니다."

    return "\n\n---\n\n".join(
        f"[문서 {idx}] (출처: {doc.section})\n{doc.content}"
        for idx, doc in enumerate(documents, start=1)
    )


def build_rag_prompts(question: str, documents: List[RetrievedChunk]) -> Tuple[str, str]:
    """(system 프롬프트, user 프롬프트) 반환"""
    user_prompt = RAG_USER_PROMPT_TEMPLATE.format(
        context=_format_documents(documents),
        question=question,
    )
    return RAG_SYSTEM_PROMPT, user_prompt


# ---------------------------------------------------------------------------
# 답변 평가 (LLM-as-a-Judge)
# ---------------------------------------------------------------------------

# 판정자가 낼 수 있는 문제 유형
#
# 사용자가 bad를 고를 때 쓰는 항목과 어휘를 맞추되, 정의역은 다르다.
#   - "오래된 정보"는 판정자가 낼 수 없다. 문서가 오래되었어도 해당 문서를 기준으로 판별하기 때문
#   - retrieval_miss는 사용자가 낼 수 없다. 사용자는 전체 근거 문서를 보지 않는다.
EVAL_ISSUE_CODES = ("factual_error", "insufficient", "irrelevant", "retrieval_miss")

# 종합 판정. 사용자 만족여부 예측
EVAL_VERDICTS = ("pass", "fail")


EVAL_SYSTEM_PROMPT = """\
당신은 RAG 시스템의 답변 환각(Hallucination)과 품질을 엄격하게 검수하는 전문 평가자입니다.
[참고 문서]만을 근거로 [생성된 답변]을 검증하고, 반드시 JSON으로만 응답하세요.

[점수]
1. faithfulness (0.0 ~ 1.0) — 충실도
   - 답변의 모든 주장과 사실이 [참고 문서]에 명확히 있으면 1.0.
   - 문서에 없는 내용을 지어내거나 추측했다면(환각) 0.0 ~ 0.5로 감점.
   - 특히 화면 이름, 버튼 이름, 메뉴 경로, 클릭 순서가 문서에 있는지 확인하세요.
2. answer_relevance (0.0 ~ 1.0) — 답변 관련성
   - 답변이 [사용자 질문]의 의도에 직결되면 1.0, 딴소리를 하면 감점.
3. context_relevance (0.0 ~ 1.0) — 문서 관련성
   - [참고 문서]가 [사용자 질문]과 관련 있으면 1.0, 엉뚱한 문서가 검색됐으면 감점.
   - 답변이 아니라 **검색 결과**를 채점하는 항목입니다. 인용되지 않은 문서까지 포함해 보세요.

[판정]
4. verdict — "pass" 또는 "fail" 중 하나.
   - 이 답변을 받은 사용자가 만족했을지로 판단하세요.
     점수가 조금 낮아도 실용적으로 쓸 만하면 "pass", 사실이 틀렸거나 질문에 답하지 못했으면 "fail".
5. issues — 아래 네 코드 중 해당하는 것만 배열로 담으세요.
   문제가 없으면 빈 배열이고, 둘 이상 해당하면 여러 개를 담습니다.
   - "factual_error"  : 답변에 [참고 문서]로 뒷받침되지 않는 사실·화면 이름·버튼 이름·절차가 있다
   - "insufficient"   : 틀리지는 않았지만, [참고 문서]에 있는데도 빠뜨린 핵심 내용이 있다
   - "irrelevant"     : 답변이 질문의 의도와 다른 것을 말한다
   - "retrieval_miss" : [참고 문서] 자체가 질문과 무관하다 (검색이 잘못 걸렸다)
   ★ 위 네 개 외의 코드를 만들지 마세요. 답변이 낡았는지는 문서만 보고 알 수 없으니 판정하지 마세요.
6. reason — 감점 사유와 환각으로 판단한 문장을 간결하게 쓰고,
   답변이 [참고 문서]의 어느 부분을 근거로 삼았는지 그 문장을 발췌해 밝히세요.

[답변이 "정보 없음"인 경우]
답변이 "제공된 문서에서 관련 정보를 찾을 수 없습니다"처럼 답을 거절한 것이면
지어낸 내용이 없으므로 faithfulness와 answer_relevance는 1.0으로 두고,
context_relevance로 검색 품질만 판정하세요. verdict는 "fail"입니다(사용자는 답을 받지 못했습니다).
- [참고 문서]에 답이 있었는데도 거절했다면 issues는 ["insufficient"]
- [참고 문서] 자체가 질문과 무관해 거절할 수밖에 없었다면 ["retrieval_miss"]

[JSON 응답 형식]
{
  "faithfulness": 1.0,
  "answer_relevance": 1.0,
  "context_relevance": 0.6,
  "verdict": "pass",
  "issues": [],
  "reason": "답변의 '팀 관리 메뉴'는 [참고 문서 2]의 '팀 설정 > 팀 관리에서 멤버를 초대합니다'에 근거가 있습니다. [참고 문서 3]은 휴지통 문서라 질문과 무관해 context_relevance를 감점했습니다."
}
"""


EVAL_USER_PROMPT_TEMPLATE = """\
[사용자 질문]
{question}

[참고 문서]
{context}

[생성된 답변]
{answer}
"""


def _format_context_documents(documents: List[str]) -> str:
    """
    검색된 문서 본문을 [참고 문서 N] 형식으로 직렬화.

    프롬프트가 "어느 문서에서 근거를 얻었는지" 번호로 답하라고 시키므로,
    번호 없이 이어붙이면 판정자가 지킬 수 없는 지시가 된다.
    """
    if not documents:
        return "참고 문서 없음"

    return "\n\n".join(
        f"[참고 문서 {idx}]\n{doc}"
        for idx, doc in enumerate(documents, start=1)
    )


def build_eval_prompts(
    question: str, context_documents: List[str], generated_answer: str
) -> Tuple[str, str]:
    """(system 프롬프트, user 프롬프트) 반환"""
    user_prompt = EVAL_USER_PROMPT_TEMPLATE.format(
        question=question,
        context=_format_context_documents(context_documents),
        answer=generated_answer,
    )
    return EVAL_SYSTEM_PROMPT, user_prompt


# ---------------------------------------------------------------------------
# 검색 문장 초안 (운영 콘솔)
#
# 검색 문장은 답변이 아니라 **검색에 걸리기 위한 미끼**다. 원문의 문장을 그대로 베끼면
# 임베딩이 원문과 겹쳐 쓸모가 줄고, 사용자가 실제로 치는 말과 멀어진다.
# ---------------------------------------------------------------------------

VIEW_SENTENCE_SYSTEM_PROMPT = """\
당신은 RAG 검색 인덱스를 만드는 전문가입니다.
주어진 뤼이도 이용 가이드 문서 하나를 읽고, 그 문서가 검색에 걸리도록 만드는 문장을
생성합니다. 반드시 JSON으로만 응답하세요.

[문장 유형]
- "hypo_q"     : 이 문서로 답할 수 있는 질문. 서로 다른 각도로 {hypo_count}개를 만드세요.
- "real_q"     : 사용자가 실제로 칠 법한 짧고 구어적인 질문 1개.
                 ("팀원 추가 어떻게 해?"처럼 짧게. 존댓말이 아니어도 됩니다)
- "contextual" : 이 문서가 무엇을 다루는지 한 문장으로 요약. 1개.
                 ("뤼이도 가이드의 ○○ 섹션. …을 안내한다" 형태)

[규칙]
1. 문서에 실제로 있는 내용만 쓰세요. 문서에 없는 기능·화면 이름을 지어내지 마세요.
2. 원문 문장을 그대로 복사하지 마세요. 검색어로 쓰일 말로 바꿔 쓰세요.
3. 질문은 40자 이내로 짧게. 한 문장에 한 가지만 물으세요.
4. 서로 거의 같은 질문을 여러 개 만들지 마세요. 각각 다른 것을 물어야 합니다.
5. 이미 등록된 문장이 주어지면 그것과 겹치지 않는 것만 만드세요.

[출력 형식]
{{"items": [{{"view_type": "hypo_q", "text": "..."}}, ...]}}
"""


def build_view_sentence_prompts(
    section: str,
    content: str,
    hypo_count: int = 3,
    existing: List[str] = None,
) -> Tuple[str, str]:
    """(system, user). 원문과 이미 등록된 문장을 함께 준다."""
    system = VIEW_SENTENCE_SYSTEM_PROMPT.format(hypo_count=hypo_count)

    parts = [f"[문서 위치]\n{section}", f"\n[문서 원문]\n{content}"]
    if existing:
        parts.append("\n[이미 등록된 문장 — 겹치지 않게 하세요]\n" +
                     "\n".join(f"- {t}" for t in existing))
    return system, "\n".join(parts)
