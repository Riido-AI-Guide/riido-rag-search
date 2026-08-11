"""
A파트 — search(query) 함수가 이 파일의 산출물
이후 이 함수의 반환값(dict)을 받아서 작업하면 됨

하이브리드 검색: 벡터 + Kiwi 기반 키워드 검색을 RRF로 결합
rrf_score(정렬용)와 similarity(신뢰도 판단용)를 분리해서 반환
"""

import os
import re
import json
import time
import requests
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from kiwipiepy import Kiwi

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "dbname=riido user=postgres password=postgres host=localhost port=5432")
embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2")
kiwi = Kiwi()


def extract_keywords(text: str) -> str:
    """조사/어미는 버리고 명사·동사·형용사만 추출"""
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
            doc_title TEXT,
            section_path TEXT,
            chunk_type TEXT,
            content_tsv TSVECTOR
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv ON doc_chunks USING GIN (content_tsv);")

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


def chunk_riido_docs(raw_text: str, max_chars: int = 600):
    doc_parts = re.split(r"\n# (.+?)\n", raw_text)
    chunks = []
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
                    chunks.append({
                        "content": f"[{path}]\n{text[k:k + max_chars]}",
                        "doc_title": title,
                        "section_path": path,
                        "chunk_type": "guide",
                    })

            if heading:
                current_heading = heading

    return chunks


def chunk_support_qa(json_path: str):
    """마지막 bot 요약 메시지만 사용 (개인화된 상담 대화는 제외)"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = []
    for item in data["items"]:
        bot_msgs = [c["text"] for c in item["conversation"] if c["role"] == "bot"]
        if not bot_msgs or len(bot_msgs[-1]) < 20:
            continue

        chunks.append({
            "content": f"질문: {item['question']}\n답변: {bot_msgs[-1]}",
            "doc_title": "실제 고객 문의",
            "section_path": f"고객문의 > {item['sample_id']}",
            "chunk_type": "support_qa",
        })

    return chunks


def embed_and_store(chunks: list, batch_size: int = 90):
    conn = get_connection()
    cur = conn.cursor()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embeddings.embed_documents([c["content"] for c in batch])

        for chunk, vector in zip(batch, vectors):
            keywords_text = extract_keywords(chunk["content"])
            cur.execute("""
                INSERT INTO doc_chunks (content, embedding, doc_title, section_path, chunk_type, content_tsv)
                VALUES (%s, %s, %s, %s, %s, to_tsvector('simple', %s))
            """, (chunk["content"], vector, chunk["doc_title"], chunk["section_path"], chunk["chunk_type"], keywords_text))

        conn.commit()
        print(f"  → {min(i + batch_size, len(chunks))}/{len(chunks)}개 임베딩 완료")

        if i + batch_size < len(chunks):
            time.sleep(60)  # rate limit 대응

    cur.close()
    conn.close()


def build_index(support_qa_path: str = None):
    sample_vector = embeddings.embed_query("테스트")
    setup_database(embedding_dim=len(sample_vector))

    if not is_db_empty():
        print("📂 이미 데이터가 있어 재수집을 건너뜁니다.")
        return

    res = requests.get("https://docs.riido.io/llms-full.txt")
    guide_chunks = chunk_riido_docs(res.text)
    print(f"✅ 이용가이드 {len(guide_chunks)}개 청크")
    embed_and_store(guide_chunks)

    if support_qa_path:
        time.sleep(60)
        qa_chunks = chunk_support_qa(support_qa_path)
        print(f"✅ 고객 QA {len(qa_chunks)}개 청크")
        embed_and_store(qa_chunks)


def vector_search(query: str, top_k: int = 20):
    query_vector = embeddings.embed_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, content, doc_title, section_path, chunk_type,
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
        SELECT id, content, doc_title, section_path, chunk_type,
               ts_rank(content_tsv, plainto_tsquery('simple', %s)) AS rank
        FROM doc_chunks WHERE content_tsv @@ plainto_tsquery('simple', %s)
        ORDER BY rank DESC LIMIT %s
    """, (query_keywords, query_keywords, top_k))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def reciprocal_rank_fusion(vector_results, keyword_results, k: int = 60):
    scores = {}
    for rank, row in enumerate(vector_results):
        scores.setdefault(row["id"], {"data": row, "score": 0})
        scores[row["id"]]["score"] += 1 / (k + rank + 1)
    for rank, row in enumerate(keyword_results):
        scores.setdefault(row["id"], {"data": row, "score": 0})
        scores[row["id"]]["score"] += 1 / (k + rank + 1)
    return sorted(scores.values(), key=lambda x: x["score"], reverse=True)


def search(query: str, top_k: int = 3) -> dict:
    """B파트와 합의: {"documents": [{"title","content","section","rrf_score","similarity","type"}]}"""
    v_results = vector_search(query, top_k=20)
    k_results = keyword_search(query, top_k=20)
    fused = reciprocal_rank_fusion(v_results, k_results)[:top_k]

    similarity_map = {row["id"]: row["similarity"] for row in v_results}

    documents = []
    for item in fused:
        row = item["data"]
        documents.append({
            "title": row["doc_title"],
            "content": row["content"],
            "section": row["section_path"],
            "rrf_score": item["score"],
            "similarity": similarity_map.get(row["id"]),
            "type": row["chunk_type"],
        })

    return {"documents": documents}


#### 테스트용 #####

if __name__ == "__main__":
    build_index(support_qa_path="./qa_reviewed_20260804.json")

    test_questions = [
        "팀을 삭제하면 어떻게 돼?",
        "스프린트 기간은 최대 몇 주까지 설정할 수 있어?",
        "학생이면 뤼이도 유료 요금제 무료로 쓸 수 있어?",
        "PR 연동 문제 해결하는 방법",
        "회원 탈퇴 어떻게 해?",
    ]

    for q in test_questions:
        result = search(q)
        print(f"\n[질문] {q}")
        for i, d in enumerate(result["documents"], 1):
            sim = f"{d['similarity']:.4f}" if d["similarity"] is not None else "N/A"
            print(f"  {i}. [{d['type']}] [{d['section']}] (similarity={sim}, rrf={d['rrf_score']:.4f})")
            print(f"     {d['content'][:80]}...")
        print("-" * 50)
        time.sleep(5)