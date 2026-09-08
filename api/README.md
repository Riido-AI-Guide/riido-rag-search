# Riido RAG API

의존 방향은 `api/` → `core/` → `domain/` 단방향이다. 코어는 HTTP를 모르고, 
아무도 `api/`를 import하지 않는다.

## 실행

```bash
pip install -r requirements.txt             # fastapi·uvicorn·pydantic-settings 포함
python -m scripts.build_answer_units        # answer_units 적재 (먼저)
python -m scripts.build_search_units        # search_units 적재
python -m scripts.build_qna_logs            # 질의응답 로그·평가 테이블 (인덱스와 무관, 순서 상관없음)
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
│   ├── common.py  chat.py  units.py  health.py  evaluations.py  qna.py
│   ├── index_status.py  coverage.py  feedback.py
├── repositories/                  DB 접근 SQL
│   ├── units_repository.py  qna_repository.py  feedback_repository.py
├── routers/                       chat / answer_units / search_units / evaluations / qna
│                                  index_status / feedback / health
└── services/                      rag_service.py  search_units_service.py
```

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/ask` | **질문 → 답변 + 근거 문서 id** |
| GET | `/api/v1/answer-units` | **모든 근거 문서 목록** |
| GET | `/api/v1/answer-units/{doc_id}` | 문서 단건 + 연결된 검색 문장 |
| GET | `/api/v1/search-units` | **모든 검색용 문장 목록** |
| GET | `/api/v1/search-units/stats` | view_type별 적재 통계 |
| GET | `/api/v1/search-units/coverage` | **문서별 검색 문장 현황 (없음·낡음 표시)** |
| GET | `/api/v1/search-units/coverage/{doc_id}` | 문서 전문 + 검색 문장 전체 |
| PUT | `/api/v1/search-units/coverage/{doc_id}` | **검색 문장 저장 (전체 교체 = 추가·수정·삭제)** |
| POST | `/api/v1/search-units/draft` | LLM으로 검색 문장 초안 생성 (저장 안 함) |
| GET | `/api/v1/search-units/export` | **검색 문장 전체를 `rag_view_sentences.json` 형식으로 내보내기** |
| GET | `/api/v1/evaluations` | **저장된 답변 평가 목록 — id 여러 개 한 번에 조회** |
| GET | `/api/v1/evaluations/{qna_uuid}` | 평가 단건 |
| GET | `/api/v1/qna` | **질의응답 로그 목록 — 미평가(`status=pending`) 조회** |
| POST | `/api/v1/evaluations/run` | **평가 일괄 실행 / 재실행 (여러 건)** |
| POST | `/api/v1/evaluations/{qna_uuid}` | 평가 실행 / 재실행 (1건, 결과를 기다린다) |
| GET | `/api/v1/feedback` | **사용자 피드백 목록 (자동 평가와 대조)** |
| GET | `/api/v1/feedback/stats` | rating × verdict 교차표 |
| GET | `/api/v1/feedback/{qna_uuid}` | 피드백 단건 (답변 본문 포함) |
| GET | `/api/v1/index-status` | **인덱스가 본문을 따라잡았는지 (재빌드 필요 여부)** |
| GET | `/api/v1/health` | DB·인덱스 적재 상태 |

`doc_id`에 슬래시가 들어가므로(`guide/팀/팀-관리`) 단건 조회는 `{doc_id:path}` 컨버터를 쓴다.
`GET /api/v1/answer-units/guide/팀/팀-관리` 형태로 그대로 호출하면 된다.

### POST /api/v1/ask

`query`가 필수고, 이전 대화가 있으면 `history`와 `conversation_id`를 함께 보낸다.
`top_k`·`vector_weight`는 **요청으로 받지 않는다.** 서버 기본값(`Settings`)만 쓴다 —
클라이언트가 `top_k`를 올려 비용을 밀어넣을 수 있게 둘 이유가 없고, 검색 파라미터 튜닝은
`python -m scripts.evaluate_search`로 오프라인에서 한다. 근거 문서는 항상 포함한다.

**답변을 보낸 뒤** 서버가 그 답변을 자동으로 채점해 품질 로그에 남긴다. 채점은 응답을 보낸 다음에
돌기 때문에 `/ask`의 응답 시간은 그대로다. 채점 결과는 응답의 `qna_uuid`로 되짚는다.

```jsonc
// 요청 — 첫 대화면 history와 conversation_id를 생략한다
{
  "query": "팀원을 어떻게 추가해?"
}

