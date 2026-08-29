"""
evaluate_search_split.py — 검색 평가 본체 (2차: 혼합 하이브리드 개선안 검증)

golden_set.json(질문 → 정답 doc_id)으로 검색 품질을 Hit@1 / Hit@3 / MRR로 채점한다.

[이번 실험의 가설]
1. 질문형 정제: 검색 인덱스(search_units)가 질문형 문장(hypo_q·real_q) 중심이므로,
   쿼리도 키워드체("작업 생성 방법")가 아니라 질문형("작업은 어떻게 만드나요?")으로
   정제하면 벡터 거리가 더 가까워진다.
2. 혼합 하이브리드: 벡터 검색은 검색용 문장(search_units)에서, 키워드 검색은
   원문(answer_units.content)에서 수행한다. 짧은 질문 문장은 담긴 키워드가 적어
   키워드 검색이 빈약한데, 원문은 단어가 풍부해 키워드 매칭이 잘 걸린다.

[비교 대상 — 전처럼 두 가지]
  분리저장 혼합   : 벡터=search_units + 키워드=원문, RRF 결합   ← 개선안
  원문직접검색    : 벡터·키워드 모두 원문, RRF 결합             ← 비교 기준
  (두 방식 모두 동일한 질문형 정제 쿼리를 입력받는다 — 공정성 통제)

공정성 통제:
- 임베딩 모델 동일 (text-embedding-3-small, rag_search와 같은 인스턴스 재사용)
- RRF 파라미터 동일 (k=30, 벡터:키워드 = 0.5:0.5 — 프로덕션과 동일)
- 검색 입력 동일 (같은 질문형 정제 쿼리. LLM 정제 결과는 파일 캐시)
- 채점 규칙 동일 (문서 계층 반영: 정답의 부모/자식 문서도 정답 인정)

출력:
- 콘솔: 방식×(전체/real/synthetic) 요약 표
- eval_results.csv: 질문별 상세 (순위 + 실제 top-3 목록) — 실패 사례 분석용

실행: python evaluate_search_split.py
"""

import os
import csv
import json
import time
from typing import Dict, List, Optional

import psycopg2.extras
from openai import OpenAI

# 프로덕션 검색 코드를 그대로 재사용한다 — "평가한 것 = 실제 시스템"을 보장
from db import DATABASE_URL
from rag_search import embeddings, extract_keywords, build_tsquery, vector_search


def get_connection():
    """평가 스크립트 전용 단순 커넥션 (프로덕션은 db.py 풀 사용)"""
    return psycopg2.connect(DATABASE_URL)

GOLDEN_PATH = "./golden_set.json"
TRANSFORM_CACHE_PATH = "./eval_transform_cache.json"
RESULTS_CSV_PATH = "./eval_results.csv"

TOP_K = 10           # 이 순위까지 정답을 찾는다 (MRR 계산 범위)
RRF_K = 30           # RRF 파라미터 (rag_search 프로덕션 기본값과 동일)
VECTOR_WEIGHT = 0.5  # 벡터:키워드 가중치 (프로덕션 기본값과 동일)
EMBED_BATCH = 50
EMBED_SLEEP_SEC = 3
CONTENT_EMBED_CHARS = 8000  # 임베딩 입력 안전 상한 (모델 한도 초과 방지)

QUESTION_CLEAN_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 1) 원문 검색용 테이블 — 키워드(원문)와 원문직접검색 양쪽에서 사용
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
    """answer_units 원문을 임베딩·색인해서 채운다. 이미 된 문서는 건너뛴다(증분)."""
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
        print("✅ 원문 검색 테이블(answer_content_vectors) 준비 완료 (이미 구축됨)")
        cur.close(); conn.close()
        return

    print(f"🔨 원문 검색 테이블 구축: {len(todo)}개 임베딩 (몇 분 걸릴 수 있음)")
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


