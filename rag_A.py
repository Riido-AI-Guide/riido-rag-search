"""
A파트 — search() 함수가 이 파일의 산출물
하이브리드 검색: 벡터 + Kiwi 기반 키워드 검색을 RRF로 결합
rrf_score(정렬용)와 similarity(신뢰도 판단용)를 분리해서 반환
임베딩: OpenAI text-embedding-3-small
"""

import os
import re
import json
import time
from typing import Dict, List
import requests
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from kiwipiepy import Kiwi

from dto import RawChunk, RetrievedChunk

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "dbname=riido user=postgres password=postgres host=localhost port=5432")
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
kiwi = Kiwi()


def extract_keywords(text: str) -> str:
    tokens = kiwi.tokenize(text)
    keywords = [t.form for t in tokens if t.tag.startswith(("NN", "VV", "VA"))]
    return " ".join(keywords)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def setup_database(embedding_dim: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS doc_chunks (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            embedding VECTOR({embedding_dim}),
            title TEXT,
            section TEXT,
            source_type TEXT,
            content_tsv TSVECTOR
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv ON doc_chunks USING GIN (content_tsv);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding ON doc_chunks USING hnsw (embedding vector_cosine_ops);")
    conn.commit()
    cur.close()
    conn.close()


def is_db_empty() -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM doc_chunks;")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count == 0


def chunk_riido_docs(chunks: List[RawChunk], raw_text: str, max_chars: int = 600):
    doc_parts = re.split(r"\n# (.+?)\n", raw_text)
    seen_titles = set()

    for i in range(1, len(doc_parts), 2):
        title = doc_parts[i].strip()
        content = doc_parts[i + 1] if i + 1 < len(doc_parts) else ""

        if title in seen_titles:
            continue
        seen_titles.add(title)

        content = re.sub(r"<figure>.*?</figure>", "", content, flags=re.DOTALL)
        content = re.sub(r"\{%.*?%\}", "", content)

        sub_parts = re.split(r"\n(#{2,3}) (.+?)\n", content)
        current_heading = None

        for j in range(0, len(sub_parts), 3):
            text = sub_parts[j].strip()
            heading = sub_parts[j + 2] if j + 2 < len(sub_parts) else None

            if text:
                path = f"{title} > {current_heading}" if current_heading else title
                for k in range(0, len(text), max_chars):
                    chunks.append(RawChunk(
                        content=f"[{path}]\n{text[k:k + max_chars]}",
                        title=title,
                        section=path,
                        source_type="guide"
                    ))

            if heading:
                current_heading = heading


def clean_greeting_and_intro(text: str) -> str:
    """인사말 + 뒤이어 나오는 자기소개(마스킹 태그 포함 문장) 제거"""
    text = text.strip()
    text = re.sub(r'^(네\s+|담당자님\s+|<[^>]+>\s+)*안녕하세요[!.,]?\s*', '', text)
    text = re.sub(r'^[^\n.!?]{0,80}?<[^>]+>[^\n.!?]{0,40}(입니다|팀입니다)[.!]?\s*', '', text)
    return text.strip()


def clean_manager_text(text: str) -> str:
    return clean_greeting_and_intro(text)


def is_content_bearing(raw_text: str) -> bool:
    """인사말 뗀 후 이메일/태그/기호를 빼고도 실질 내용이 남는지 판별"""
    text = clean_greeting_and_intro(raw_text)
    if len(text) < 15:
        return False
    stripped = re.sub(r'<[^>]+>|[\w.+-]+@[\w-]+\.[\w.-]+|[\s!.,]', '', text)
    return len(stripped) >= 5


def find_question(user_msgs: list) -> str:
    """실제 내용이 담긴 첫 발화를 질문으로 채택, 없으면 첫 발화 그대로"""
    for msg in user_msgs:
        if is_content_bearing(msg):
            return clean_greeting_and_intro(msg)
    return clean_greeting_and_intro(user_msgs[0]) if user_msgs else ""


def chunk_support_qa(chunks: List[RawChunk], json_path: str):
    """
    question: 실제 내용이 담긴 첫 user 발화
    answer: manager(운영자) 답변만 사용
    manager 답변 없는 상담은 제외
    (bot 요약은 목차 수준이라 구체적 방법 안내라는 목적에 안 맞음)
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    
    for item in data["items"]:
        user_msgs = [c["text"] for c in item["conversation"] if c["role"] == "user"]
        manager_msgs = [c["text"] for c in item["conversation"] if c["role"] == "manager"]

        if not manager_msgs:
            continue

        cleaned = [clean_manager_text(m) for m in manager_msgs]
        cleaned = [c for c in cleaned if len(c) > 10]
        answer = " ".join(cleaned)

        if len(answer) < 20:
            continue

        first_question = find_question(user_msgs) if user_msgs else item["question"][:100]

        chunks.append(RawChunk(
            content=f"질문: {first_question}\n답변: {answer}",
            title="실제 고객 문의",
            section=f"고객문의 > {item['sample_id']}",
            source_type="support_qa"
        ))


def embed_and_store(chunks: List[RawChunk], batch_size: int = 90):
    conn = get_connection()
    cur = conn.cursor()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embeddings.embed_documents([c.content for c in batch])

        for chunk, vector in zip(batch, vectors):
            chunk.content_keywords = extract_keywords(chunk.content)
            cur.execute("""
                INSERT INTO doc_chunks (content, embedding, title, section, source_type, content_tsv)
                VALUES (%s, %s, %s, %s, %s, to_tsvector('simple', %s))
            """, (chunk.content, vector, chunk.title, chunk.section, chunk.source_type, chunk.content_keywords))

        conn.commit()
        print(f"  → {min(i + batch_size, len(chunks))}/{len(chunks)}개 임베딩 완료")

        if i + batch_size < len(chunks):
            time.sleep(5)

    cur.close()
    conn.close()


def build_index(support_qa_path: str = None):
    sample_vector = embeddings.embed_query("테스트")
    setup_database(embedding_dim=len(sample_vector))

    if not is_db_empty():
        print("📂 이미 데이터가 있어 재수집을 건너뜁니다.")
        return

    guide_chunks: List[RawChunk] = []
    
    res = requests.get("https://docs.riido.io/llms-full.txt")
    chunk_riido_docs(guide_chunks, res.text)
    print(f"✅ 이용가이드 {len(guide_chunks)}개 청크")
    embed_and_store(guide_chunks)

    if support_qa_path:
        time.sleep(5)
        qa_chunks: List[RawChunk] = []
        chunk_support_qa(qa_chunks, support_qa_path)
        print(f"✅ 고객 QA {len(qa_chunks)}개 청크")
        embed_and_store(qa_chunks)


def vector_search(query: str, top_k: int = 20):
    query_vector = embeddings.embed_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, content, title, section, source_type,
               1 - (embedding <=> %s::vector) AS similarity
        FROM doc_chunks ORDER BY embedding <=> %s::vector LIMIT %s
    """, (query_vector, query_vector, top_k))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def keyword_search(query: str, top_k: int = 20):
    query_keywords = extract_keywords(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, content, title, section, source_type,
               ts_rank(content_tsv, plainto_tsquery('simple', %s)) AS rank
        FROM doc_chunks WHERE content_tsv @@ plainto_tsquery('simple', %s)
        ORDER BY rank DESC LIMIT %s
    """, (query_keywords, query_keywords, top_k))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def reciprocal_rank_fusion(vector_results, keyword_results, k: int = 30, vector_weight: float = 0.5):
    keyword_weight = 1 - vector_weight
    scores = {}
    chunk_map: Dict[int, RetrievedChunk] = {}
    
    for rank, row in enumerate(vector_results):
        chunk_id = row["id"]
        
        if chunk_id not in chunk_map:
            chunk_map[chunk_id] = RetrievedChunk(
                id=chunk_id,
                title=row["title"],
                section=row["section"],
                content=row["content"],
                source_type=row["source_type"],
                v_similarity=row.get("similarity", 0.0)
            )
        else:
            chunk_map[chunk_id].v_similarity = row.get("similarity", 0.0)
        chunk_map[chunk_id].rrf_score += vector_weight * (1 / (k + rank + 1))
    
    for rank, row in enumerate(keyword_results):
        chunk_id = row["id"]
        
        if chunk_id not in chunk_map:
            chunk_map[chunk_id] = RetrievedChunk(
                id=chunk_id,
                title=row["title"],
                section=row["section"],
                content=row["content"],
                source_type=row["source_type"],
                k_similarity=row.get("rank", 0.0)
            )
        else:
            chunk_map[chunk_id].k_similarity = row.get("rank", 0.0)
        chunk_map[chunk_id].rrf_score += keyword_weight * (1 / (k + rank + 1))
    return sorted(chunk_map.values(), key=lambda x: x.rrf_score, reverse=True)


def search(query: str, top_k: int = 3, vector_weight: float = 0.5) -> dict:
    """계약: {"documents": [{"title","content","section","rrf_score","similarity","type"}]}"""
    v_results = vector_search(query, top_k=20)
    k_results = keyword_search(query, top_k=20)
    fused_chunks: List[RetrievedChunk] = reciprocal_rank_fusion(v_results, k_results, vector_weight=vector_weight)[:top_k]

    return fused_chunks


if __name__ == "__main__":
    build_index(support_qa_path="./qa_reviewed_20260804.json")

    test_questions = [
        "팀을 삭제하면 어떻게 돼?",
        "스프린트 기간은 최대 몇 주까지 설정할 수 있어?",
        "학생이면 뤼이도 유료 요금제 무료로 쓸 수 있어?",
        "PR 연동 문제 해결하는 방법",
        "회원 탈퇴 어떻게 해?",
    ]

    # vector_weight 두 가지만 비교 (0.5=벡터·키워드 동등, 0.9=벡터 위주)
    for vw in [0.5, 0.9]:
        print(f"\n{'='*20} vector_weight={vw} {'='*20}")
        for q in test_questions:
            result = search(q, vector_weight=vw)
            print(f"\n[질문] {q}")
            for i, d in enumerate(result, 1):
                sim = f"{d.v_similarity:.4f}" if d.v_similarity is not None else "N/A"
                print(f"  {i}. [{d.source_type}] [{d.section}] (similarity={sim}, rrf={d.rrf_score:.4f})")
                print(f"     {d.content[:300]}...")
            print("-" * 50)