// 응답
{
  "qna_uuid": "3f2b9c14-8a51-4e77-9d2c-6b0f5a1e7c84",   // 이 턴의 식별자
  "raw_query": "팀원을 어떻게 추가해?",
  "cleaned_query": "팀원 추가 방법",
  "needs_search": true,
  "conversation_id": null,
  "title": "팀원 추가 방법",       // 이 턴의 제목. 매 턴 온다
  "answer_type": "step",          // 프론트가 레이아웃을 고르는 값
  "answers": [                    // 답변 본문. 평문 answer 필드는 없다
    { "label": "핵심답변", "text": "팀 설정 > 멤버에서 초대할 수 있습니다.",
      "sources": [{ "doc_id": "guide/멤버", "section": "멤버 > 멤버 초대",
                    "url": "https://docs.riido.io/workspaces/members#undefined-1" }] },
    { "label": "주의사항", "text": "초대는 관리자만 할 수 있습니다.",
      "sources": [{ "doc_id": "guide/멤버/권한", "section": "멤버 > 권한",
                    "url": "https://docs.riido.io/workspaces/members#undefined-3" }] }
  ],
  "doc_ids": ["guide/멤버", "guide/멤버/권한"],
  "documents": [...]
}
```

**`qna_uuid`** — 이 턴에 붙는 식별자다. **어느 경로에서도 비지 않는다.**
답변을 보낸 뒤 서버가 그 답변을 채점하고 결과를 이 값에 붙여 두므로, 메시지와 함께 저장해 두면
나중에 사용자 good/bad 평가와 대조하거나 대화가 지워질 때 품질 로그도 함께 정리할 수 있다.
당장 쓰지 않아도 무방하다.

백엔드가 발급하는 메시지 id와는 **다른 값이다.** 이 값은 답변을 만들 때 생기고, 메시지 id는
답변을 저장한 뒤에 생긴다.

**`answer_type`** — 답변 유형이다. 정상 답변은 `concept`/`step`/`judgement`/`troubleshoot`/
`explore` 중 하나이고, 유형마다 `answers`의 `label` 구성이 다르다(→ [core/prompts.py](../core/prompts.py)의
`SECTION_LABELS`). 나머지 셋은 답이 없거나 정상 경로가 아닌 경우다.

| 값 | 뜻 |
|---|---|
| `no_answer` | 검색은 했지만 근거 문서에 답이 없다 |
| `parse_error` | LLM이 형식을 깨뜨렸다. 라벨 없는 섹션 하나에 원문만 들어 있다 |
| `no_search` | 인사·잡담이라 검색·생성을 건너뛰었다 (`needs_search: false`) |

**`title`** — 이 턴의 제목이다. **매 턴 온다.** 답변 생성이 만든 제목을 그대로 쓰기 때문에
제목 때문에 LLM을 더 부르지 않는다(예전에는 첫 턴마다 제목 전용 호출이 한 번 더 있었다).

쓰임이 둘이다.

| | 하는 일 |
|---|---|
| 첫 턴 | 이 값을 **대화 제목으로 저장**한다. 말풍선 제목으로도 쓴다 |
| 후속 턴 | **말풍선 제목으로만** 쓴다. 대화 제목은 그대로 둔다 |

**후속 턴의 `title`로 대화 제목을 덮어쓰면 안 된다.** 매 턴 그 답변에 맞춰 달라지는 값이라
덮어쓰면 대화 제목이 계속 바뀐다. 첫 턴인지는 백엔드가 `conversation_id`를 보냈는지로 이미
알고 있으니, **대화를 새로 만들 때만 저장하면 된다.**

**첫 턴에서는 비지 않는다.** 답변에 제목이 없으면(`no_answer`, `parse_error`) 질문 원문을
줄인 값이, 인사·잡담(`no_search`)이면 `"새 대화"`가 대신 온다. 빈 문자열은 후속 턴의
인사에서만 오고, 그때는 제목 없이 본문만 그리면 된다.

**`answers`** — 답변을 이루는 덩어리와 그 덩어리의 근거다. 프론트는 덩어리 단위로 렌더하고
`sources`를 문장 끝 근거 버튼으로 단다. 버튼 라벨에는 `section`("멤버 > 권한")을 쓰고,
링크는 `url`로 건다 — `doc_id`는 슬러그고 `url`은 사람이 읽을 형태가 아니라
(`#undefined-3`) 둘 다 화면에 그대로 쓸 값이 아니다. 근거가 없으면 `sources`가 빈 배열이다.

`url`은 그 문서의 docs.riido.io 주소이고 가능하면 섹션 앵커까지 붙는다. 앵커를 못 붙인
문서는 페이지 주소만, 링크 자체가 없는 문서는 빈 문자열이므로 **버튼을 걸기 전에 확인해야
한다.** 앵커가 `#undefined-3` 꼴인 것은 GitBook이 한글 제목의 슬러그를 만들지 못하기
때문이고, 빌드 때 실제 발행된 id를 읽어온 값이라 그대로 열면 해당 섹션으로 이동한다
(→ [scripts/doc_links.py](../scripts/doc_links.py)). `documents[].url`도 같은 값이다.

