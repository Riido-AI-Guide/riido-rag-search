"""
scripts/build_search_units.py — 검색용 테이블(search_units) 빌드

rag_view_sentences.json(가설질문·실제질문·맥락요약 문장)을 검색 단위로 적재한다.
- 문장 1개 = 검색 단위 1행. 답변 본문은 answer_units에 있고 여기엔 doc_id만 둔다.
- 키워드 검색: Kiwi로 명사/동사/형용사만 뽑아 to_tsvector('simple', ...)  (core/search.py와 동일)
- 벡터 검색: OpenAI text-embedding-3-small
- 이미 적재된 문장은 다시 임베딩하지 않는다(증분). JSON에서 빠진 문장은 정리한다.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import psycopg2.extras
from langchain_openai import OpenAIEmbeddings
from kiwipiepy import Kiwi

from core.config import OPENAI_API_KEY
from core.db import connect
from domain import SearchChunk
from scripts.paths import DATA_DIR
from scripts.build_answer_units import setup_answer_table



VIEW_SENTENCES_PATH = DATA_DIR / "rag_view_sentences.json"
EMBED_BATCH_SIZE = 90
EMBED_SLEEP_SEC = 5

# core/search.py와 같은 이유로 키 존재를 먼저 확인한다
# (OpenAIEmbeddings가 환경변수를 직접 읽는다).
if not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY가 설정되지 않았습니다. .env를 확인하세요 (.env.example 참고)."
    )

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
kiwi = Kiwi()


def extract_keywords(text: str) -> str:
    """명사·동사·형용사만 남긴 키워드 문자열 (tsvector 입력용)"""
    tokens = kiwi.tokenize(text)
    keywords = [t.form for t in tokens if t.tag.startswith(("NN", "VV", "VA"))]
    return " ".join(keywords)


# ---------------------------------------------------------------------------
# 1) 스키마
# ---------------------------------------------------------------------------

def setup_search_table(embedding_dim: int) -> None:
    """search_units 생성. doc_id는 answer_units를 참조하고 답변이 지워지면 함께 정리된다."""
    setup_answer_table()  # FK 대상 테이블 보장

    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS search_units (
            id         SERIAL PRIMARY KEY,
            doc_id     TEXT NOT NULL REFERENCES answer_units(doc_id) ON DELETE CASCADE,
            view_type  TEXT NOT NULL,
            text       TEXT NOT NULL,
            text_tsv   TSVECTOR,
            embedding  VECTOR({embedding_dim}),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (doc_id, text)
        );
    """)
    # source      : 이 문장을 누가 넣었나. 'file'만 JSON 기준으로 정리(prune)한다 —
    #               콘솔에서 넣은 문장을 다음 빌드가 지워버리면 안 된다.
    # source_hash : 이 문장을 만들 때 본 본문의 해시. 본문이 바뀌면 달라지므로
    #               "문서는 갱신됐는데 문장은 옛 내용"을 잡아낼 수 있다.
    cur.execute("ALTER TABLE search_units ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'file';")
    cur.execute("ALTER TABLE search_units ADD COLUMN IF NOT EXISTS source_hash TEXT;")

    # 이 컬럼 이전에 들어온 문장은 어느 본문 기준인지 알 수 없다. 지금 본문 기준으로 간주한다 —
    # 전부 '낡음'으로 두면 재검토 큐가 179건으로 시작해 아무도 쓰지 않는다.
    cur.execute("""
        UPDATE search_units s SET source_hash = a.source_hash
        FROM answer_units a WHERE a.doc_id = s.doc_id AND s.source_hash IS NULL;
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_search_units_tsv ON search_units USING GIN (text_tsv);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_search_units_embedding ON search_units USING hnsw (embedding vector_cosine_ops);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_search_units_doc ON search_units (doc_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_search_units_view ON search_units (view_type);")
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# 2) JSON 로드
# ---------------------------------------------------------------------------

def load_view_sentences(json_path: Path = VIEW_SENTENCES_PATH) -> List[SearchChunk]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks: List[SearchChunk] = []
    seen: Set[Tuple[str, str]] = set()

    for item in data:
        text = (item.get("text") or "").strip()
        doc_id = (item.get("doc_id") or "").strip()
        if not text or not doc_id:
            continue

        key = (doc_id, text)
        if key in seen:  # UNIQUE(doc_id, text)와 같은 기준으로 사전 중복 제거
            continue
        seen.add(key)

        chunks.append(SearchChunk(
            text=text,
            doc_id=doc_id,
            view_type=item.get("view_type", ""),
            text_tsv=extract_keywords(text),
        ))

    return chunks


# ---------------------------------------------------------------------------
# 3) 적재
# ---------------------------------------------------------------------------

def fetch_existing_keys(source: Optional[str] = None) -> Set[Tuple[str, str]]:
    """적재된 (doc_id, text). source를 주면 그 출처의 것만."""
    conn = connect()
    cur = conn.cursor()
    if source:
        cur.execute("SELECT doc_id, text FROM search_units WHERE source = %s;", (source,))
    else:
        cur.execute("SELECT doc_id, text FROM search_units;")
    keys = {(row[0], row[1]) for row in cur.fetchall()}
    cur.close()
    conn.close()
    return keys


def fetch_doc_hashes() -> Dict[str, str]:
    """{doc_id: 본문 해시}. 새로 넣는 문장에 '어느 본문을 보고 썼는지'를 새긴다."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT doc_id, source_hash FROM answer_units;")
    hashes = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return hashes


