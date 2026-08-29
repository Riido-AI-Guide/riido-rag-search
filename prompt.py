"""
prompt.py — 답변 생성 프롬프트 조립

system 프롬프트와 user 프롬프트를 모두 여기서 만든다.
(예전에는 system이 llm.py에 상수로 박혀 있어 프롬프트가 두 파일에 흩어져 있었다)
"""

from typing import List, Tuple

from domain import RetrievedChunk


# ---------------------------------------------------------------------------
# 섹션 라벨 — 프론트가 이 값으로 스타일을 정하므로 고정한다
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
    """검색된 문서를 [문서 N] 형식으로 직렬화. N이 곧 sourceIds의 값이 된다."""
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
