# Riido RAG 챗봇 - 검색 인프라 (A파트)
`search(질문)` 함수 하나만 쓰면 됨. 이용가이드 + 실제 고객 QA에서 관련 문서를 하이브리드 검색(벡터+키워드)으로 찾아서 반환.
```python
from search import search
result = search("팀을 삭제하면 어떻게 돼?")
```
## 반환 형태
```json
{
  "documents": [
    {
      "title": "팀",
      "content": "팀을 삭제하면 해당 팀과 관련된 모든 데이터가...",
      "section": "팀 > 팀 관리",
      "rrf_score": 0.0328,
      "similarity": 0.6894,
      "type": "guide"
    }
  ]
}
```
## 필드 설명
| 필드 | 뭘 담고 있나 | 어디에 쓰면 되는지 |
|---|---|---|
| `title` | 어느 문서에서 왔는지 | 근거 표시 |
| `content` | 실제 텍스트 내용 | **LLM 프롬프트 재료** |
| `section` | 문서 안 위치 (예: "팀 > 팀 관리") | 근거 상세 표시 (URL 대신 이 텍스트로 출처 표시하기로 결정함) |
| `rrf_score` | 정렬용 점수 | 참고용, 절대적 의미 없음 |
| `similarity` | 순수 벡터 유사도 (0~1) | **"근거 부족 시 답변 거부"** 판단용. 키워드로만 잡힌 문서는 `None`일 수 있음 |
| `type` | `guide` / `support_qa` | 출처 구분 표시하고 싶으면 사용 |
## 예시 코드 — 근거 부족 판단
```python
result = search(question)
top_doc = result["documents"][0] if result["documents"] else None
if not top_doc or top_doc["similarity"] is None or top_doc["similarity"] < 0.6:
    answer = "관련된 내용을 찾지 못했습니다. 운영자에게 문의해주세요."
else:
    context = "\n\n".join(d["content"] for d in result["documents"])
    # LLM한테 context 넘겨서 답변 생성
```
## 검색 로직 (간단히)
```
벡터 검색(의미 기반) 20개 + 키워드 검색(정확한 단어) 20개
→ RRF로 순위 합쳐서 통합 정렬 → 상위 top_k개 반환
```
한국어는 조사 때문에 단순 검색이 잘 안 돼서, Kiwi(형태소 분석기)로 조사 떼고 핵심 단어만 뽑아서 키워드 검색에 사용.
## 데이터 현황
- 이용가이드 207개 + 실제 고객 QA 95개 = 총 302개 청크
## 같이 정해야 할 것
1. `similarity` 임계값 0.6이 적당한지
2. `similarity`가 `None`인 문서 처리 방식
## 사전 준비
```bash
pip install psycopg2-binary kiwipiepy
```
`.env`에 `DATABASE_URL`, `GOOGLE_API_KEY` 설정 필요. PostgreSQL + pgvector 실행 중이어야 함.