한 질문에 대한 후보 답변 여러 개가 아니라 **답변 하나를 이루는 조각들**이다(내부 이름은
`AnswerSection`이다). `answers[0]`을 "첫 번째 답변"으로 읽지 말 것.

`answers`는 **어느 경로에서도 비지 않는다.** 인사·잡담일 때와 LLM이 형식을 깨뜨렸을 때
(`parse_error`)는 `label`과 `sources`가 빈 항목 하나에 텍스트가 담겨 오므로, 프론트는
"라벨이 비면 제목 없이 본문만 그린다" 한 가지 규칙만 두면 된다. 빈 배열 분기가 필요 없다.

**평문 `answer` 필드는 없다.** 같은 내용을 구조와 평문으로 두 벌 들고 있으면 반드시 어긋나기
때문이다. 다음 턴 `history[].answer`에 넣을 문자열은 `answers[].text`를 이어붙여 만든다 —
**`label`은 넣지 말 것.** 그 값은 화면에 뜨는 게 아니라 질문 재작성 프롬프트에 앞 150자만
잘려 들어가는데([core/query_transform.py](../core/query_transform.py)), `**핵심답변**` 같은
라벨이 그 창의 18%를 잡아먹는다.

```js
const historyAnswer = res.answers.map(a => a.text).join("\n\n")
```

**`doc_ids`** — 답변이 실제로 인용한 문서만, 처음 등장한 순서대로 중복 없이 준다.
검색에는 걸렸지만 답변에 쓰이지 않은 문서는 빠지며, 그쪽은 `documents`에 그대로 있다
(`documents`는 `doc_ids`의 상위집합이다).

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

### GET /api/v1/evaluations

답변을 보낸 뒤 서버가 자동으로 매긴 점수를 **최근 것부터** 준다. 운영 콘솔에서 답변 품질을
훑고, 사용자 good/bad와 대조할 때 쓴다.

점수만 있는 목록은 쓸모가 없으므로 `qna_logs`를 조인해 **무엇을 채점한 것인지**(`raw_query`,
`cleaned_query`, `answer_type`, `conversation_id`)를 함께 준다. 답변 본문(`answer_text`)은
싣지 않는다 — 수천 자짜리 필드가 페이지마다 붙으면 목록 응답이 커진다.

**채점에 실패한 답변은 여기 없다.** 평가 실패는 행을 남기지 않기 때문이다(→ 설계 메모의 LLM
오류 처리). 목록에 없는 `qna_uuid`는 "아직 채점 안 됨"이고, 그게 곧 재실행 대상이다.

| 쿼리 | 설명 |
|---|---|
| `limit` · `offset` | 페이지네이션. 기본 50, 상한 500 (`Settings`) |
| `qna_uuid` | **이 턴들의 평가만.** 반복해 넘긴다: `?qna_uuid=A&qna_uuid=B`. 한 번에 500개까지 |
| `conversation_id` | 한 대화의 평가만 |
| `verdict` | `pass` / `fail` |
| `issue` | `factual_error` / `insufficient` / `irrelevant` / `retrieval_miss` 중 하나가 달린 평가만 |
| `answer_type` | 채점 대상 답변의 유형 (`step`, `no_answer` …) |
| `q` | `raw_query`·`cleaned_query` 부분 일치 |

```jsonc
// GET /api/v1/evaluations?verdict=fail&limit=2
{
  "total": 37, "limit": 2, "offset": 0,
  "items": [
    {
      "qna_uuid": "3f2b9c14-8a51-4e77-9d2c-6b0f5a1e7c84",
      "conversation_id": "conv_01H8XK",
      "raw_query": "휴지통 복구?",
      "cleaned_query": "휴지통 복구 방법",
      "answer_type": "no_answer",
      "faithfulness": 1.0,
      "answer_relevance": 1.0,
      "context_relevance": 0.2,
      "verdict": "fail",
      "issues": ["retrieval_miss"],
      "reason": "참고 문서가 질문과 무관합니다.",
      "created_at": "2026-09-08T00:13:40.716963Z",
      "updated_at": "2026-09-08T00:13:40.716963Z"
    }
  ]
}
```

**여러 개를 볼 때 단건 API를 반복 호출하지 말 것.** 단건은 uuid 하나를 이미 알고 그것만
펼쳐 볼 때(상세 패널)를 위한 것이다. 화면에 메시지가 20개 떠 있으면 요청도 20번 나간다.

```js
// ✗ N+1 — 메시지 수만큼 요청이 나간다
await Promise.all(ids.map(id => fetch(`/api/v1/evaluations/${id}`)))

// ✓ 한 번에
const qs = ids.map(id => `qna_uuid=${id}`).join("&")
const { items } = await fetch(`/api/v1/evaluations?${qs}&limit=${ids.length}`).then(r => r.json())
const byId = new Map(items.map(e => [e.qna_uuid, e]))   // 맵에 없는 메시지 = 평가 없음
```

