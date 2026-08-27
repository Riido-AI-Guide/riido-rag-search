# Riido RAG API (초안)

기존 모듈([rag_search.py](../rag_search.py), [llm.py](../llm.py), [evaluator.py](../evaluator.py),
[query_transform.py](../query_transform.py))은 **수정하지 않고 호출만** 한다.
API에 필요한 것만 이 폴더 안에 새로 작성했다.

## 실행

```bash
pip install -r requirements.txt             # fastapi·uvicorn·pydantic-settings 포함
python answer_builder.py                    # answer_units 적재 (먼저)
python search_builder.py                    # search_units 적재
uvicorn api.main:app --reload               # 프로젝트 루트에서
```

- Swagger: http://127.0.0.1:8000/docs
- 인덱스가 없거나 비어 있으면 `GET /api/v1/health`가 `degraded`와 함께 안내를 준다.

## 구조

```
api/
├── main.py                        FastAPI 앱, lifespan, 예외 핸들러
├── config.py                      Settings (DATABASE_URL, 기본 top_k 등)
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
| POST | `/api/v1/search` | 답변 생성 없이 검색만 (LLM 비용 0) |
| GET | `/api/v1/answer-units` | **모든 근거 문서 목록** |
| GET | `/api/v1/answer-units/{doc_id}` | 문서 단건 + 연결된 검색 문장 |
| GET | `/api/v1/search-units` | **모든 검색용 문장 목록** |
| GET | `/api/v1/search-units/stats` | view_type별 적재 통계 |
| GET | `/api/v1/health` | DB·인덱스 적재 상태 |

`doc_id`에 슬래시가 들어가므로(`guide/팀/팀-관리`) 단건 조회는 `{doc_id:path}` 컨버터를 쓴다.
`GET /api/v1/answer-units/guide/팀/팀-관리` 형태로 그대로 호출하면 된다.

### POST /api/v1/ask

요청 본문은 `query` 하나뿐이다. `top_k`·`vector_weight`는 서버 기본값(`Settings`)을 쓰고,
근거 문서는 항상 포함하며, 환각 평가는 하지 않는다.

```jsonc
// 요청
{
  "query": "팀원을 어떻게 추가해?"
}