def fetch_known_doc_ids() -> Set[str]:
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT doc_id FROM answer_units;")
    doc_ids = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return doc_ids


def embed_and_store(
    chunks: List[SearchChunk],
    doc_hashes: Optional[Dict[str, str]] = None,
    batch_size: int = EMBED_BATCH_SIZE,
) -> None:
    if not chunks:
        return

    doc_hashes = doc_hashes or {}

    conn = connect()
    cur = conn.cursor()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embeddings.embed_documents([c.text for c in batch])

        for chunk, vector in zip(batch, vectors):
            chunk.embedding = vector
            cur.execute("""
                INSERT INTO search_units
                    (doc_id, view_type, text, text_tsv, embedding, source, source_hash)
                VALUES (%s, %s, %s, to_tsvector('simple', %s), %s::vector, 'file', %s)
                ON CONFLICT (doc_id, text) DO UPDATE SET
                    view_type   = EXCLUDED.view_type,
                    text_tsv    = EXCLUDED.text_tsv,
                    embedding   = EXCLUDED.embedding,
                    source_hash = EXCLUDED.source_hash,
                    updated_at  = now();
            """, (chunk.doc_id, chunk.view_type, chunk.text, chunk.text_tsv,
                  str(chunk.embedding), doc_hashes.get(chunk.doc_id)))

        conn.commit()
        print(f"  → {min(i + batch_size, len(chunks))}/{len(chunks)}개 임베딩 완료")

        if i + batch_size < len(chunks):
            time.sleep(EMBED_SLEEP_SEC)

    cur.close()
    conn.close()


def prune_removed(keep_keys: Set[Tuple[str, str]]) -> int:
    """
    JSON에서 사라진 문장 삭제. **파일이 출처인 문장만** 본다 —
    콘솔에서 등록한 문장은 JSON에 없는 것이 정상이라 여기서 지우면 안 된다.
    """
    stale = [key for key in fetch_existing_keys(source="file") if key not in keep_keys]
    if not stale:
        return 0

    conn = connect()
    cur = conn.cursor()
    psycopg2.extras.execute_batch(
        cur,
        "DELETE FROM search_units WHERE doc_id = %s AND text = %s AND source = 'file';",
        stale,
    )
    conn.commit()
    cur.close()
    conn.close()
    return len(stale)


# ---------------------------------------------------------------------------
# 4) 오케스트레이션
# ---------------------------------------------------------------------------

def build_search_units(json_path: Path = VIEW_SENTENCES_PATH) -> None:
    sample_vector = embeddings.embed_query("테스트")
    setup_search_table(embedding_dim=len(sample_vector))

    chunks = load_view_sentences(json_path)
    print(f"✅ 검색 문장 {len(chunks)}개 로드")

    known_doc_ids = fetch_known_doc_ids()
    if not known_doc_ids:
        raise RuntimeError(
            "answer_units가 비어 있습니다. python -m scripts.build_answer_units를 먼저 실행하세요."
        )

    orphans = [c for c in chunks if c.doc_id not in known_doc_ids]
    if orphans:
        missing = sorted({c.doc_id for c in orphans})
        print(f"⚠️  answer_units에 없는 doc_id {len(missing)}건은 건너뜁니다 (FK 위반 방지)")
        for doc_id in missing[:10]:
            print(f"  - {doc_id}")

    chunks = [c for c in chunks if c.doc_id in known_doc_ids]
    keep_keys = {(c.doc_id, c.text) for c in chunks}

    existing = fetch_existing_keys()
    new_chunks = [c for c in chunks if (c.doc_id, c.text) not in existing]
    print(f"📦 신규 {len(new_chunks)} / 유지 {len(chunks) - len(new_chunks)}")

    embed_and_store(new_chunks, fetch_doc_hashes())

    deleted = prune_removed(keep_keys)
    if deleted:
        print(f"🧹 JSON에서 사라진 문장 {deleted}건 삭제")


