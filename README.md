# Riido RAG 챗봇

## 백로그

- **`SearchChunk.text_tsv` 이름 정정** — [dto/SearchChunk.py](dto/SearchChunk.py)의 이 필드는
  `TSVECTOR`가 아니라 Kiwi가 뽑은 키워드 문자열을 담는다(실제 `to_tsvector()`는 SQL에서 실행).
  `text_keywords`가 맞다. [search_builder.py](search_builder.py)의 대입부도 함께 고쳐야 한다.
- **`QnA` 저장 기능** — [dto/QnA.py](dto/QnA.py)는 질문·답변 로그를 DB에 남기려고 만들었지만
  아직 아무 데서도 쓰지 않는다. 설계 방향은 정해졌다:

  - 평가 점수가 낮으면 답변을 다시 생성하므로, **질문 1개에 답변 시도 N개**가 달린다.
  - 근거 문서는 시도마다 달라지므로 `QnA`가 아니라 **각 시도**가 들고 있어야 한다.
    (현재 `QnA.documents`에 있는 건 잘못된 위치)
  - 따라서 `AnswerAttempt(검색어, 문서, 답변, 평가)`를 만들고 `QnA`는
    `query` + `attempts: List[AnswerAttempt]` + `is_good` + `final_attempt`를 갖는다.
  - `Answer.evaluation`은 이때 `AnswerAttempt.evaluation`으로 옮긴다
    (`generate_rag_answer()`가 평가를 만들 수 없으므로 `Answer`에 두면 항상 `None`).
  - 재시도가 **무엇을 바꾸는지**(검색어 변형 / top_k / temperature) 먼저 정해야
    로그 스키마의 컬럼이 의미를 갖는다.
  - 로그의 `doc_id`에는 FK를 걸지 않는다. [answer_builder.py](answer_builder.py)가
    사라진 문서를 지울 때 과거 로그까지 CASCADE로 삭제된다.
- **`search_queries` 활용** — [query_transform.py](query_transform.py)가 변형 검색어를 2~3개
  만들지만 검색에는 `cleaned_query` 하나만 쓴다. 멀티쿼리 검색 도입 여부 미정.
- **`rag_view_sentences.json` 생성 스크립트** — 외부에서 만들어 커밋한 파일이라
  저장소에 재생성 경로가 없다.