`limit`을 id 개수만큼 함께 올려야 한다. id를 100개 넘겨도 `limit`이 기본값 50이면 50건만
온다 — 필터와 페이지네이션은 서로 모른다. 한 대화 전체라면 `?conversation_id=...` 쪽이 간단하다.

**요청한 id가 응답에 다 오지 않는다.** 채점되지 않은 턴은 평가 행이 없어서 그냥 빠진다 —
오류가 아니다. 맵에 없는 메시지는 "평가 없음"으로 그리고, 왜 없는지(미평가인지 채점 대상이
아닌지)가 필요하면 `GET /qna`의 `status`를 본다.

`GET /api/v1/evaluations/{qna_uuid}`는 같은 형태의 단건이다. 채점되지 않았으면 404,
uuid 형식이 아니면 422다.

### GET /api/v1/qna

질의응답 로그를 최근 턴부터 준다. **미평가 턴은 여기서만 보인다** — 평가에 실패하면 행을
남기지 않는 설계라(→ 설계 메모의 LLM 오류 처리) `/evaluations`에는 애초에 나타나지 않는다.

각 행의 `status`가 채점 여부다.

| status | 뜻 |
|---|---|
| `pending` | **미평가.** 채점을 놓쳤거나 실패한 턴 — 재실행 대상이다 |
| `evaluated` | 채점됨. `verdict`(pass/fail)가 함께 오고, 점수 전체는 `/evaluations`에 있다 |
| `skipped` | 인사·잡담(`no_search`)이라 애초에 채점 대상이 아니다 |

`skipped`를 따로 두는 이유는 그 턴들이 **영원히** 채점되지 않기 때문이다
(`rag_service.evaluate_and_store()`가 `no_search`를 그냥 건너뛴다). 미평가에 섞이면
재실행 목록이 "안녕하세요"로 차서 줄지 않는다. 상태 계산은
`qna_repository.LOG_STATUS_SQL` 한 곳에만 있다.

| 쿼리 | 설명 |
|---|---|
| `limit` · `offset` | 페이지네이션. 기본 50, 상한 500 (`Settings`) |
| `qna_uuid` | 이 턴들만. 반복해 넘긴다: `?qna_uuid=A&qna_uuid=B`. 한 번에 500개까지 |
| `status` | `pending` / `evaluated` / `skipped`. 그 외 값은 422 |
| `answer_type` | `step`, `no_answer` … |
| `conversation_id` | 한 대화의 턴만 |
| `q` | `raw_query`·`cleaned_query` 부분 일치 |
| `include_answer` | 답변 본문 포함 여부. 기본 `false` |

답변 본문(`answer_text`)은 기본으로 빼고 `include_answer=true`일 때만 싣는다 — 수천 자짜리
필드가 페이지마다 붙으면 목록 응답이 커진다.

```jsonc
// GET /api/v1/qna?status=pending — 미평가(재실행 대상) 목록
{
  "total": 4, "limit": 50, "offset": 0,
  "items": [
    {
      "qna_uuid": "3f2b9c14-8a51-4e77-9d2c-6b0f5a1e7c84",
      "conversation_id": "conv_01H8XK",
      "raw_query": "휴지통 복구?",
      "cleaned_query": "휴지통 복구 방법",
      "answer_type": "no_answer",
      "retrieved_doc_ids": ["guide/휴지통"],
      "status": "pending",
      "verdict": null,               // 채점된 턴에만 pass/fail이 온다
      "created_at": "2026-09-08T00:13:40.716963Z",
      "answer_text": null            // include_answer=true면 채워진다
    }
  ]
}
```

미평가를 다시 돌리는 흐름은 이렇다.

```
GET  /api/v1/qna?status=pending        # 재실행 대상 확인
POST /api/v1/evaluations/run           # 미평가를 한 번에 채점 (202, 뒤에서 돈다)
POST /api/v1/evaluations/{qna_uuid}    # 한 건만, 결과를 기다리며 채점
```

### POST /api/v1/evaluations/{qna_uuid}

그 턴을 **지금 채점**하고 결과를 돌려준다. 쓰임이 둘이다.

1. **미평가 재실행** — `/ask` 뒤 백그라운드 채점을 놓쳤거나(프로세스 종료) 판정자가 실패한 턴.
   `GET /qna?status=pending`으로 찾아 이걸 부른다
2. **재채점** — 프롬프트를 고친 뒤 이미 채점된 턴을 다시 매긴다. 답변 1건에 평가 1건이라
   덮어쓴다(`updated_at`만 갱신된다)

