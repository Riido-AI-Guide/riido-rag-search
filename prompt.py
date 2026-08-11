import os
from typing import List, Dict, Any

from dotenv import load_dotenv
from openai import OpenAI

# RAG 기본 시스템 프롬프트 템플릿
RAG_SYSTEM_PROMPT = """\
당신은 뤼이도(riido)의 이용 가이드를 바탕으로 답변하는 친절하고 정확한 AI 비서입니다.

[작성 규칙]
1. 아래 제시된 [참고 문서(Context)] 내용만을 바탕으로 질문에 답변하세요.
2. 문서 내용에 나와 있지 않거나 모르는 내용이라면, 지어내지 말고 "제공된 문서에서 해당 내용을 찾을 수 없습니다."라고 명확히 답변하세요.
3. 답변은 읽기 편하도록 간결한 문단과 불릿 포인트(-)를 활용해 작성하세요.
"""

RAG_USER_PROMPT_TEMPLATE = """\
[참고 문서(Context)]
{context}

[사용자 질문]
{question}
"""


def _format_documents(documents: List[str]) -> str:
    """ 검색된 여러 문서(List)를 단일 str로 합쳐 변환 """
    if not documents:
        return "참고할 수 있는 관련 문서가 없습니다."

    formatted_docs = []
    for idx, doc in enumerate(documents, start=1):
        formatted_docs.append(f"[문서 {idx}] (출처: {doc['section']})\n{doc['content']}")
    
    return "\n\n---\n\n".join(formatted_docs)


def build_rag_prompts(question: str, documents: List[str]) -> Dict[str, str]:
    """
    질문과 검색된 문서 리스트를 받아 LLM에 전달할 system, user 프롬프트를 만듭니다.
    """
    context_text = _format_documents(documents)
    user_prompt = RAG_USER_PROMPT_TEMPLATE.format(
        context=context_text,
        question=question
    )

    return {
        "system": RAG_SYSTEM_PROMPT,
        "user": user_prompt
    }
