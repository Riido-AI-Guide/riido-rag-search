import os
import json
from typing import Dict, Any
from dotenv import load_dotenv
from openai import OpenAI

from dto import Query

def transform_user_query(
    raw_query: str,
    model_name: str = "gpt-4o-mini",
) -> Query:
    """ 사용자 질문을 분석하여 검색 필요 여부, 정제된 쿼리, 변형 쿼리를 반환하는 함수 """
    
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt = """
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

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": f"사용자 질문: {raw_query}"}
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
        # LLM 오류 발생 시
        return Query(
            raw_query=raw_query,
            cleaned_query=raw_query,
            search_queries=[raw_query],
            needs_search=True
        )