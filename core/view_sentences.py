"""
core/view_sentences.py — 검색 문장 초안 생성

운영 콘솔이 부른다. 문서 원문을 주면 가설질문·실제질문·맥락요약 초안을 만들어
돌려주기만 하고, **저장은 하지 않는다** — 사람이 고쳐 쓸 것을 전제로 한다.

빈 칸에서 시작하면 아무도 채우지 않는다. 초안을 채워두고 고치게 하는 것이 목적이다.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from core.config import OPENAI_API_KEY
from core.generation import LlmError
from core.prompts import build_view_sentence_prompts
from domain import VIEW_TYPES

logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

# 원문이 길면 앞부분만 준다. 문장은 문서의 주제에서 나오므로 뒷부분까지 다 넣을 이유가 적다.
DRAFT_CONTENT_CHARS = 6000

# 한 번에 만들 문장 수 상한. 사람이 검토할 수 있는 양을 넘기면 그냥 안 읽는다.
MAX_DRAFT_ITEMS = 12


def _parse_items(raw: str) -> List[Dict[str, str]]:
    """
    {"items": [{"view_type", "text"}]}만 받는다. 모르는 유형과 빈 문장은 버린다 —
    초안은 사람이 보고 고르는 것이라, 조금 적게 나오는 편이 이상한 게 섞이는 것보다 낫다.
    """
    try:
        data: Dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LlmError(f"초안 생성 응답이 JSON이 아닙니다: {e}") from e

    items = data.get("items")
    if not isinstance(items, list):
        raise LlmError("초안 생성 응답에 items 배열이 없습니다.")

    parsed: List[Dict[str, str]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        view_type = str(item.get("view_type", "")).strip()
        text = str(item.get("text", "")).strip()
        if not text or view_type not in VIEW_TYPES:
            logger.info("초안에서 버린 항목: %r", item)
            continue
        if text in seen:  # 같은 문장을 두 번 내는 경우가 있다
            continue
        seen.add(text)
        parsed.append({"view_type": view_type, "text": text})

    if not parsed:
        raise LlmError("쓸 수 있는 초안이 하나도 나오지 않았습니다.")
    return parsed[:MAX_DRAFT_ITEMS]


def draft_view_sentences(
    section: str,
    content: str,
    hypo_count: int = 3,
    existing: Optional[List[str]] = None,
    model_name: str = "gpt-4o",
    temperature: float = 0.7,
) -> List[Dict[str, str]]:
    """
    [{view_type, text}] 초안. 실패하면 LlmError를 올린다(저장할 것이 없으므로 부분 성공이 없다).

    temperature가 답변 생성(0.0)보다 높다 — 초안은 정답 하나를 맞히는 일이 아니라
    서로 다른 각도의 질문을 여러 개 뽑는 일이고, 0.0이면 비슷한 문장만 나온다.
    """
    system_prompt, user_prompt = build_view_sentence_prompts(
        section=section,
        content=content[:DRAFT_CONTENT_CHARS],
        hypo_count=hypo_count,
        existing=existing,
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
    except Exception as e:
        raise LlmError(f"검색 문장 초안 생성 실패: {e}") from e

    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        raise LlmError("LLM이 빈 초안을 반환했습니다.")

    return _parse_items(raw)
