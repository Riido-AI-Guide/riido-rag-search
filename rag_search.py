"""
rag_search.py — 검색 모듈 (분리혼합 하이브리드)

- 벡터 검색: search_units (가설질문/실제질문/맥락요약 문장 단위)
- 키워드 검색: answer_content_vectors (가이드 원문 단위)
  질문 문장은 담긴 단어가 적어 키워드 매칭이 빈약하지만, 원문은 단어가
  풍부해 잘 걸린다. 2차 평가에서 이 조합이 전 지표 우위(evaluate_search_split.py).
- 두 순위를 문서 단위 RRF로 결합해 top-k 문서를 뽑고,
  answer_units에서 답변 본문을 가져온다
- 임베딩: OpenAI text-embedding-3-small
"""

import re
from typing import Dict, List

import psycopg2.errors
from langchain_openai import OpenAIEmbeddings
from kiwipiepy import Kiwi

from db import get_cursor
from dto import RetrievedChunk, SearchHit

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
kiwi = Kiwi()


def extract_keywords(text: str) -> str:
    tokens = kiwi.tokenize(text)
    keywords = [t.form for t in tokens if t.tag.startswith(("NN", "VV", "VA"))]
    return " ".join(keywords)


# 질문에 흔히 섞이는 기능어 어간. 인덱스에는 남겨두고 질의에서만 뺀다
# ("어떻게 해?"의 '어떻'·'하'가 OR에 들어가면 무관한 문장이 대량으로 딸려온다)
QUERY_STOPWORDS = {
    "하", "되", "있", "없", "수", "것", "거", "등", "때", "좀",
    "어떻", "어떠", "같", "이", "그", "저", "무엇", "뭐",
}


def build_tsquery(keywords: str) -> str:
    """키워드를 OR(|)로 묶은 tsquery 문자열.

    OR로 묶어 일부만 겹쳐도 후보에 올리고, 순위는 ts_rank(많이·자주 겹칠수록 높음)에 맡긴다.
    """
    terms = []
    for term in keywords.split():
        term = re.sub(r"[^\w가-힣]", "", term)  # tsquery 문법 문자(&|!:*()') 제거
        if term and term not in QUERY_STOPWORDS and term not in terms:
            terms.append(term)
    return " | ".join(f"'{t}'" for t in terms)


# ---------------------------------------------------------------------------
# 1) 벡터 검색 — search_units (질문형 문장 인덱스)
# ---------------------------------------------------------------------------

def vector_search(query: str, top_k: int = 20):
    # 임베딩(외부 API 호출)을 끝낸 뒤에 커넥션을 빌린다.
    # 반대로 하면 네트워크 대기 동안 풀 커넥션을 붙잡고 있게 된다.
    query_vector = embeddings.embed_query(query)

    with get_cursor() as cur:
        cur.execute("""
            SELECT id, doc_id, view_type, text,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM search_units ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(query_vector), str(query_vector), top_k))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# 2) 키워드 검색 — answer_content_vectors (가이드 원문 인덱스)
# ---------------------------------------------------------------------------

def content_keyword_search(query: str, top_k: int = 20):
    """가이드 원문에 대한 키워드 검색 → 문서 순위 [{doc_id, rank}]"""
    tsquery = build_tsquery(extract_keywords(query))
    if not tsquery:  # 명사·동사·형용사가 하나도 안 나온 질문
        return []

    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT doc_id,
                       ts_rank(text_tsv, to_tsquery('simple', %s)) AS rank
                FROM answer_content_vectors
                WHERE text_tsv @@ to_tsquery('simple', %s)
                ORDER BY rank DESC LIMIT %s
            """, (tsquery, tsquery, top_k))
            return cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        # 원문 인덱스가 아직 없으면 벡터 검색만으로 동작 (search_builder.py 실행 필요)
        print("⚠️  answer_content_vectors 없음 — search_builder.py를 실행해 원문 인덱스를 구축하세요")
        return []


# ---------------------------------------------------------------------------
# 3) 문서 단위 RRF 결합
# ---------------------------------------------------------------------------

