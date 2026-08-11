import json
from llm import generate_rag_answer
from rag_A import search
from query_transform import transform_user_query



test_queries = [
    "팀원을 어떻게 추가해?",
    "워크스페이스의 멤버 목록을 확인하는 법 알려줘"
    # "작업 어케만듦?",
    # "안녕하세요, 오늘 날씨 어때요?",
    # "뤼이도에서 지원하는 API 문서 어디서 볼 수 있나요?",
    # "감사합니다!",
    # "뤼이도에서 작업 생성하는 방법 알려주세요."
]

for query in test_queries:
    # 사용자 질문 재생성
    print(f"\n[원본 질문] {query}")
    transformed_query = transform_user_query(query)
    print(f"[정제된 질문] {transformed_query['cleaned_query']}")
    print(f"[연관 검색어] {transformed_query['search_queries']}")
    
    # 답변할 수 없는 질문일 경우 검색을 하지 않음
    if not transformed_query["needs_search"]:
        print("답변할 수 없는 질문입니다. 검색을 수행하지 않습니다.")
    else:
        # 문서 검색
        searched_docs = search(transformed_query["cleaned_query"])["documents"]
        print(f"[검색된 문서 수] {len(searched_docs)}")
        print(f"[검색된 문서] {json.dumps(searched_docs, ensure_ascii=False, indent=2)}")
        
        answer = generate_rag_answer(transformed_query["cleaned_query"], searched_docs)
        print(f"[생성된 답변] {answer['answer']}")
    