"""
evaluate_search_split.py — 검색 평가 본체

golden_set.json(질문 → 정답 doc_id)으로 검색 품질을 Hit@1 / Hit@3 / MRR로 채점한다.

비교 대상 (한 번 실행에 4가지 모두 채점):
  [저장 구조 비교] ← 1부의 핵심 질문 "두 저장소로 나눈 설계가 효과 있는가"
    A-cleaned : 현행 방식. search_units(가설질문·실제질문·요약 문장)를 하이브리드 검색
    B-cleaned : 비교 방식. 원문(answer_units.content)을 같은 하이브리드로 직접 검색
  [검색 모드 실험] ← search_queries 처리 결정용 (팀 회의 판정 기준: Hit@3 ±3%p)
    A-multi   : query_transform의 변형 검색어들로 각각 검색 후 RRF 합산
    A-raw     : 정제 없이 원질문 그대로 검색 (query_transform 효과 검증)

공정성 통제:
- 임베딩 모델 동일 (text-embedding-3-small, rag_search와 같은 인스턴스 재사용)
- 하이브리드 로직 동일 (벡터 + Kiwi 키워드 + RRF — rag_search 함수 재사용)
- 검색 입력 동일 (A/B 모두 같은 정제 질문. transform 결과는 파일 캐시)
- 채점 규칙 동일 (doc_id 기준 첫 등장 순으로 접어 문서 단위 순위)

출력:
- 콘솔: 모드×(전체/real/synthetic) 요약 표
- eval_results.csv: 질문별 상세 (각 모드에서 정답이 몇 등이었는지) — 실패 사례 분석용

실행: python evaluate_search_split.py
      (최초 실행 시 answer_content_vectors 테이블 생성 + 원문 임베딩에 몇 분 소요)
"""

import os
import csv
import json
import time
from typing import Dict, List, Optional

import psycopg2.extras

# 프로덕션 검색 코드를 그대로 재사용한다 — "평가한 것 = 실제 시스템"을 보장
from rag_search import (
    embeddings, get_connection, extract_keywords, build_tsquery,
    vector_search, keyword_search, reciprocal_rank_fusion, search_units,
)
from query_transform import transform_user_query

GOLDEN_PATH = "./golden_set.json"
TRANSFORM_CACHE_PATH = "./eval_transform_cache.json"
RESULTS_CSV_PATH = "./eval_results.csv"

TOP_K = 10           # 이 순위까지 정답을 찾는다 (MRR 계산 범위)
UNIT_POOL = 30       # 문서로 접기 전 가져올 검색 단위 수 (중복 접힘 대비 여유분)
EMBED_BATCH = 50
EMBED_SLEEP_SEC = 3
CONTENT_EMBED_CHARS = 8000  # 임베딩 입력 안전 상한 (모델 한도 초과 방지)


# ---------------------------------------------------------------------------
# 1) 방식 B 준비 — 원문 직접 검색용 테이블 (비교 실험 전용)
# ---------------------------------------------------------------------------