def content_vector_docs(query: str, top_k: int = 20) -> List[str]:
    """원문 벡터 검색 → 문서 순위"""
    qv = embeddings.embed_query(query)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT doc_id FROM answer_content_vectors
        ORDER BY embedding <=> %s::vector LIMIT %s
    """, (str(qv), top_k))
    docs = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return docs


def content_keyword_docs(query: str, top_k: int = 20) -> List[str]:
    """원문 키워드 검색 → 문서 순위"""
    tsq = build_tsquery(extract_keywords(query))
    if not tsq:
        return []
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT doc_id FROM answer_content_vectors
        WHERE text_tsv @@ to_tsquery('simple', %s)
        ORDER BY ts_rank(text_tsv, to_tsquery('simple', %s)) DESC LIMIT %s
    """, (tsq, tsq, top_k))
    docs = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return docs


def search_unit_vector_docs(query: str, top_k: int = 20) -> List[str]:
    """search_units 벡터 검색 → 문서 순위 (같은 문서의 여러 문장은 첫 등장만)"""
    rows = vector_search(query, top_k=top_k)  # 프로덕션 함수 재사용
    docs: List[str] = []
    for r in rows:
        if r["doc_id"] not in docs:
            docs.append(r["doc_id"])
    return docs


# ---------------------------------------------------------------------------
# 2) 문서 단위 RRF 결합 — 두 검색의 문서 순위를 합친다
# ---------------------------------------------------------------------------

def rrf_docs(vec_docs: List[str], kw_docs: List[str],
             k: int = RRF_K, vector_weight: float = VECTOR_WEIGHT) -> List[str]:
    scores: Dict[str, float] = {}
    for rank, d in enumerate(vec_docs):
        scores[d] = scores.get(d, 0.0) + vector_weight * (1.0 / (k + rank + 1))
    for rank, d in enumerate(kw_docs):
        scores[d] = scores.get(d, 0.0) + (1 - vector_weight) * (1.0 / (k + rank + 1))
    return sorted(scores, key=lambda d: scores[d], reverse=True)


def search_split_mixed(query: str) -> List[str]:
    """개선안: 벡터=search_units(질문형 문장) + 키워드=원문 → RRF 결합"""
    return rrf_docs(search_unit_vector_docs(query), content_keyword_docs(query))[:TOP_K]


def search_content_only(query: str) -> List[str]:
    """비교 기준: 벡터·키워드 모두 원문 → RRF 결합"""
    return rrf_docs(content_vector_docs(query), content_keyword_docs(query))[:TOP_K]


# ---------------------------------------------------------------------------
# 3) 질문형 정제 — 노이즈 제거하되 질문형 문장 유지 (결과는 파일 캐시)
# ---------------------------------------------------------------------------

QUESTION_CLEAN_PROMPT = """
당신은 검색 쿼리 정제 도우미입니다. 사용자 질문에서 인사말, 이메일 주소, 서명,
후속 대화("감사합니다", "해결했습니다" 등), 마스킹 토큰을 제거하고,
핵심 의도 하나를 담은 자연스러운 한국어 질문 한 문장으로 정리하세요.

중요: 키워드 나열("작업 생성 방법")로 바꾸지 말고, 사람이 묻는 질문형 문장
("작업은 어떻게 만드나요?")을 유지하세요. 여러 주제가 섞여 있으면 가장 중심이
되는 질문 하나만 남기세요.

JSON으로만 응답: {"question": "정리된 질문 한 문장"}
"""

_client = None