응답은 `GET /evaluations`의 항목과 같은 모양이다. **판정자 LLM을 1회 호출하므로 수 초 걸린다** —
응답을 기다렸다가 그대로 화면에 반영하면 된다.

| 상태 | 뜻 |
|---|---|
| 200 | 채점 완료. 결과가 저장되었다 |
| 404 | 로그가 없다. 대화가 지워졌거나 로그 저장이 실패했던 턴이라 다시 눌러도 같다 |
| 409 | 인사·잡담(`no_search`)이라 채점 대상이 아니다. `GET /qna`의 `skipped`가 이 턴들이다 |
| 502 | 판정자가 실패했다. **아무것도 저장되지 않아 미평가로 남는다** — 다시 시도할 수 있다 |
| 422 | uuid 형식이 아니다 |

여러 건이면 이걸 반복 호출하지 말고 아래 일괄 실행을 쓴다.

### POST /api/v1/evaluations/run

여러 턴을 한 번에 채점한다. 한 건마다 판정자 LLM이 1회 돌아 수 초씩 걸리므로, 단건 API를
20번 부르면 요청도 20번이고 응답도 수 분이다.

| 바디 | 무엇을 채점하나 |
|---|---|
| `{"qna_uuids": ["...", "..."]}` | 그 턴들. **이미 채점된 턴도 다시 매긴다**(프롬프트를 고친 경우) |
| `{"limit": 20}` 또는 `{}` | **미평가에서 최근 `limit`건.** 재실행 버튼 하나가 이것이다 |

한 번에 50건까지다 — 그 상한이 곧 판정자 LLM 호출 수의 상한이다.

```jsonc
// 202 Accepted — 채점은 응답을 보낸 뒤에 돈다
{
  "queued":    ["3f2b9c14-…", "8a51-…"],   // 채점을 예약한 턴
  "skipped":   ["6b0f5a1e-…"],             // 인사·잡담이라 채점 대상이 아님
  "not_found": [],                          // 로그가 없는 턴
  "hint": "채점은 응답을 보낸 뒤에 돕니다. GET /api/v1/qna?status=pending 의 건수가 …"
}
```

**202로 먼저 답한다.** 몇 분짜리 작업이라 응답 안에서 끝낼 수 없다. 진행 상황은
`GET /qna?status=pending`의 건수가 줄어드는 것으로 보고, 결과는 `GET /evaluations`에서 본다.

**버튼을 두 번 눌러도 비용이 두 배가 되지 않는다.** 지금 채점 중인 턴은 예약이 조용히
버려진다(`rag_service._in_flight`). 프로세스 안에서만 유효한 자물쇠라 워커를 여러 개 띄우면
워커별로 따로 잡히지만, 결과는 덮어쓰기라 데이터가 망가지지는 않는다 — 비용만 든다.

### GET /api/v1/index-status

가이드를 다시 빌드한 뒤 **검색 인덱스가 따라왔는지** 본다. `/health`는 테이블이 있는지·
비었는지만 보므로 이건 그 다음 질문이다: 적재는 됐는데 **검색이 최신인가.**

`python -m scripts.build_answer_units`는 답변 본문만 갱신하고 검색 인덱스는 건드리지 않는다.
그것만 돌리면 **답변은 최신인데 검색은 옛 문서 기준**으로 남고, 에러가 나지 않아 아무도 모른다.

| 항목 | 뜻 | 해결 |
|---|---|---|
| `outdated_content_vector` | 옛 본문으로 색인된 문서. 새 내용의 단어로는 안 걸리고 지워진 내용의 단어로는 걸린다 | `build_search_units` |
| `no_content_vector` | 원문 인덱스가 없어 **키워드 검색**에서 빠진 문서 | `build_search_units` |
| `no_search_units` | 검색 문장이 없어 **벡터 검색**에서 빠진 문서 | `rag_view_sentences.json`에 문장 추가 후 `build_search_units`. **자동 생성 경로가 없어 사람 손이 필요하다** |

낡음의 기준은 `answer_content_vectors.source_hash`다 — 그 인덱스가 어느 본문을 색인한
것인지 행에 적어 두고 `answer_units.source_hash`와 비교한다. 예전에는 행이 있으면 그냥
건너뛰어서 변경된 문서가 영원히 옛 인덱스로 남았다.

```jsonc
{
  "status": "stale",              // ok면 재빌드할 것이 없다
  "answer_units": 179,
  "built_at": "2026-09-02T02:59:51.467076Z",   // answer_units가 마지막으로 바뀐 시각
  "no_search_units":         { "count": 1, "doc_ids": ["guide/소개"] },
  "no_content_vector":       { "count": 0, "doc_ids": [] },
  "outdated_content_vector": { "count": 0, "doc_ids": [] },
  "hint": "검색 문장이 없는 문서 1건은 자동으로 채워지지 않습니다 — ..."
}
```

