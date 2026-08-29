# Riido RAG API

의존 방향은 `api/` → `core/` → `domain/` 단방향이다. 코어는 HTTP를 모르고, 
아무도 `api/`를 import하지 않는다.

## 실행

```bash
pip install -r requirements.txt             # fastapi·uvicorn·pydantic-settings 포함
python -m scripts.build_answer_units        # answer_units 적재 (먼저)
python -m scripts.build_search_units        # search_units 적재
uvicorn api.main:app --reload               # 프로젝트 루트에서
```

- Swagger: http://127.0.0.1:8000/docs
- 인덱스가 없거나 비어 있으면 `GET /api/v1/health`가 `degraded`와 함께 안내를 준다.

## 구조

```
api/
├── main.py                        FastAPI 앱, lifespan, 예외 핸들러
├── settings.py                    HTTP 계층 정책 (CORS, 기본 top_k, 페이지 크기)
├── deps.py                        페이지네이션·검색 파라미터 기본값
├── schemas/                       HTTP 경계 Pydantic 모델
│   ├── common.py  chat.py  units.py  health.py
├── repositories/units_repository.py   목록·단건 조회 SQL
├── routers/                       chat / answer_units / search_units / health
└── services/rag_service.py        질의→검색→생성→평가 오케스트레이션
```

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/ask` | **질문 → 답변 + 근거 문서 id** |
| GET | `/api/v1/answer-units` | **모든 근거 문서 목록** |
| GET | `/api/v1/answer-units/{doc_id}` | 문서 단건 + 연결된 검색 문장 |
| GET | `/api/v1/search-units` | **모든 검색용 문장 목록** |
| GET | `/api/v1/search-units/stats` | view_type별 적재 통계 |
| GET | `/api/v1/health` | DB·인덱스 적재 상태 |

`doc_id`에 슬래시가 들어가므로(`guide/팀/팀-관리`) 단건 조회는 `{doc_id:path}` 컨버터를 쓴다.
`GET /api/v1/answer-units/guide/팀/팀-관리` 형태로 그대로 호출하면 된다.

### POST /api/v1/ask

`query`가 필수고, 이전 대화가 있으면 `history`와 `conversation_id`를 함께 보낸다.
`top_k`·`vector_weight`는 **요청으로 받지 않는다.** 서버 기본값(`Settings`)만 쓴다 —
클라이언트가 `top_k`를 올려 비용을 밀어넣을 수 있게 둘 이유가 없고, 검색 파라미터 튜닝은
`python -m scripts.evaluate_search`로 오프라인에서 한다. 근거 문서는 항상 포함하며,
환각 평가는 하지 않는다.

```jsonc
// 요청 — 첫 대화면 history와 conversation_id를 생략한다
{
  "query": "팀원을 어떻게 추가해?"
}