def to_question_form(raw: str) -> str:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        res = _client.chat.completions.create(
            model=QUESTION_CLEAN_MODEL,
            messages=[
                {"role": "system", "content": QUESTION_CLEAN_PROMPT.strip()},
                {"role": "user", "content": f"사용자 질문: {raw}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        q = json.loads(res.choices[0].message.content).get("question", "").strip()
        return q or raw
    except Exception:
        return raw  # 정제 실패 시 원문으로 검색 (평가가 통째로 멈추지 않게)


def load_question_queries(golden: List[Dict]) -> Dict[str, str]:
    """sample_id → 질문형 정제 쿼리. 기존 캐시 파일에 'question' 키로 누적 저장."""
    cache: Dict[str, Dict] = {}
    if os.path.exists(TRANSFORM_CACHE_PATH):
        with open(TRANSFORM_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)

    todo = [g for g in golden if "question" not in cache.get(g["sample_id"], {})]
    if todo:
        print(f"🔄 질문형 정제: {len(todo)}개 (캐시됨 {len(golden) - len(todo)}개)")
    for i, g in enumerate(todo, 1):
        entry = cache.setdefault(g["sample_id"], {})
        entry["question"] = to_question_form(g["query"])
        with open(TRANSFORM_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)  # 매번 저장 — 중단 후 재개 가능
        if i % 20 == 0:
            print(f"  → {i}/{len(todo)}")
    return {g["sample_id"]: cache[g["sample_id"]]["question"] for g in golden}


# ---------------------------------------------------------------------------
# 4) 채점
# ---------------------------------------------------------------------------

def is_correct(golden_doc_id: str, doc_id: str) -> bool:
    """문서 계층을 고려한 정답 판정.

    문서가 부모/자식 계층(guide/팀 ↔ guide/팀/개요)으로 나뉘어 있어서
    정답의 같은 가족 문서를 찾아와도 내용상 정답인 경우가 많다.
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
MODE_SPLIT = "분리저장혼합(벡터=질문문장+키워드=원문)"   # 개선안
MODE_CONTENT = "원문직접검색(벡터·키워드=원문)"          # 비교 기준

MODES = [MODE_SPLIT, MODE_CONTENT]


def main() -> None:
    if not os.path.exists(GOLDEN_PATH):
        raise RuntimeError(f"{GOLDEN_PATH}가 없습니다. golden_set_builder.py를 먼저 완료하세요.")
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        golden = json.load(f)
    n_real = sum(1 for g in golden if g.get("source") == "real")
    print(f"✅ 골든셋 {len(golden)}개 로드 (real {n_real} / synthetic {len(golden) - n_real})")

    build_content_vectors()
    questions = load_question_queries(golden)

    scorers = {m: {"all": Scorer(), "real": Scorer(), "synthetic": Scorer()} for m in MODES}
    detail_rows = []

    print(f"\n🔍 검색 평가 실행: 질문 {len(golden)}개 × 검색 방식 {len(MODES)}개 (질문형 정제 쿼리 사용)")
    for i, g in enumerate(golden, 1):
        q = questions[g["sample_id"]]
        results = {
            MODE_SPLIT: search_split_mixed(q),
            MODE_CONTENT: search_content_only(q),
        }
        src = g.get("source", "real")
        row = {"sample_id": g["sample_id"], "source": src,
               "golden_doc_id": g["golden_doc_id"],
               "query_used": q[:80]}
        for m in MODES:
            r = rank_of(g["golden_doc_id"], results[m])
            scorers[m]["all"].add(r)
            scorers[m][src].add(r)
            row[f"rank[{m}]"] = r if r else f">{TOP_K}"
            row[f"top3[{m}]"] = " | ".join(results[m][:3])  # 실제로 뭘 찾아왔는지 (실패 분석용)
        detail_rows.append(row)
        if i % 20 == 0:
            print(f"  → {i}/{len(golden)}")

    with open(RESULTS_CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    print(f"\n{'=' * 88}")
    print(f"{'검색 방식':<34} {'구분':<10} {'n':>4} {'Hit@1':>8} {'Hit@3':>8} {'MRR':>8}")
    print("-" * 88)
    for m in MODES:
        for split in ("all", "real", "synthetic"):
            s = scorers[m][split].summary()
            if s["n"] == 0:
                continue
            print(f"{m:<34} {split:<10} {s['n']:>4} {s['hit@1']:>8.3f} {s['hit@3']:>8.3f} {s['mrr']:>8.3f}")
        print("-" * 88)

    print(f"""
[해석 가이드]
- 비교: '{MODE_SPLIT}' vs '{MODE_CONTENT}'
  → 개선안이 높으면 "질문형 정제 + 혼합 하이브리드(벡터는 질문문장, 키워드는 원문)"가
    저장소 분리 설계의 올바른 활용법이라는 증거 (대표 숫자는 real 기준)
- 이전 라운드(키워드체 정제, 벡터·키워드 모두 같은 저장소)와 비교하면
  질문형 정제의 효과도 가늠 가능 — 이전: 현행 real Hit@3=0.448 / 원문직접 0.241
- 질문별 상세: {RESULTS_CSV_PATH} (query_used 열에서 정제된 쿼리도 확인)
- 주의: 점수 차이가 근소하면(±3%p) 표본 크기 한계로 단정하지 말 것
""")


if __name__ == "__main__":
    main()