`doc_ids`는 앞 20건 표본이고 `count`가 전체다. `status`가 `stale`이어도 오류가 아니라
"검색이 최신이 아니다"라는 뜻이므로 200으로 답한다.

### 검색 문장 관리 (운영 콘솔)

검색 문장(`search_units`)은 **자동 생성 경로가 없다.** 출처인
[data/rag_view_sentences.json](../data/rag_view_sentences.json)이 외부에서 만들어 커밋한
파일이라, 새로 생긴 문서나 내용이 바뀐 문서의 문장은 사람이 채워야 한다. 아래 네 API가
그 작업 화면을 이룬다.

```
GET  /search-units/coverage?status=missing   ① 손볼 문서 찾기
GET  /search-units/coverage/{doc_id}         ② 원문 + 지금 문장 보기
POST /search-units/draft                     ③ LLM 초안 (저장 안 함)
PUT  /search-units/coverage/{doc_id}         ④ 고친 목록 통째로 저장 → 즉시 검색됨
GET  /search-units/export                    ⑤ 작업 결과를 JSON으로 받아 저장소에 커밋
```

②와 ④가 **같은 URL**이다. 읽은 목록을 사용자가 고친 그대로 다시 보내면 되고, 그래서
삭제 API가 따로 없다.

**문서 상태는 셋이다.**

| status | 뜻 |
|---|---|
| `missing` | 문장이 하나도 없다. **벡터 검색에서 절대 안 걸린다** |
| `outdated` | 본문이 바뀐 뒤 문장을 손보지 않았다. 옛 내용 기준으로 걸린다 |
| `ok` | 지금 본문 기준의 문장이 있다 |

낡음의 기준은 `search_units.source_hash`다 — 그 문장을 쓸 때 본 본문의 해시를 행에 새겨
두고 `answer_units.source_hash`와 비교한다. 목록은 손볼 것이 위로 오도록
`missing → outdated → ok` 순으로 정렬한다.

**`view_types`는 고정 개수가 아니다.** 지금 데이터가 문서당 `hypo_q` 1~2 + `real_q` 1 +
`contextual` 1로 균일할 뿐, 한 유형에 문장을 여러 개 달 수 있다. 그래서 응답은 고정 필드가
아니라 `{"hypo_q": 2, "real_q": 1, "contextual": 1}` 형태의 맵이다. 유형을 늘리려면
[domain/search_chunk.py](../domain/search_chunk.py)의 `VIEW_TYPES`에 추가한다 — API 검증과
초안 프롬프트가 그 목록을 본다.

```jsonc
// ④ PUT /api/v1/search-units/coverage/guide/소개
//    보낸 목록이 곧 그 문서의 문장 전체다
{
  "items": [
    { "view_type": "hypo_q",     "text": "뤼이도의 핵심 기능은 무엇인가요?" },
    { "view_type": "real_q",     "text": "뤼이도 핵심 기능 좀 알려줘" }
    // 여기서 뺀 문장은 삭제된다
  ]
}
// 200 — 저장 후 그 문서의 문장 전체와 상태(status/units/view_types)를 돌려준다
```

| 보낸 목록에서 | 결과 |
|---|---|
| 빠진 문장 | **삭제**된다. 빈 배열이면 그 문서의 문장이 전부 사라진다(`status`가 `missing`이 된다) |
| 그대로인 문장 | 임베딩을 다시 만들지 않는다. `id`도 그대로다 — 유형만 고친 경우가 여기 걸린다 |
| 새 문장 | 그 자리에서 임베딩해 넣는다 |

삭제와 저장은 **한 트랜잭션**이라 중간에 실패해도 문장이 반쯤 지워진 채 남지 않는다.
**저장하면 곧바로 검색된다** — 재빌드를 기다릴 필요가 없다.

**저장한 문장은 다음 빌드가 지우지 않는다.** `build_search_units`의 정리(prune)는 JSON이
기준이라, 그대로 두면 콘솔에서 넣은 문장이 다음 빌드에 사라진다. 그래서 `search_units.source`로
출처를 구분하고(`file` / `console`) 정리 대상은 `file`뿐이다. 저장한 문서의 문장은 전부
`console`이 된다 — 사람이 한 번 손본 문서는 그 사람이 주인이라는 뜻이다.

**단, JSON에 있는 문장을 지웠다면 내보내기(⑤)까지 해야 한다.** 정리(prune)는 파일에 없는
것을 지우는 일이지 파일에 있는 것을 안 넣는 일이 아니라서, 파일이 그 문장을 그대로 갖고
있으면 다음 빌드가 되살린다.

### GET /api/v1/search-units/export

