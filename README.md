# Riido RAG 챗봇

뤼이도 이용 가이드를 근거로 답하는 RAG 검색·답변 서비스.

## 폴더 구조

```
api/        HTTP 계층 — FastAPI 앱, 라우터, 스키마, 서비스, 리포지토리
core/       RAG 코어 — 검색·질문 전처리·답변 생성·평가·DB 풀. HTTP를 모른다
domain/     도메인 dataclass — SearchHit, RetrievedChunk, Query, Answer …
scripts/    오프라인 CLI — 인덱스 빌드, 골든셋 만들기, 검색 평가
data/       입력·산출 데이터 (json, csv)
```

의존 방향은 한쪽으로만 흐른다. **`api/` → `core/` → `domain/`**, `scripts/`는 `core/`와 `domain/`을
쓰고, **아무도 `api/`를 import하지 않는다.** 코어를 스크립트에서도 쓸 수 있는 건 이 방향 덕이다.

서비스 계층(`api/services/`)은 **엮을 게 있을 때만** 둔다. `rag_service.ask()`는 히스토리 자르기,
첫 턴 판정, 전처리→검색→생성 순서를 관리하므로 존재하고, 목록 조회 라우터는 리포지토리를
직접 부른다. 대칭을 맞추려고 통과 전용 서비스를 만들지 않는다.

## 실행

```bash
pip install -r requirements.txt

python -m scripts.build_answer_units        # answer_units 적재 (먼저)
python -m scripts.build_search_units        # search_units 적재

uvicorn api.main:app --reload               # 저장소 루트에서
```

Swagger: http://127.0.0.1:8000/docs · API 상세는 [api/README.md](api/README.md)

스크립트는 `python -m` 형태로 돌린다. 저장소 루트가 `sys.path`에 올라야 `core`·`domain`
import가 풀린다.

```bash
python -m scripts.build_golden_set propose   # 검색 평가용 골든셋 (propose → finalize → synthesize)
python -m scripts.evaluate_search            # Hit@1 / Hit@3 / MRR 채점
```

## 백로그

- **`SearchChunk.text_tsv` 이름 정정** — [domain/search_chunk.py](domain/search_chunk.py)의 이 필드는
  `TSVECTOR`가 아니라 Kiwi가 뽑은 키워드 문자열을 담는다(실제 `to_tsvector()`는 SQL에서 실행).
  `text_keywords`가 맞다. [scripts/build_search_units.py](scripts/build_search_units.py)의 대입부도
  함께 고쳐야 한다.
- **`QnA` 저장 기능** — [domain/qna.py](domain/qna.py)는 질문·답변 로그를 DB에 남기려고 만들었지만
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
  - 로그의 `doc_id`에는 FK를 걸지 않는다. [scripts/build_answer_units.py](scripts/build_answer_units.py)가
    사라진 문서를 지울 때 과거 로그까지 CASCADE로 삭제된다.
- **import 시점 부수효과** — [core/search.py](core/search.py)가 모듈 로드 때 `Kiwi()`와
  `OpenAIEmbeddings()`를 만든다. 그래서 부팅 때 `warmup()`이 필요하고, 이 모듈을 import하는
  테스트는 무조건 수 초를 기다린다. 지연 생성으로 바꾸면 `warmup()` 본문이 실제 준비를 맡는다.
- **`search_queries` 활용** — [core/query_transform.py](core/query_transform.py)가 변형 검색어를 2~3개
  만들지만 검색에는 `cleaned_query` 하나만 쓴다. 멀티쿼리 검색 도입 여부 미정.
- **`rag_view_sentences.json` 생성 스크립트** — 외부에서 만들어 커밋한 파일이라
  저장소에 재생성 경로가 없다. ([data/rag_view_sentences.json](data/rag_view_sentences.json))
- **테스트 부재** — [test_rag.py](test_rag.py)는 이름과 달리 pytest 테스트가 아니라 눈으로 확인하는
  수동 스모크 스크립트다. `rag_service.ask()`의 분기(첫 턴/후속 턴, `needs_search`)는 LLM을
  스텁으로 갈아끼우면 값싸게 테스트할 수 있다.
