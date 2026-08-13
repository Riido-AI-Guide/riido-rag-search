import json
from evaluator import evaluate_faithfulness
from llm import generate_rag_answer
from rag_A import search
from query_transform import transform_user_query



test_queries = [
    "팀원을 어떻게 추가해?"
    # "팀을 삭제하면 어떻게 돼?",
    # "스프린트 기간은 최대 몇 주까지 설정할 수 있어?",
    # "학생이면 뤼이도 유료 요금제 무료로 쓸 수 있어?",
    # "PR 연동 문제 해결하는 방법",
    # "회원 탈퇴 어떻게 해?",
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
        for i, d in enumerate(searched_docs, 1):
            print(f"  {i}. [{d['type']}] [{d['section']}])")
        
        # 답변 생성
        answer = generate_rag_answer(transformed_query["cleaned_query"], searched_docs)
        print(f"[생성된 답변] {answer['answer']}")
        
        # 답변 평가
        eval_result = evaluate_faithfulness(
            question=transformed_query["cleaned_query"],
            context_documents=[doc["content"] for doc in searched_docs],
            generated_answer=answer["answer"]
        )
        print(f"[평가 결과] {json.dumps(eval_result.__dict__, ensure_ascii=False, indent=2)}")