def print_stats() -> None:
    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT view_type,
               COUNT(*)                    AS units,
               COUNT(DISTINCT doc_id)      AS docs,
               AVG(LENGTH(text))::int      AS avg_len
        FROM search_units GROUP BY view_type ORDER BY view_type;
    """)
    for row in cur.fetchall():
        print(f"  [{row['view_type']}] {row['units']}개 / 문서 {row['docs']}개 (평균 {row['avg_len']}자)")
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# 원문 키워드·벡터 인덱스 (answer_content_vectors)
#   분리혼합 검색의 키워드 검색 대상. rag_search.content_keyword_search가 사용한다.
#   (2차 평가 scripts/evaluate_search.py에서 검증된 구조를 정식 편입)
# ---------------------------------------------------------------------------

CONTENT_EMBED_CHARS = 8000  # 임베딩 입력 안전 상한 (모델 한도 초과 방지)


def setup_content_table(embedding_dim: int) -> None:
    conn = connect()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS answer_content_vectors (
            doc_id      TEXT PRIMARY KEY REFERENCES answer_units(doc_id) ON DELETE CASCADE,
            text_tsv    TSVECTOR,
            embedding   VECTOR({embedding_dim}),
            source_hash TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    # 이 인덱스가 '어느 본문'을 색인한 것인지 행에 적어 둔다. 이게 없으면 본문이 바뀌어도
    # 낡았다는 사실을 아무도 알 수 없다(예전에는 행이 있으면 그냥 건너뛰었다).
    cur.execute("ALTER TABLE answer_content_vectors ADD COLUMN IF NOT EXISTS source_hash TEXT;")
    cur.execute(
        "ALTER TABLE answer_content_vectors "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acv_tsv ON answer_content_vectors USING GIN (text_tsv);")
    conn.commit()
    cur.close()
    conn.close()


def build_content_vectors() -> None:
    """answer_units 원문을 임베딩·색인한다. 인덱스가 없거나 **낡은** 문서만 다시 만든다.

    낡음의 기준은 source_hash다 — answer_units의 해시와 다르면 그 인덱스는 옛 본문을
    색인한 것이다. 예전에는 행이 있으면 그냥 건너뛰어서, 본문이 바뀐 문서는 답변만
    최신이고 키워드 검색은 옛 본문으로 걸렸다(그 사실이 아무 데도 드러나지 않았다).

    source_hash가 비어 있는 행(이 컬럼 이전에 만들어진 인덱스)은 어느 본문 기준인지
    알 수 없으므로 낡은 것으로 본다. 처음 한 번만 전체가 다시 임베딩된다.
    """
    dim = len(embeddings.embed_query("차원 확인"))
    setup_content_table(dim)

    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.doc_id, a.content, a.source_hash,
               (v.doc_id IS NULL) AS is_new
        FROM answer_units a
        LEFT JOIN answer_content_vectors v ON v.doc_id = a.doc_id
        WHERE v.doc_id IS NULL
           OR v.source_hash IS DISTINCT FROM a.source_hash
        ORDER BY a.doc_id;
    """)
    todo = cur.fetchall()
    if not todo:
        print("✅ 원문 검색 인덱스(answer_content_vectors) 최신 상태")
        cur.close(); conn.close()
        return

    new_count = sum(1 for r in todo if r["is_new"])
    print(f"🔨 원문 검색 인덱스: 신규 {new_count} / 갱신 {len(todo) - new_count}"
          f" — 총 {len(todo)}개 임베딩 (몇 분 걸릴 수 있음)")
    for i in range(0, len(todo), EMBED_BATCH_SIZE):
        batch = todo[i:i + EMBED_BATCH_SIZE]
        vectors = embeddings.embed_documents([r["content"][:CONTENT_EMBED_CHARS] for r in batch])
        for row, vec in zip(batch, vectors):
            # 갱신 대상이 있으므로 DO NOTHING이면 안 된다 — 낡은 행을 덮어써야 한다
            cur.execute("""
                INSERT INTO answer_content_vectors (doc_id, text_tsv, embedding, source_hash, updated_at)
                VALUES (%s, to_tsvector('simple', %s), %s::vector, %s, now())
                ON CONFLICT (doc_id) DO UPDATE SET
                    text_tsv    = EXCLUDED.text_tsv,
                    embedding   = EXCLUDED.embedding,
                    source_hash = EXCLUDED.source_hash,
                    updated_at  = now();
            """, (row["doc_id"], extract_keywords(row["content"]), str(vec), row["source_hash"]))
        conn.commit()
        print(f"  → {min(i + EMBED_BATCH_SIZE, len(todo))}/{len(todo)}")
        if i + EMBED_BATCH_SIZE < len(todo):
            time.sleep(EMBED_SLEEP_SEC)
    cur.close()
    conn.close()


if __name__ == "__main__":
    build_search_units()
    build_content_vectors()
    print_stats()
