"""
rag_search.py — 검색 모듈

- 검색 대상 DB: search_units (가설질문/실제질문/맥락요약 문장 단위)
- 하이브리드 검색: 벡터 + Kiwi 기반 키워드 검색을 RRF로 결합
- 1단계: 거리가 가까운 top-n 검색 단위(text, doc_id, view_type)를 뽑는다
- 2단계: 그 doc_id로 answer_units에서 답변 본문을 가져온다
- 임베딩: OpenAI text-embedding-3-small
"""

import re
from typing import Dict, List

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

    search_units는 문장 단위라 plainto_tsquery(모든 키워드 AND)로는 거의 매칭되지 않는다.
    OR로 묶어 일부만 겹쳐도 후보에 올리고, 순위는 ts_rank(많이·자주 겹칠수록 높음)에 맡긴다.
    """
    terms = []
    for term in keywords.split():
        term = re.sub(r"[^\w가-힣]", "", term)  # tsquery 문법 문자(&|!:*()') 제거
        if term and term not in QUERY_STOPWORDS and term not in terms:
            terms.append(term)
    return " | ".join(f"'{t}'" for t in terms)


# ---------------------------------------------------------------------------
# 1) 검색 (search_units)
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


def keyword_search(query: str, top_k: int = 20):
    tsquery = build_tsquery(extract_keywords(query))
    if not tsquery:  # 명사·동사·형용사가 하나도 안 나온 질문
        return []

    with get_cursor() as cur:
        cur.execute("""
            SELECT id, doc_id, view_type, text,
                   ts_rank(text_tsv, to_tsquery('simple', %s)) AS rank
            FROM search_units WHERE text_tsv @@ to_tsquery('simple', %s)
            ORDER BY rank DESC LIMIT %s
        """, (tsquery, tsquery, top_k))
        return cur.fetchall()


def reciprocal_rank_fusion(vector_results, keyword_results, k: int = 30, vector_weight: float = 0.5):
    keyword_weight = 1 - vector_weight
    hit_map: Dict[int, SearchHit] = {}

    for rank, row in enumerate(vector_results):
        hit_id = row["id"]

        if hit_id not in hit_map:
            hit_map[hit_id] = SearchHit(
                id=hit_id,
                doc_id=row["doc_id"],
                view_type=row["view_type"],
                text=row["text"],
                v_similarity=row.get("similarity", 0.0),
            )
        else:
            hit_map[hit_id].v_similarity = row.get("similarity", 0.0)
        hit_map[hit_id].rrf_score += vector_weight * (1 / (k + rank + 1))

    for rank, row in enumerate(keyword_results):
        hit_id = row["id"]

        if hit_id not in hit_map:
            hit_map[hit_id] = SearchHit(
                id=hit_id,
                doc_id=row["doc_id"],
                view_type=row["view_type"],
                text=row["text"],
                k_similarity=row.get("rank", 0.0),
            )
        else:
            hit_map[hit_id].k_similarity = row.get("rank", 0.0)
        hit_map[hit_id].rrf_score += keyword_weight * (1 / (k + rank + 1))

    return sorted(hit_map.values(), key=lambda x: x.rrf_score, reverse=True)


def search_units(query: str, top_k: int = 5, vector_weight: float = 0.5) -> List[SearchHit]:
    """거리가 가까운 top-k 검색 단위(text, doc_id, view_type)를 반환"""
    v_results = vector_search(query, top_k=20)
    k_results = keyword_search(query, top_k=20)
    return reciprocal_rank_fusion(v_results, k_results, vector_weight=vector_weight)[:top_k]


# ---------------------------------------------------------------------------
# 2) 답변 본문 (answer_units)
# ---------------------------------------------------------------------------

def fetch_answer_units(hits: List[SearchHit]) -> List[RetrievedChunk]:
    """검색된 doc_id로 answer_units 본문을 가져온다. 검색 순위 순서를 유지한다."""
    if not hits:
        return []

    ordered_doc_ids: List[str] = []
    for hit in hits:
        if hit.doc_id not in ordered_doc_ids:
            ordered_doc_ids.append(hit.doc_id)

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
    """검색 단위 top-k와 그에 대응하는 answer_units 문서를 함께 반환"""
    hits = search_units(query, top_k=top_k, vector_weight=vector_weight)
    docs = fetch_answer_units(hits)
    return hits, docs


# ---------------------------------------------------------------------------
# 3) 출력
# ---------------------------------------------------------------------------

def print_result(query: str, hits: List[SearchHit], docs: List[RetrievedChunk], content_chars: int = 300) -> None:
    print(f"\n[질문] {query}")

    print(f"\n── 검색 단위 top {len(hits)} (search_units) ──")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. [{h.view_type}] doc_id={h.doc_id} "
              f"(similarity={h.v_similarity:.4f}, ts_rank={h.k_similarity:.4f}, rrf={h.rrf_score:.4f})")
        print(f"     text: {h.text}")

    print(f"\n── 매칭 문서 {len(docs)}건 (answer_units) ──")
    for i, d in enumerate(docs, 1):
        view_types = ", ".join(h.view_type for h in d.hits)
        best_rrf = max((h.rrf_score for h in d.hits), default=0.0)
        print(f"  {i}. doc_id={d.doc_id} [{d.source_type}] [{d.section}] "
              f"(matched={view_types}, best_rrf={best_rrf:.4f})")
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
