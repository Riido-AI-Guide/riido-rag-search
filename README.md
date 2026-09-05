# Riido RAG 챗봇

뤼이도 이용 가이드를 근거로 답하는 RAG 검색·답변 서비스.

## 폴더 구조

```
api/        HTTP 계층 — FastAPI 앱, 라우터, 스키마, 서비스, 리포지토리
core/       RAG 코어 — 검색·질문 전처리·답변 생성·평가·DB 풀·환경설정. HTTP를 모른다
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
cp .env.example .env                        # DATABASE_URL·OPENAI_API_KEY 채우기
pip install -r requirements.txt

python -m scripts.build_answer_units        # answer_units 적재 (먼저)
python -m scripts.build_search_units        # search_units 적재
python -m scripts.build_qna_logs            # 질의응답 로그·평가 테이블 (인덱스와 무관, 순서 상관없음)

uvicorn api.main:app --reload               # 저장소 루트에서
```

개발자마다 DB가 따로라 별도 마이그레이션 절차를 두지 않는다. **빌드 스크립트가 스키마를
맞춘다** — 이미 데이터가 있는 DB에서 `build_answer_units`를 돌리면 없는 컬럼을 만들고
빈 값만 채운다. 본문 해시가 그대로면 재임베딩하지 않으므로(`유지` / `링크만 갱신`으로
집계된다) 이미 만들어 둔 벡터는 그대로 남는다.

### 근거 링크

답변 단위마다 원문 주소(`answer_units.source_url`)를 들고 있고, `/ask` 응답의
`answers[].sources[].url`로 나간다. 링크는 규칙으로 만들지 않고 **빌드 때 스냅샷으로
받아온다** — llms.txt의 나열 순서로 페이지를 짝짓고(제목으로 짝지으면 안 된다. '자동화'와
'MCP 서버'가 각각 두 번 나온다), 섹션 앵커는 렌더된 페이지에서 실제 id를 읽는다.
이유와 함정은 [scripts/doc_links.py](scripts/doc_links.py)에 적어 두었다.

### 답변 평가

`/ask`는 답변을 보낸 뒤 그 답변을 자동으로 채점해 DB에 남긴다. 채점은 **응답을 보낸 다음에**
돌기 때문에(`BackgroundTasks`) 응답 시간에는 영향이 없다. 테이블은
`python -m scripts.build_qna_logs`가 만든다.

| 테이블 | 내용 |
|---|---|
| `qna_logs` | 채점에 넣는 입력 — 질문 원문·정제된 질문·답변 평문·검색된 문서 id. `qna_uuid`가 PK |
| `answer_evaluations` | 채점 결과 — 충실도·답변 관련성·문서 관련성, `verdict`(pass/fail), `issues`, 사유. 답변 1건에 1행 |

`qna_uuid`는 `/ask`가 발급해 응답에 실어 보낸다. 백엔드가 발급하는 메시지 id와는 **다른 값이다** —
이 값은 답변을 만들 때 생기고, 메시지 id는 답변을 저장한 뒤에 생긴다.

**채점에 실패하면 아무것도 저장하지 않는다.** 실패한 행을 남기면 "아직 평가 안 함"과 구분되지
않기 때문이다. 행이 없어야 미평가로 다시 잡혀 나중에 재실행할 수 있다.

판정 프롬프트와 문제 유형 코드는 [core/prompts.py](core/prompts.py)에 있다. 코드는 사용자가
bad를 고를 때 쓰는 항목과 어휘를 맞췄지만 정의역이 다르다 — "오래된 정보"는 판정자가 낼 수 없고
(검색된 문서만 보므로 문서 자체가 낡았으면 오히려 충실도가 1.0이 된다), 반대로 `retrieval_miss`
(문서가 질문과 무관하다)는 근거 문서를 보지 않는 사용자가 낼 수 없다.

### 설정

설정 파일은 둘이고, 다루는 것이 겹치지 않는다.

| 파일 | 갖는 것 | 쓰는 쪽 |
|---|---|---|
| [core/config.py](core/config.py) | 환경변수에서 오는 값 — DB 접속·풀 크기, OpenAI 키 | `core/`, `scripts/`, `api/` |
| [api/settings.py](api/settings.py) | HTTP 계층 정책 — CORS, 경로 접두사, 페이지·검색 기본값 | `api/`만 |

`.env`는 `core/config.py`가 한 번만 읽고, 다른 파일에서 `os.getenv()`를 직접 부르지 않는다.
필요한 값의 목록은 [.env.example](.env.example)이 전부다.

한쪽이 다른 쪽 값을 받아 그대로 넘기기만 하는 필드는 두지 않는다. 통과만 하는 필드가 있으면
"이 설정의 주인이 누구인가"가 흐려진다. HTTP 계층 설정을 `core/`에 합치지 않는 이유도 같다 —
`core/`는 HTTP를 몰라야 `scripts/`가 그대로 쓸 수 있다.

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
- **운영 콘솔 API** — 품질 로그 조회(`GET /qna`)와 평가 재실행(`POST /evaluations`)이 아직 없다.
  자동 채점을 놓쳤거나 판정 프롬프트를 고쳐 다시 돌릴 때 필요하다. 콘솔이 백엔드를 거치지 않고
  이 서버에 직접 붙을 예정이라 관리자 인증도 함께 있어야 한다.
- **답변 재생성** — 채점이 답변을 보낸 뒤에 돌기 때문에 점수가 낮아도 그 자리에서 다시 만들 수 없다.
  지금은 질문 1 : 답변 1 : 평가 1이다. 재생성을 도입하려면 재시도가 **무엇을 바꾸는지**
  (검색어 변형 / top_k / temperature) 먼저 정해야 로그에 붙일 컬럼이 의미를 갖는다.
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