DB의 검색 문장 전체를 [data/rag_view_sentences.json](../data/rag_view_sentences.json)과
**같은 모양·같은 순서**로 내보낸다. 받은 내용으로 그 파일을 덮어쓰고 커밋하면 콘솔 작업이
저장소에 남는다.

```bash
curl -s localhost:8000/api/v1/search-units/export -o data/rag_view_sentences.json
git diff data/rag_view_sentences.json     # 콘솔에서 손댄 것만 뜬다
```

**바뀐 것이 없으면 diff도 없다.** 직렬화(2칸 들여쓰기, 한글 그대로)와 정렬(`doc_id` →
`view_type` → 적재 순)을 파일과 맞춰 두었고, 지금 DB로 내보내면 커밋된 파일과 바이트까지
같다. 그래서 diff에 뜨는 것이 곧 이번 작업 내용이다.

이 단계가 있어야 세 가지가 닫힌다.

| | 내보내기가 없으면 | 있으면 |
|---|---|---|
| 콘솔에서 추가한 문장 | 이 DB에만 있다. 다른 개발자·새 환경에는 없다 | 파일에 담겨 모두에게 전달된다 |
| 파일 문장을 지운 것 | 다음 빌드가 되살린다 | 파일에서도 빠져 영구히 반영된다 |
| 작업 이력 | 없다 | git 히스토리에 남는다 |

**초안(③)은 저장하지 않는다.** 입력창에 채워 넣을 값을 돌려줄 뿐이고, 사람이 고른 것만 ④로
보낸다 — 빈 칸에서 시작하면 아무도 채우지 않기 때문에 있는 API지, LLM에게 인덱스를 맡기려는
것이 아니다. 이미 등록된 문장을 프롬프트에 함께 넣어 겹치는 초안을 피한다.



### 사용자 피드백 대조 — GET /api/v1/feedback

백엔드가 쌓는 좋아요/싫어요(`app.message_feedbacks`)와 이쪽 판정자 점수를 **`qna_uuid`로
맞춰** 한 줄에 놓는다. 자동 채점이 사람의 판단과 얼마나 맞는지 보는 화면이다.

**백엔드 스키마를 읽는 유일한 곳이다.** 같은 데이터베이스의 다른 스키마라 `DATABASE_URL`은
그대로이고, `app.message_feedbacks`처럼 스키마를 명시해 **읽기만** 한다 — 그 스키마의 주인은
백엔드이므로 여기서 CREATE/ALTER/DROP을 하지 않는다. 뷰를 만들지 않은 것도 같은 이유다:
뷰는 카탈로그에 의존성을 남겨 백엔드가 그 테이블을 고칠 때 그쪽 마이그레이션을 막을 수 있다.
SQL은 [feedback_repository.py](repositories/feedback_repository.py) 한 곳에만 둔다.

`/ask` 경로에서는 부르지 않는다. 답변 도중에 남의 테이블을 읽으면 그쪽 장애가 답변 실패가
된다 — 이 화면은 답변과 무관한 조회다.

| agreement | 뜻 |
|---|---|
| `match` | 사용자와 판정자가 같은 방향 (GOOD↔pass, BAD↔fail) |
| `mismatch` | 엇갈림. **`rating=BAD` + `verdict=pass`가 프롬프트를 고칠 1순위 표본이다** |
| `unevaluated` | 아직 채점되지 않은 턴. `POST /evaluations/run`으로 돌리면 대조에 들어온다 |

```jsonc
// GET /api/v1/feedback?agreement=mismatch&rating=BAD
{
  "qna_uuid": "2e964097-…",
  "message_id": 36,
  "rating": "BAD", "reason": "BROKEN_LINK",       // 사용자가 고른 항목
  "raw_query": "그럼 권한은 어떻게 바꿔?",
  "verdict": "fail", "issues": ["insufficient"],  // 판정자
  "faithfulness": 1.0, "answer_relevance": 0.6, "context_relevance": 0.8,
  "agreement": "match"
}
```

단건(`/feedback/{qna_uuid}`)은 여기에 **답변 본문과 검색된 문서 id**를 더 준다 — 사용자가 왜
그렇게 눌렀는지 되짚으려면 그 두 개가 필요하다. 피드백이 없는 턴이면 404이고, 채점 결과만
보려면 `GET /evaluations/{qna_uuid}`를 쓴다.

**피드백은 있는데 우리 로그가 없는 행도 준다.** 백엔드가 `qna_uuid`를 저장하기 전의
메시지들이 그렇다(지금 ASSISTANT 메시지 18건 중 2건만 `qna_uuid`가 있다). 그런 행은
`raw_query`가 null이고 `agreement`는 `unevaluated`다 — 조용히 빠지면 피드백 수가 맞지 않는다.

