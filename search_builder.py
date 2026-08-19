"""
search_builder.py — 검색용 테이블(search_units) 빌드

rag_view_sentences.json(가설질문·실제질문·맥락요약 문장)을 검색 단위로 적재한다.
- 문장 1개 = 검색 단위 1행. 답변 본문은 answer_units에 있고 여기엔 doc_id만 둔다.
- 키워드 검색: Kiwi로 명사/동사/형용사만 뽑아 to_tsvector('simple', ...)  (rag_A.py와 동일)
- 벡터 검색: OpenAI text-embedding-3-small
- 이미 적재된 문장은 다시 임베딩하지 않는다(증분). JSON에서 빠진 문장은 정리한다.
"""

import os
import json
import time
from typing import Dict, List, Set, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from kiwipiepy import Kiwi

from dto import SearchChunk
from answer_builder import setup_answer_table

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "dbname=riido user=postgres password=postgres host=localhost port=5432",
)

VIEW_SENTENCES_PATH = "./rag_view_sentences.json"
EMBED_BATCH_SIZE = 90
EMBED_SLEEP_SEC = 5

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
kiwi = Kiwi()


def get_connection():
    return psycopg2.connect(DATABASE_URL)


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

    conn = get_connection()
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

def load_view_sentences(json_path: str = VIEW_SENTENCES_PATH) -> List[SearchChunk]:
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

def fetch_existing_keys() -> Set[Tuple[str, str]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT doc_id, text FROM search_units;")
    keys = {(row[0], row[1]) for row in cur.fetchall()}
    cur.close()
    conn.close()
    return keys


def fetch_known_doc_ids() -> Set[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT doc_id FROM answer_units;")
    doc_ids = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return doc_ids


def embed_and_store(chunks: List[SearchChunk], batch_size: int = EMBED_BATCH_SIZE) -> None:
    if not chunks:
        return

    conn = get_connection()
    cur = conn.cursor()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embeddings.embed_documents([c.text for c in batch])

        for chunk, vector in zip(batch, vectors):
            chunk.embedding = vector
            cur.execute("""
                INSERT INTO search_units (doc_id, view_type, text, text_tsv, embedding)
                VALUES (%s, %s, %s, to_tsvector('simple', %s), %s::vector)
                ON CONFLICT (doc_id, text) DO UPDATE SET
                    view_type  = EXCLUDED.view_type,
                    text_tsv   = EXCLUDED.text_tsv,
                    embedding  = EXCLUDED.embedding,
                    updated_at = now();
            """, (chunk.doc_id, chunk.view_type, chunk.text, chunk.text_tsv, str(chunk.embedding)))

        conn.commit()
        print(f"  → {min(i + batch_size, len(chunks))}/{len(chunks)}개 임베딩 완료")

        if i + batch_size < len(chunks):
            time.sleep(EMBED_SLEEP_SEC)

    cur.close()
    conn.close()


def prune_removed(keep_keys: Set[Tuple[str, str]]) -> int:
    """JSON에서 사라진 문장 삭제"""
    stale = [key for key in fetch_existing_keys() if key not in keep_keys]
    if not stale:
        return 0

    conn = get_connection()
    cur = conn.cursor()
    psycopg2.extras.execute_batch(
        cur,
        "DELETE FROM search_units WHERE doc_id = %s AND text = %s;",
        stale,
    )
    conn.commit()
    cur.close()
    conn.close()
    return len(stale)


# ---------------------------------------------------------------------------
# 4) 오케스트레이션
# ---------------------------------------------------------------------------

def build_search_units(json_path: str = VIEW_SENTENCES_PATH) -> None:
    sample_vector = embeddings.embed_query("테스트")
    setup_search_table(embedding_dim=len(sample_vector))

    chunks = load_view_sentences(json_path)
    print(f"✅ 검색 문장 {len(chunks)}개 로드")

    known_doc_ids = fetch_known_doc_ids()
    if not known_doc_ids:
        raise RuntimeError(
            "answer_units가 비어 있습니다. answer_builder.py를 먼저 실행하세요."
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

    embed_and_store(new_chunks)

    deleted = prune_removed(keep_keys)
    if deleted:
        print(f"🧹 JSON에서 사라진 문장 {deleted}건 삭제")


def print_stats() -> None:
    conn = get_connection()
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


if __name__ == "__main__":
    build_search_units()
    print_stats()
