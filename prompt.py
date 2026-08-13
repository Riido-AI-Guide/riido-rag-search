import os
from typing import List, Dict, Any

from dto import RetrievedChunk

RAG_USER_PROMPT_TEMPLATE = """\
[참고 문서]
{context}

[사용자 질문]
{question}
"""


def _format_documents(documents: List[RetrievedChunk]) -> str:
    """ 검색된 여러 문서(List)를 단일 str로 합쳐 변환 """
    if not documents:
        return "참고할 수 있는 관련 문서가 없습니다."

    formatted_docs = []
    for idx, doc in enumerate(documents, start=1):
        formatted_docs.append(f"[문서 {idx}] (출처: {doc.section})\n{doc.content}")
    
    return "\n\n---\n\n".join(formatted_docs)


def build_rag_prompts(question: str, documents: List[RetrievedChunk]) -> str:
    """
    질문과 검색된 문서 리스트를 받아 LLM에 전달할 system, user 프롬프트를 만듭니다.
    """
    context_text = _format_documents(documents)
    return RAG_USER_PROMPT_TEMPLATE.format(
        context=context_text,
        question=question
    )
