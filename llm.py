import os
from typing import List, Dict, Any, Optional, Iterator
from dotenv import load_dotenv
from openai import OpenAI

from dto import Answer, RetrievedChunk
from prompt import build_rag_prompts


class LlmError(RuntimeError):
    """
    답변 생성 실패.

    실패를 정상 답변 문자열로 감싸 반환하면 호출자가 확인을 잊었을 때
    오류 메시지가 그대로 사용자에게 나간다. 예외로 올려 호출자가
    상태 코드·재시도 정책을 결정하게 한다.
    """

RAG_SYSTEM_PROMPT = """\
당신은 뤼이도(riido)의 이용 가이드를 바탕으로 답변하는 친절하고 정확한 AI 비서입니다.

[작성 규칙]
1. 반드시 아래 제시된 [참고 문서] 내용만을 바탕으로 질문에 답변하세요.
2. [참고 문서]만으로 질문에 답변할 수 없다면, 절대로 추측하거나 지어내지 말고 정확히 아래 문장만 출력하세요:
  "제공된 문서에서 관련 정보를 찾을 수 없습니다."
3. 답변은 읽기 편하도록 간결한 문단과 불릿 포인트(-)를 활용해 작성하세요.
"""

def generate_rag_answer(
    question: str,
    documents: List[RetrievedChunk],
    model_name: str = "gpt-4o",
    temperature: float = 0.2,
) -> Answer:
    """
    질문과 검색된 문서들을 받아 LLM을 호출하고 합성 답변을 반환하는 메인 함수
    """
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 1. prompt.py의 메인 함수를 불러와 프롬프트 바인딩
    user_prompts = build_rag_prompts(question=question, documents=documents)

    # 2. LLM 호출
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompts}
            ],
            temperature=temperature
        )
    except Exception as e:
        raise LlmError(f"답변 생성 실패: {e}") from e

    message = (response.choices[0].message.content or "").strip()
    if not message:
        raise LlmError("LLM이 빈 답변을 반환했습니다.")

    return Answer(
        message=message,
        evaluation=None
    )