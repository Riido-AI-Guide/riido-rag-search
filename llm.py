"""
llm.py — 답변 생성

검색된 문서를 근거로 구조화된 답변(title + answerType + sections)을 만든다.
- response_format으로 JSON 출력을 강제한다 (프롬프트 지시만으로는 형식이 흔들린다)
- LLM이 준 [문서 N] 번호를 doc_id/section으로 역매핑해 섹션마다 근거를 붙인다
- 파싱이 실패해도 예외를 위로 던지지 않고 원문을 담은 Answer를 돌려준다
"""

import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI

from dto import Answer, AnswerSection, RetrievedChunk, SourceRef
from prompt import NO_ANSWER_TEXT, SECTION_LABELS, allowed_labels, build_rag_prompts

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class LlmError(RuntimeError):
    """
    답변 생성 실패.

    실패를 정상 답변 문자열로 감싸 반환하면 호출자가 확인을 잊었을 때
    오류 메시지가 그대로 사용자에게 나간다. 예외로 올려 호출자가
    상태 코드·재시도 정책을 결정하게 한다.
    """


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------

def _resolve_sources(source_ids: Any, documents: List[RetrievedChunk]) -> List[SourceRef]:
    """LLM이 준 [문서 N] 번호를 실제 문서로 바꾼다. 범위 밖 번호는 버린다."""
    if not isinstance(source_ids, list):
        return []

    refs: List[SourceRef] = []
    seen = set()
    for n in source_ids:
        if isinstance(n, str) and n.strip().isdigit():   # "1" 또는 "문서 1" 대비
            n = int(n.strip())
        elif isinstance(n, str):
            digits = "".join(c for c in n if c.isdigit())
            n = int(digits) if digits else None

        if not isinstance(n, int) or not (1 <= n <= len(documents)):
            continue

        doc = documents[n - 1]
        if doc.doc_id in seen:
            continue
        seen.add(doc.doc_id)
        refs.append(SourceRef(doc_id=doc.doc_id, section=doc.section))

    return refs


def _parse_answer(raw: str, documents: List[RetrievedChunk]) -> Answer:
    try:
        data: Dict[str, Any] = json.loads(raw)
        raw_sections = data.get("sections") or []
        if not isinstance(raw_sections, list):
            raise ValueError("sections가 배열이 아님")

        sections = [
            AnswerSection(
                label=str(s.get("label", "")).strip(),
                text=str(s.get("text", "")).strip(),
                sources=_resolve_sources(s.get("sourceIds"), documents),
            )
            for s in raw_sections
            if isinstance(s, dict) and str(s.get("text", "")).strip()
        ]

        # 유형 검증 — 목록에 없는 값은 기본값으로 흡수한다.
        # (프론트가 answer_type으로 레이아웃을 고르므로 모르는 값이 나가면 안 된다)
        answer_type = str(data.get("answerType", "concept")).strip()
        if answer_type not in SECTION_LABELS:
            answer_type = "concept"

        # 라벨 검증 — 그 유형에서 허용되지 않는 라벨의 섹션은 버린다.
        # (프론트가 label로 스타일을 정하므로 모르는 라벨은 스타일이 안 붙는다)
        ok_labels = set(allowed_labels(answer_type))
        sections = [s for s in sections if s.label in ok_labels]

        # 파싱에 성공했으면 raw는 담지 않는다 (sections와 같은 내용이라 중복).
        # 실패 경로에서만 원문을 살린다.
        return Answer(
            title=str(data.get("title", "")).strip(),
            answer_type=answer_type,
            sections=sections,
        )

    except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
        # 형식이 깨져도 내용은 살려서 돌려준다 (화면엔 통짜 텍스트로 표시)
        return Answer(title="", answer_type="parse_error", sections=[], raw=raw)


# ---------------------------------------------------------------------------
# 생성
# ---------------------------------------------------------------------------

def generate_rag_answer(
    question: str,
    documents: List[RetrievedChunk],
    model_name: str = "gpt-4o",
    temperature: float = 0.0,
) -> Answer:
    """질문과 검색된 문서를 받아 구조화된 답변을 만든다."""
    # 근거가 하나도 없으면 LLM을 부르지 않는다.
    # 호출해봐야 거절 문구가 나올 뿐이고, 근거 0개는 환각 확률이 제일 높은 구간이다.
    if not documents:
        return Answer(
            title="",
            answer_type="no_answer",
            sections=[AnswerSection(label="안내", text=NO_ANSWER_TEXT, sources=[])],
        )

    system_prompt, user_prompt = build_rag_prompts(question=question, documents=documents)

    # 2. LLM 호출
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},   # 형식 강제
            temperature=temperature,
        )
    except Exception as e:
        raise LlmError(f"답변 생성 실패: {e}") from e

    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        raise LlmError("LLM이 빈 답변을 반환했습니다.")

    return _parse_answer(raw, documents)