**두 어휘는 정의역이 다르다.** 사용자의 `reason`(12종)과 판정자의 `issues`(4종)는 겹치는
부분만 대응된다. 특히 `BROKEN_LINK`는 **판정자가 낼 수 없는 항목이다** — 텍스트만 보고
링크가 살아 있는지 알 수 없기 때문이다(→ [core/prompts.py](../core/prompts.py)의
`EVAL_ISSUE_CODES` 주석). 그런 불만은 채점이 아니라 빌드 때 링크를 검사해 잡을 일이다.

**조인 키의 타입이 다르다.** 백엔드는 `varchar`, 이쪽은 `uuid`라 문자열로 맞춰 조인한다
(`l.qna_uuid::text = f.qna_uuid`). `f.qna_uuid::uuid`로 캐스팅하면 값이 uuid 형식이 아닌
행 하나에 쿼리 전체가 죽는다. 백엔드가 `uuid`로 바꾸면 이 캐스팅은 없앨 수 있다.

이 DB에 `app` 스키마가 없으면(백엔드를 함께 띄우지 않은 개발 환경) 503과 함께 그 사실을
알려준다. 다른 API는 영향을 받지 않는다.

연결 상태는 `GET /health`의 `backend` 필드로도 보인다.

```jsonc
"backend": { "table": "app.message_feedbacks", "available": true, "rows": 1 }
```

**이 값은 `status` 판정에 넣지 않는다.** 백엔드 스키마가 없어도 답변·검색·채점은 모두
정상이고 `/feedback` 하나만 못 쓴다. 남의 스키마 때문에 이 서비스가 `degraded`로 뜨면
헬스체크를 로드밸런서나 알림에 물릴 수 없다.

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
  [core/evaluation.py](../core/evaluation.py)는 `EvaluationError`를 올린다. 평가는 응답을 보낸 뒤에
  도는 일이라 요청에는 영향이 없고, 실패하면 **아무것도 저장하지 않는다** — 실패를 0.0으로 채우면
  "완전한 환각" 판정과 값이 같아지고, 실패한 행을 남기면 "아직 평가 안 함"과 구분되지 않는다.
  행이 없어야 미평가로 다시 잡혀 나중에 재실행할 수 있다.
- **평가를 응답 뒤에 두는 이유**: 채점에 LLM 1회가 더 들어 응답 경로에 두면 그만큼 늦어진다.
  `BackgroundTasks`로 응답을 보낸 다음에 돌린다. 대신 **로그 행은 응답 전에 동기로** 남긴다 —
  프로세스가 죽어 채점을 놓쳐도 행이 있어야 나중에 다시 돌릴 대상을 찾을 수 있다.
  로그 저장이 실패해도 답변은 그대로 나가고(답변이 로그보다 중요하다), 그 상황은 `/health`가 알린다.
- **부팅 비용**: `core.search`는 로드 시점에 Kiwi와 임베딩 클라이언트를 만든다. 첫 요청이 이 비용을
  떠안지 않도록 `lifespan`에서 `core.search.warmup()`을 부른다. 그 import를 파일 최상단으로
  올리면 안 된다 — 로드 비용이 부팅 전으로 앞당겨져 lifespan이 재는 시간이 0이 된다.

## 추가로 제안하는 API

구현하지 않았다. 스키마나 테이블 결정이 필요해서다.

| 제안 | 이유 |
|---|---|
| 채점 작업 상태 조회 | `POST /evaluations/run`은 202만 주고 끝난다. 지금 몇 건이 돌고 있고 무엇이 실패했는지는 `GET /qna?status=pending`이 줄어드는 것으로 간접 확인한다. 작업 이력을 남기려면 테이블이 필요하다 |
| 관리자 인증 | 이 서버의 조회·재실행 API는 사용자 질문 원문을 그대로 노출하고 재실행은 LLM 비용을 쓴다. 운영 콘솔이 백엔드를 거치지 않고 직접 붙는 구조라 인증이 이 서버에 있어야 한다 |
| `POST /admin/reindex` — 인덱스 재빌드 트리거 | 지금은 `GET /index-status`로 낡은 것을 확인하고 서버에서 스크립트를 돌려야 한다. 수 분 걸리는 작업이라 202를 먼저 주고(`BackgroundTasks`) 진행 상황은 `index-status`가 줄어드는 것으로 보는 형태가 맞다. 동시 실행 방지(`pg_advisory_lock`)가 필요하다 — 두 번 누르면 임베딩 비용이 두 배로 나간다 |
| `GET /ask/stream` — 답변 토큰 스트리밍 | `/ask`는 LLM 2~3회 + 임베딩 1회라 체감 지연이 크다. SSE로 답변을 흘려보내면 개선된다. `core/generation.py`가 `stream=True`를 지원하도록 바뀌어야 한다 |