// 응답
{
  "raw_query": "팀원을 어떻게 추가해?",
  "cleaned_query": "팀원 추가 방법",
  "needs_search": true,
  "answer": "...",
  "doc_ids": ["guide/멤버", "guide/멤버/권한"],
  "documents": [...]
}
```

인사·잡담이면 `needs_search: false`로 검색과 생성을 모두 건너뛰고 `doc_ids`는 빈 배열이다.

## DTO 설계

**도메인 dataclass와 API 스키마를 분리했다.**

| 계층 | 위치 | 역할 |
|---|---|---|
| 도메인 | [dto/](../dto/) — `SearchHit`, `RetrievedChunk`, `Query`, `Answer` … | 모듈 간 내부 표현. 점수·임베딩 등 내부 값 포함 |
| API | `api/schemas/` — `AskResponse`, `AnswerUnitOut`, `SearchHitOut` … | HTTP 계약. 노출할 필드만, 검증 규칙과 예시 포함 |

이유는 세 가지다.

1. **내부 전용 필드를 자연스럽게 감춘다.** `RetrievedChunk.hits`(그 문서를 끌어온 검색 문장들)는
   응답에 필요 없다. `AnswerUnitOut.from_domain()`이 떨어뜨린다.
2. **내부 리팩터링이 API 계약을 깨지 않는다.** dataclass 필드가 바뀌어도 `from_domain()`만 고치면
   클라이언트는 영향이 없다.
3. **OpenAPI 문서 품질.** `top_k: Field(ge=1, le=20)`, `examples=[...]`가 Swagger에 그대로 나오고,
   잘못된 입력은 라우터에 들어오기 전에 422로 막힌다 (빈 문자열 → 422 확인 완료).

변환은 각 스키마의 `from_domain()` / `from_row()` 한 곳에서만 한다. 라우터에는 변환 로직을 두지 않는다.

`dto/`를 그대로 Pydantic으로 바꾸는 선택지도 있다. 파일 수는 줄지만 내부 구조가 HTTP 계약에
직접 묶여서, 지금처럼 검색 파이프라인을 자주 고치는 단계에서는 분리 쪽이 안전하다.

## 설계 메모

- **엔드포인트가 `async def`가 아니라 `def`인 이유**: psycopg2·openai·langchain이 전부 동기라
  `async def`로 두면 이벤트 루프가 막힌다. `def`로 두면 FastAPI가 스레드풀에서 실행한다.
- **커넥션 풀**: [db.py](../db.py)의 `ThreadedConnectionPool` 하나를 rag_search와 API가 공유한다.
  FastAPI는 `lifespan`에서 `init_pool()`/`close_pool()`로 관리하고, 스크립트로 직접 실행할 때는
  첫 사용 시 지연 초기화된다. 커넥션은 SQL을 던지는 구간에서만 빌린다 — 임베딩(외부 API) 호출을
  마친 뒤에 빌리므로, 네트워크 대기 동안 커넥션을 붙잡지 않는다.
  단, [answer_builder.py](../answer_builder.py)·[search_builder.py](../search_builder.py)는
  배치 작업이라 각자 커넥션을 직접 연다(장시간 트랜잭션이 풀을 점유하면 안 되기 때문).
- **LLM 오류 처리**: [llm.py](../llm.py)는 실패 시 `LlmError`를 올린다. `main.py`의 예외 핸들러가
  502로 변환하므로 오류 메시지가 정상 답변처럼 200 OK로 나가지 않는다.
  [evaluator.py](../evaluator.py)는 `EvaluationError`를 올린다. 평가는 부가 정보라
  `rag_service`가 잡아서 로그만 남기고 `evaluation: null`로 응답한다 — 실패를 0.0으로 채우면
  "완전한 환각" 판정과 값이 같아져 구분할 수 없기 때문이다.
- **부팅 비용**: `rag_search` import 시 Kiwi와 임베딩 클라이언트가 생성된다. 첫 요청이 이 비용을
  떠안지 않도록 `lifespan`에서 미리 import한다.

## 추가로 제안하는 API

구현하지 않았다. 스키마나 테이블 결정이 필요해서다.

| 제안 | 이유 |
|---|---|
| `POST /feedback` — 답변 good/bad | [dto/QnA.py](../dto/QnA.py)에 `is_good` 필드가 있는데 현재 아무 데서도 안 쓴다. 대화 로그를 남길 계획이었다면 `qna` 테이블과 이 엔드포인트가 그 자리다 |
| `GET /answer-units/orphans` — 검색 문장이 없는 문서 | `search_units`가 하나도 안 달린 `answer_units`는 영원히 검색되지 않는다. 인덱스 품질 점검용 |
| `POST /admin/reindex` — 인덱스 재빌드 트리거 | 지금은 서버에 SSH로 들어가 스크립트를 돌려야 한다. 다만 수 분 걸리는 작업이라 BackgroundTasks나 작업 큐가 필요하고, 인증도 있어야 한다 |
| `GET /ask/stream` — 답변 토큰 스트리밍 | `/ask`는 LLM 2~3회 + 임베딩 1회라 체감 지연이 크다. SSE로 답변을 흘려보내면 개선된다. `llm.py`가 `stream=True`를 지원하도록 바뀌어야 한다 |
| 평가를 백그라운드로 | 환각 평가는 LLM 1회가 더 들어 `/ask`에서 뺐다. `rag_service.ask(evaluate=True)` 경로는 남아 있으니, 응답은 먼저 주고 평가는 `BackgroundTasks`로 돌려 로그에만 남기면 된다 |
