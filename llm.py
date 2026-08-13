import os
from typing import List, Dict, Any, Optional, Iterator
from dotenv import load_dotenv
from openai import OpenAI

from dto import Answer, RetrievedChunk
from prompt import build_rag_prompts

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

    try:
        # 2. LLM 호출
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompts}
            ],
            temperature=temperature
        )

        answer = response.choices[0].message.content.strip()

        return Answer(
            message=answer,
            evaluation=None
        )

    except Exception as e:
        # 오류 발생 시 사용자 알림용 Fallback 메시지 반환
        return Answer(
            message=f"답변 생성 중 오류가 발생했습니다: {str(e)}",
            evaluation=None
        )