// 응답
{
  "raw_query": "팀원을 어떻게 추가해?",
  "cleaned_query": "팀원 추가 방법",
  "needs_search": true,
  "conversation_id": null,
  "history_turns_used": 0,
  "title": "팀원 추가 방법",     // 첫 턴에서만 채워진다
  "answer": "...",
  "doc_ids": ["guide/멤버", "guide/멤버/권한"],
  "documents": [...]
}
```

인사·잡담이면 `needs_search: false`로 검색과 생성을 모두 건너뛰고 `doc_ids`는 빈 배열이다.

**멀티턴** — 이전 대화를 `history`로 보내면 후속 질문의 대명사·생략을 앞 턴에서 풀어 검색한다.
대화의 소유자는 백엔드다. 이 서비스는 요청 바디로 받은 것만 보고 `conversation_id`로 DB를
조회하지 않는다(요청 경로에서 남의 DB를 읽으면 그쪽 장애가 답변 실패가 된다).

```jsonc
// 요청
{
  "query": "그럼 삭제는?",
  "conversation_id": "conv_01H8XK",
  "history": [
    { "question": "팀원을 어떻게 추가해?", "answer": "팀 설정 > 멤버에서 초대할 수 있습니다." }
  ]
}
```

**`title`** — 대화 목록에 걸 제목이다. `history`와 `conversation_id`가 **둘 다 없는 첫 턴에서만**
채워 보내고, 후속 턴은 빈 문자열이다. 빈 문자열은 "제목을 바꾸지 말라"는 뜻이므로 그 값으로
기존 제목을 덮어쓰면 안 된다. 백엔드는 자기가 `conversation_id`를 보냈는지로 첫 턴인지 이미
알고 있으니, **대화를 새로 만들 때만 반영하고 후속 턴에서는 무시**하는 편이 안전하다.

## 도메인 객체 vs API 스키마

**도메인 dataclass와 API 스키마를 분리했다.**

| 계층 | 위치 | 역할 |
|---|---|---|
| 도메인 | [domain/](../domain/) — `SearchHit`, `RetrievedChunk`, `Query`, `Answer` … | 모듈 간 내부 표현. 점수·임베딩 등 내부 값 포함 |
| API | `api/schemas/` — `AskResponse`, `AnswerUnitOut`, `SearchUnitOut` … | HTTP 계약. 노출할 필드만, 검증 규칙과 예시 포함 |

이유는 세 가지다.

1. **내부 전용 필드를 자연스럽게 감춘다.** `RetrievedChunk.hits`(그 문서를 끌어온 검색 문장들)는
   응답에 필요 없다. `AnswerUnitOut.from_domain()`이 떨어뜨린다.
2. **내부 리팩터링이 API 계약을 깨지 않는다.** dataclass 필드가 바뀌어도 `from_domain()`만 고치면
   클라이언트는 영향이 없다.
3. **OpenAPI 문서 품질.** `query: Field(min_length=1, max_length=1000)`, `examples=[...]`가
   Swagger에 그대로 나오고, 잘못된 입력은 라우터에 들어오기 전에 422로 막힌다
   (빈 문자열 → 422 확인 완료).

변환은 각 스키마의 `from_domain()` / `from_row()` 한 곳에서만 한다. 라우터에는 변환 로직을 두지 않는다.

`domain/`을 그대로 Pydantic으로 바꾸는 선택지도 있다. 파일 수는 줄지만 내부 구조가 HTTP 계약에
직접 묶여서, 지금처럼 검색 파이프라인을 자주 고치는 단계에서는 분리 쪽이 안전하다.

## 설계 메모

- **엔드포인트가 `async def`가 아니라 `def`인 이유**: psycopg2·openai·langchain이 전부 동기라
  `async def`로 두면 이벤트 루프가 막힌다. `def`로 두면 FastAPI가 스레드풀에서 실행한다.
- **커넥션 풀**: [core/db.py](../core/db.py)의 `ThreadedConnectionPool` 하나를 `core.search`와 API가 공유한다.
  FastAPI는 `lifespan`에서 `init_pool()`/`close_pool()`로 관리하고, 스크립트로 직접 실행할 때는
  첫 사용 시 지연 초기화된다. 커넥션은 SQL을 던지는 구간에서만 빌린다 — 임베딩(외부 API) 호출을
  마친 뒤에 빌리므로, 네트워크 대기 동안 커넥션을 붙잡지 않는다.
  단, `scripts/`의 빌드 작업은 풀 대신 `core.db.connect()`로 독립 커넥션을 연다
  (수 분짜리 트랜잭션이 풀을 점유하면 그동안 요청 처리 쪽이 굶는다).
  DSN과 풀 크기 기본값의 출처는 [core/db.py](../core/db.py) 하나뿐이고,
  `Settings.database_url`은 그 값을 기본값으로 얹어 `.env`로 덮어쓸 수 있게 한 것이다.
- **LLM 오류 처리**: [core/generation.py](../core/generation.py)는 실패 시 `LlmError`를 올린다. `main.py`의 예외 핸들러가
  502로 변환하므로 오류 메시지가 정상 답변처럼 200 OK로 나가지 않는다.
  [core/evaluation.py](../core/evaluation.py)는 `EvaluationError`를 올린다. 평가는 부가 정보라
  `rag_service`가 잡아서 로그만 남기고 `evaluation: null`로 응답한다 — 실패를 0.0으로 채우면
  "완전한 환각" 판정과 값이 같아져 구분할 수 없기 때문이다.
- **부팅 비용**: `core.search`는 로드 시점에 Kiwi와 임베딩 클라이언트를 만든다. 첫 요청이 이 비용을
  떠안지 않도록 `lifespan`에서 `core.search.warmup()`을 부른다. 그 import를 파일 최상단으로
  올리면 안 된다 — 로드 비용이 부팅 전으로 앞당겨져 lifespan이 재는 시간이 0이 된다.

## 추가로 제안하는 API

구현하지 않았다. 스키마나 테이블 결정이 필요해서다.

| 제안 | 이유 |
|---|---|
| `POST /feedback` — 답변 good/bad | [domain/qna.py](../domain/qna.py)에 `is_good` 필드가 있는데 현재 아무 데서도 안 쓴다. 대화 로그를 남길 계획이었다면 `qna` 테이블과 이 엔드포인트가 그 자리다 |
| `GET /answer-units/orphans` — 검색 문장이 없는 문서 | `search_units`가 하나도 안 달린 `answer_units`는 영원히 검색되지 않는다. 인덱스 품질 점검용 |
| `POST /admin/reindex` — 인덱스 재빌드 트리거 | 지금은 서버에 SSH로 들어가 스크립트를 돌려야 한다. 다만 수 분 걸리는 작업이라 BackgroundTasks나 작업 큐가 필요하고, 인증도 있어야 한다 |
| `GET /ask/stream` — 답변 토큰 스트리밍 | `/ask`는 LLM 2~3회 + 임베딩 1회라 체감 지연이 크다. SSE로 답변을 흘려보내면 개선된다. `core/generation.py`가 `stream=True`를 지원하도록 바뀌어야 한다 |
| 평가를 백그라운드로 | 환각 평가는 LLM 1회가 더 들어 `/ask`에서 뺐다. `rag_service.ask(evaluate=True)` 경로는 남아 있으니, 응답은 먼저 주고 평가는 `BackgroundTasks`로 돌려 로그에만 남기면 된다 |