def setup_content_table(dim: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS answer_content_vectors (
            doc_id    TEXT PRIMARY KEY REFERENCES answer_units(doc_id) ON DELETE CASCADE,
            text_tsv  TSVECTOR,
            embedding VECTOR({dim})
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acv_tsv ON answer_content_vectors USING GIN (text_tsv);")
    conn.commit()
    cur.close()
    conn.close()


def build_content_vectors() -> None:
    """answer_units 원문을 임베딩해서 채운다. 이미 된 문서는 건너뛴다(증분)."""
    dim = len(embeddings.embed_query("차원 확인"))
    setup_content_table(dim)

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.doc_id, a.content
        FROM answer_units a
        LEFT JOIN answer_content_vectors v ON v.doc_id = a.doc_id
        WHERE v.doc_id IS NULL
        ORDER BY a.doc_id;
    """)
    todo = cur.fetchall()
    if not todo:
        print("✅ 방식 B 테이블(answer_content_vectors) 준비 완료 (이미 구축됨)")
        cur.close(); conn.close()
        return

    print(f"🔨 방식 B 테이블 구축: 원문 {len(todo)}개 임베딩 (몇 분 걸릴 수 있음)")
    for i in range(0, len(todo), EMBED_BATCH):
        batch = todo[i:i + EMBED_BATCH]
        vectors = embeddings.embed_documents([r["content"][:CONTENT_EMBED_CHARS] for r in batch])
        for row, vec in zip(batch, vectors):
            cur.execute("""
                INSERT INTO answer_content_vectors (doc_id, text_tsv, embedding)
                VALUES (%s, to_tsvector('simple', %s), %s::vector)
                ON CONFLICT (doc_id) DO NOTHING;
            """, (row["doc_id"], extract_keywords(row["content"]), str(vec)))
        conn.commit()
        print(f"  → {min(i + EMBED_BATCH, len(todo))}/{len(todo)}")
        if i + EMBED_BATCH < len(todo):
            time.sleep(EMBED_SLEEP_SEC)
    cur.close()
    conn.close()


def _content_vector_search(query: str, top_k: int = 20):
    qv = embeddings.embed_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT doc_id AS id, doc_id, 'content' AS view_type, '' AS text,
               1 - (embedding <=> %s::vector) AS similarity
        FROM answer_content_vectors ORDER BY embedding <=> %s::vector LIMIT %s
    """, (str(qv), str(qv), top_k))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def _content_keyword_search(query: str, top_k: int = 20):
    tsq = build_tsquery(extract_keywords(query))
    if not tsq:
        return []
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT doc_id AS id, doc_id, 'content' AS view_type, '' AS text,
               ts_rank(text_tsv, to_tsquery('simple', %s)) AS rank
        FROM answer_content_vectors WHERE text_tsv @@ to_tsquery('simple', %s)
        ORDER BY rank DESC LIMIT %s
    """, (tsq, tsq, top_k))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def search_content(query: str) -> List[str]:
    """방식 B: 원문 하이브리드 검색 → 문서 순위 (A와 동일한 RRF 로직 재사용)"""
    fused = reciprocal_rank_fusion(
        _content_vector_search(query), _content_keyword_search(query))
    return [h.doc_id for h in fused][:TOP_K]  # 문서당 1행이라 중복 없음


# ---------------------------------------------------------------------------
# 2) 방식 A 검색 모드들 — 결과를 "문서 순위 리스트"로 통일
# ---------------------------------------------------------------------------

def _dedupe_docs(hits) -> List[str]:
    """검색 단위 순위를 doc_id 첫 등장 순으로 접는다 (fetch_answer_units와 같은 규칙)"""
    docs: List[str] = []
    for h in hits:
        if h.doc_id not in docs:
            docs.append(h.doc_id)
    return docs[:TOP_K]


def search_a_single(query: str) -> List[str]:
    """현행 방식: search_units 하이브리드 검색 1회"""
    return _dedupe_docs(search_units(query, top_k=UNIT_POOL))


def search_a_multi(queries: List[str]) -> List[str]:
    """멀티쿼리: 변형 검색어별로 검색한 뒤 변형 간 RRF로 합산"""
    scores: Dict[int, float] = {}
    doc_of: Dict[int, str] = {}
    for q in queries:
        fused = reciprocal_rank_fusion(vector_search(q, 20), keyword_search(q, 20))
        for rank, hit in enumerate(fused[:UNIT_POOL]):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (60 + rank + 1)
            doc_of[hit.id] = hit.doc_id
    ranked_units = sorted(scores, key=lambda uid: scores[uid], reverse=True)
    docs: List[str] = []
    for uid in ranked_units:
        if doc_of[uid] not in docs:
            docs.append(doc_of[uid])
    return docs[:TOP_K]


# ---------------------------------------------------------------------------
# 3) 질문 정제 캐시 — LLM 호출을 질문당 1회로
# ---------------------------------------------------------------------------

def load_transforms(golden: List[Dict]) -> Dict[str, Dict]:
    cache: Dict[str, Dict] = {}
    if os.path.exists(TRANSFORM_CACHE_PATH):
        with open(TRANSFORM_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)

    todo = [g for g in golden if g["sample_id"] not in cache]
    if todo:
        print(f"🔄 질문 정제(query_transform): {len(todo)}개 (캐시됨 {len(cache)}개)")
    for i, g in enumerate(todo, 1):
        t = transform_user_query(g["query"])
        variants = [t.cleaned_query] + [q for q in t.search_queries if q != t.cleaned_query]
        cache[g["sample_id"]] = {"cleaned": t.cleaned_query, "variants": variants[:3]}
        with open(TRANSFORM_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)  # 매번 저장 — 중단 후 재개 가능
        if i % 20 == 0:
            print(f"  → {i}/{len(todo)}")
    return cache


# ---------------------------------------------------------------------------
# 4) 채점
# ---------------------------------------------------------------------------

def is_correct(golden_doc_id: str, doc_id: str) -> bool:
    """문서 계층을 고려한 정답 판정.

    문서가 부모/자식 계층(guide/팀 ↔ guide/팀/개요)으로 나뉘어 있어서
    정답의 같은 가족 문서를 찾아와도 내용상 정답인 경우가 많다.
    (실측: 완전일치 채점에서는 synthetic 실패 48건 중 14건이 이 유형이었다)
    """
    return (
        doc_id == golden_doc_id
        or doc_id.startswith(golden_doc_id + "/")   # 정답의 자식을 찾아옴
        or golden_doc_id.startswith(doc_id + "/")   # 정답의 부모를 찾아옴
    )


def rank_of(golden_doc_id: str, docs: List[str]) -> Optional[int]:
    """정답(가족 포함)이 처음 등장하는 순위 (1부터). TOP_K 안에 없으면 None."""
    for i, d in enumerate(docs):
        if is_correct(golden_doc_id, d):
            return i + 1
    return None


class Scorer:
    def __init__(self):
        self.ranks: List[Optional[int]] = []

    def add(self, rank: Optional[int]):
        self.ranks.append(rank)

    def summary(self) -> Dict[str, float]:
        n = len(self.ranks)
        if n == 0:
            return {"n": 0, "hit@1": 0.0, "hit@3": 0.0, "mrr": 0.0}
        return {
            "n": n,
            "hit@1": sum(1 for r in self.ranks if r == 1) / n,
            "hit@3": sum(1 for r in self.ranks if r and r <= 3) / n,
            "mrr": sum((1.0 / r) for r in self.ranks if r) / n,
        }


# ---------------------------------------------------------------------------
# 5) 메인
# ---------------------------------------------------------------------------

# 모드 이름은 결과표에 그대로 찍히므로 설명형으로 명시한다
MODE_CURRENT = "분리저장+정제질문(현행)"     # search_units 하이브리드 검색, cleaned_query 사용
MODE_MULTI   = "분리저장+멀티쿼리(실험)"     # 변형 검색어 2~3개로 각각 검색 후 RRF 합산
MODE_RAW     = "분리저장+원질문(정제없이)"   # query_transform 없이 원질문 그대로 검색
MODE_CONTENT = "원문직접검색(비교대상)"      # answer_units.content를 같은 하이브리드로 검색

# 기본 실행: 1부 핵심 비교(A vs B)만.
# `python evaluate_search_split.py full`로 실행하면 멀티쿼리·원질문 실험까지 포함
#   (search_queries 처리를 정하는 팀 회의 안건용)
MODES_BASIC = [MODE_CURRENT, MODE_CONTENT]
MODES_FULL = [MODE_CURRENT, MODE_MULTI, MODE_RAW, MODE_CONTENT]


def main(full: bool = False) -> None:
    modes = MODES_FULL if full else MODES_BASIC
    if not os.path.exists(GOLDEN_PATH):
        raise RuntimeError(f"{GOLDEN_PATH}가 없습니다. golden_set_builder.py를 먼저 완료하세요.")
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        golden = json.load(f)
    n_real = sum(1 for g in golden if g.get("source") == "real")
    print(f"✅ 골든셋 {len(golden)}개 로드 (real {n_real} / synthetic {len(golden) - n_real})")

    build_content_vectors()
    transforms = load_transforms(golden)

    # 모드 × (전체/real/synthetic) 채점기
    scorers = {m: {"all": Scorer(), "real": Scorer(), "synthetic": Scorer()} for m in modes}
    detail_rows = []

    print(f"\n🔍 검색 평가 실행: 질문 {len(golden)}개 × 검색 방식 {len(modes)}개")
    for i, g in enumerate(golden, 1):
        t = transforms[g["sample_id"]]
        results = {
            MODE_CURRENT: search_a_single(t["cleaned"]),
            MODE_CONTENT: search_content(t["cleaned"]),
        }
        if full:
            results[MODE_MULTI] = search_a_multi(t["variants"])
            results[MODE_RAW] = search_a_single(g["query"])
        src = g.get("source", "real")
        row = {"sample_id": g["sample_id"], "source": src,
               "golden_doc_id": g["golden_doc_id"],
               "query_preview": " ".join(g["query"].split())[:80]}
        for m in modes:
            r = rank_of(g["golden_doc_id"], results[m])
            scorers[m]["all"].add(r)
            scorers[m][src].add(r)
            row[f"rank[{m}]"] = r if r else f">{TOP_K}"
            row[f"top3[{m}]"] = " | ".join(results[m][:3])  # 실제로 뭘 찾아왔는지 (실패 분석용)
        detail_rows.append(row)
        if i % 20 == 0:
            print(f"  → {i}/{len(golden)}")

    # 질문별 상세 로그 (실패 사례 분석용)
    with open(RESULTS_CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    # 요약 표
    print(f"\n{'=' * 80}")
    print(f"{'검색 방식':<26} {'구분':<10} {'n':>4} {'Hit@1':>8} {'Hit@3':>8} {'MRR':>8}")
    print("-" * 80)
    for m in modes:
        for split in ("all", "real", "synthetic"):
            s = scorers[m][split].summary()
            if s["n"] == 0:
                continue
            print(f"{m:<26} {split:<10} {s['n']:>4} {s['hit@1']:>8.3f} {s['hit@3']:>8.3f} {s['mrr']:>8.3f}")
        print("-" * 80)

    print(f"""
[해석 가이드]
- 1부 핵심: '{MODE_CURRENT}' vs '{MODE_CONTENT}'
  → 현행이 높으면 "저장소를 둘로 나눈 설계가 효과 있다" 증명 (대표 숫자는 real 기준)""")
    if full:
        print(f"""- 회의 안건: '{MODE_MULTI}' vs '{MODE_CURRENT}'
  → 멀티쿼리가 Hit@3 +3%p 이상이면 구현, ±3%p 이내면 현행 유지 + 생성 제거, 하락이면 제거
- 보너스: '{MODE_RAW}' vs '{MODE_CURRENT}'
  → 현행이 높으면 query_transform 정제 단계가 검색에 기여하고 있다는 뜻""")
    else:
        print("- 멀티쿼리·원질문 실험까지 보려면: python evaluate_search_split.py full")
    print(f"""- 질문별 상세: {RESULTS_CSV_PATH} (어떤 질문에서 어떤 방식이 무너지는지 확인)
- 주의: 점수 차이가 근소하면(±3%p) 표본 크기 한계로 단정하지 말 것
""")


if __name__ == "__main__":
    import sys
    main(full=(len(sys.argv) > 1 and sys.argv[1] == "full"))
