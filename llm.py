import os
from typing import List, Dict, Any, Optional, Iterator
from dotenv import load_dotenv
from openai import OpenAI

from prompt import build_rag_prompts

def generate_rag_answer(
    question: str,
    documents: List[str],
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """
    질문과 검색된 문서들을 받아 LLM을 호출하고 합성 답변을 반환하는 메인 함수
    """
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 1. prompt.py의 메인 함수를 불러와 프롬프트 바인딩
    prompts = build_rag_prompts(question=question, documents=documents)

    try:
        # 2. LLM 호출
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": prompts["system"]},
                {"role": "user", "content": prompts["user"]}
            ],
            temperature=temperature
        )

        answer = response.choices[0].message.content.strip()

        return {
            "answer": answer,
            "used_documents_count": len(documents),
            "model": model_name
        }

    except Exception as e:
        # 오류 발생 시 사용자 알림용 Fallback 메시지 반환
        return {
            "answer": f"답변 생성 중 오류가 발생했습니다: {str(e)}",
            "used_documents_count": len(documents),
            "model": model_name
        }