from core.evaluation import evaluate_answer
from core.generation import generate_rag_answer
from core.query_transform import transform_user_query
from core.search import search

test_queries = [
    "팀원을 어떻게 추가해?",
    "팀을 삭제하면 어떻게 돼?",
    "스프린트 기간은 최대 몇 주까지 설정할 수 있어?",
    "학생이면 뤼이도 유료 요금제 무료로 쓸 수 있어?",
    "PR 연동 문제 해결하는 방법",
    "회원 탈퇴 어떻게 해?",
]

for query in test_queries:
    # 사용자 질문 재생성
    print(f"\n{'=' * 70}")
    print(f"[원본 질문] {query}")
    transformed_query = transform_user_query(query)
    print(f"[정제된 질문] {transformed_query.cleaned_query}")
    print(f"[연관 검색어] {transformed_query.search_queries}")

    # 답변할 수 없는 질문일 경우 검색을 하지 않음
    if not transformed_query.needs_search:
        print("답변할 수 없는 질문입니다. 검색을 수행하지 않습니다.")
        continue

    # 문서 검색 (검색 단위 top-k → 해당 doc_id의 답변 문서)
    searched_hits, searched_docs = search(transformed_query.cleaned_query)
    print(f"[검색된 문장 수] {len(searched_hits)}")
    for i, h in enumerate(searched_hits, 1):
        print(f"  {i}. [{h.view_type}] {h.doc_id}")
        print(f"     {h.text}")

    print(f"[검색된 문서 수] {len(searched_docs)}")
    for i, d in enumerate(searched_docs, 1):
        print(f"  [문서 {i}] [{d.source_type}] [{d.section}]")

    # 답변 생성
    answer = generate_rag_answer(transformed_query.cleaned_query, searched_docs)

    print(f"\n[답변] {answer.title or '(제목 없음)'}  ·  {answer.answer_type}")
    if answer.answer_type == "parse_error":
        print(f"  ⚠ 형식 깨짐 — 라벨 없는 섹션 하나에 원문이 담긴다")
    for s in answer.sections:
        print(f"\n  ┌ {s.label}")
        for line in s.text.split("\n"):
            print(f"  │ {line}")
        srcs = " / ".join(x.section for x in s.sources) or "(없음)"
        print(f"  └ 근거: {srcs}")

    # 답변 평가 — 실제로 답한 경우에만.
    # 거절하거나 파싱이 깨진 답변을 채점하면 점수가 왜곡된다
    # (거절은 지어낸 게 없어 faithfulness가 1.0으로, parse_error는 JSON 원문이 답변으로 들어간다)
    if answer.is_answered:
        eval_result = evaluate_answer(
            question=transformed_query.cleaned_query,
            context_documents=[doc.content for doc in searched_docs],
            generated_answer=answer.message
        )
        print(f"\n[평가 결과] {eval_result}")
    else:
        print(f"\n[평가 결과] 생략 (answer_type={answer.answer_type})")