def doc_rrf_scores(vector_results, keyword_results,
                   k: int = 30, vector_weight: float = 0.5) -> Dict[str, float]:
    """벡터(문장→문서 첫 등장 순)와 키워드(문서) 순위를 문서 단위 RRF 점수로 결합"""
    keyword_weight = 1 - vector_weight
    scores: Dict[str, float] = {}

    vec_doc_order: List[str] = []
    for row in vector_results:
        if row["doc_id"] not in vec_doc_order:
            vec_doc_order.append(row["doc_id"])
    for rank, doc_id in enumerate(vec_doc_order):
        scores[doc_id] = scores.get(doc_id, 0.0) + vector_weight * (1 / (k + rank + 1))

    for rank, row in enumerate(keyword_results):
        doc_id = row["doc_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + keyword_weight * (1 / (k + rank + 1))

    return scores


def build_hits(vector_results, keyword_results,
               ordered_doc_ids: List[str], doc_scores: Dict[str, float]) -> List[SearchHit]:
    """최종 문서 순위에 속한 벡터 문장 매칭들을 SearchHit으로 정리 (표시·디버깅용)"""
    kw_rank_by_doc = {row["doc_id"]: float(row["rank"]) for row in keyword_results}
    doc_pos = {d: i for i, d in enumerate(ordered_doc_ids)}

    hits: List[SearchHit] = []
    seen_ids = set()
    for row in vector_results:
        if row["doc_id"] not in doc_pos or row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        hits.append(SearchHit(
            id=row["id"],
            doc_id=row["doc_id"],
            view_type=row["view_type"],
            text=row["text"],
            v_similarity=row.get("similarity", 0.0),
            k_similarity=kw_rank_by_doc.get(row["doc_id"], 0.0),
            rrf_score=doc_scores.get(row["doc_id"], 0.0),
        ))
    hits.sort(key=lambda h: (doc_pos[h.doc_id], -h.v_similarity))
    return hits


# ---------------------------------------------------------------------------
# 4) 답변 본문 (answer_units)
# ---------------------------------------------------------------------------

def fetch_answer_units(ordered_doc_ids: List[str], hits: List[SearchHit]) -> List[RetrievedChunk]:
    """문서 순위 순서대로 answer_units 본문을 가져온다."""
    if not ordered_doc_ids:
        return []

    with get_cursor() as cur:
        cur.execute("""
            SELECT doc_id, title, section, source_type, content, ord_idx
            FROM answer_units WHERE doc_id = ANY(%s)
        """, (ordered_doc_ids,))
        rows = {row["doc_id"]: row for row in cur.fetchall()}

    docs: List[RetrievedChunk] = []
    for doc_id in ordered_doc_ids:
        row = rows.get(doc_id)
        if row is None:  # answer_units에서 지워진 문서 (FK CASCADE 전 상태 등)
            continue

        docs.append(RetrievedChunk(
            doc_id=row["doc_id"],
            title=row["title"],
            section=row["section"],
            source_type=row["source_type"],
            content=row["content"],
            ord_idx=row["ord_idx"],
            hits=[h for h in hits if h.doc_id == doc_id],  # 점수는 SearchHit에만 둔다
        ))

    return docs


def search(query: str, top_k: int = 5, vector_weight: float = 0.5):
    """분리혼합 검색: 벡터(질문 문장)+키워드(원문)를 문서 단위로 결합해 top-k 문서 반환"""
    v_results = vector_search(query, top_k=20)
    k_results = content_keyword_search(query, top_k=20)

    scores = doc_rrf_scores(v_results, k_results, vector_weight=vector_weight)
    ordered_doc_ids = sorted(scores, key=lambda d: scores[d], reverse=True)[:top_k]
    hits = build_hits(v_results, k_results, ordered_doc_ids, scores)
    docs = fetch_answer_units(ordered_doc_ids, hits)
    return hits, docs


# ---------------------------------------------------------------------------
# 5) 출력
# ---------------------------------------------------------------------------

def print_result(query: str, hits: List[SearchHit], docs: List[RetrievedChunk], content_chars: int = 300) -> None:
    print(f"\n[질문] {query}")

    print(f"\n── 벡터 매칭 문장 {len(hits)}건 (search_units) ──")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. [{h.view_type}] doc_id={h.doc_id} "
              f"(similarity={h.v_similarity:.4f}, kw_rank={h.k_similarity:.4f}, doc_rrf={h.rrf_score:.4f})")
        print(f"     text: {h.text}")

    print(f"\n── 매칭 문서 {len(docs)}건 (answer_units) ──")
    for i, d in enumerate(docs, 1):
        view_types = ", ".join(h.view_type for h in d.hits) or "keyword-only"
        best_rrf = max((h.rrf_score for h in d.hits), default=0.0)
        print(f"  {i}. doc_id={d.doc_id} [{d.source_type}] [{d.section}] "
              f"(matched={view_types}, doc_rrf={best_rrf:.4f})")
        print(f"     {d.content[:content_chars]}...")
    print("-" * 60)


if __name__ == "__main__":
    test_queries = [
        "팀원을 어떻게 추가해?",
        "팀을 삭제하면 어떻게 돼?",
        "스프린트 기간은 최대 몇 주까지 설정할 수 있어?",
        "학생이면 뤼이도 유료 요금제 무료로 쓸 수 있어?",
        "PR 연동 문제 해결하는 방법",
        "회원 탈퇴 어떻게 해?",
    ]

    for q in test_queries:
        hits, docs = search(q, top_k=5)
        print_result(q, hits, docs)
