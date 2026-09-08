"""
api/repositories/units_repository.py — DB 읽기 전용 조회
"""

from typing import Any, Dict, List, Optional, Tuple

from core.db import get_cursor

ANSWER_COLUMNS = (
    "a.doc_id, a.title, a.section, a.source_type, a.content, a.ord_idx, "
    "COALESCE(a.source_url, '') AS source_url"
)
SEARCH_COLUMNS = "id, doc_id, view_type, text"

# 문서에 달린 검색 문장의 유형별 갯수
VIEW_TYPE_COUNTS_SQL = """
    LEFT JOIN LATERAL (
        SELECT jsonb_object_agg(view_type, cnt) AS view_types
        FROM (SELECT view_type, COUNT(*)::int AS cnt
                FROM search_units WHERE doc_id = a.doc_id GROUP BY view_type) t
    ) v ON TRUE
"""


# ---------------------------------------------------------------------------
# answer_units
# ---------------------------------------------------------------------------

def _answer_filters(source_type: Optional[str], q: Optional[str]) -> Tuple[str, list]:
    clauses, params = [], []
    if source_type:
        clauses.append("source_type = %s")
        params.append(source_type)
    if q:
        clauses.append("(section ILIKE %s OR content ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_answer_units(
    limit: int, offset: int, source_type: Optional[str] = None, q: Optional[str] = None
) -> Tuple[int, List[Dict[str, Any]]]:
    where, params = _answer_filters(source_type, q)
    # 필터는 answer_units 컬럼만 보므로 별칭을 붙여도 그대로 쓸 수 있다
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM answer_units a{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT {ANSWER_COLUMNS}, COALESCE(v.view_types, '{{}}'::jsonb) AS view_types "
            f"FROM answer_units a{VIEW_TYPE_COUNTS_SQL}{where} "
            f"ORDER BY a.source_type, a.ord_idx, a.doc_id LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def get_answer_unit(doc_id: str) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(f"SELECT {ANSWER_COLUMNS} FROM answer_units a WHERE a.doc_id = %s;", (doc_id,))
        return cur.fetchone()


def get_answer_contents(doc_ids: List[str]) -> Dict[str, str]:
    """
    {doc_id: 본문}. 재평가할 때 근거 문서를 다시 읽음
    """
    if not doc_ids:
        return {}

    with get_cursor() as cur:
        cur.execute("SELECT doc_id, content FROM answer_units WHERE doc_id = ANY(%s);", (doc_ids,))
        return {r["doc_id"]: r["content"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# search_units
# ---------------------------------------------------------------------------

def _search_filters(view_type: Optional[str], doc_id: Optional[str], q: Optional[str]) -> Tuple[str, list]:
    clauses, params = [], []
    if view_type:
        clauses.append("view_type = %s")
        params.append(view_type)
    if doc_id:
        clauses.append("doc_id = %s")
        params.append(doc_id)
    if q:
        clauses.append("text ILIKE %s")
        params.append(f"%{q}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_search_units(
    limit: int,
    offset: int,
    view_type: Optional[str] = None,
    doc_id: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    where, params = _search_filters(view_type, doc_id, q)
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM search_units{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT {SEARCH_COLUMNS} FROM search_units{where} "
            f"ORDER BY doc_id, view_type, id LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def list_search_units_by_doc(doc_id: str) -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {SEARCH_COLUMNS} FROM search_units WHERE doc_id = %s ORDER BY view_type, id;",
            (doc_id,),
        )
        return cur.fetchall()


def view_type_stats() -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT view_type, COUNT(*) AS units, COUNT(DISTINCT doc_id) AS docs
            FROM search_units GROUP BY view_type ORDER BY view_type;
        """)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

def table_status(table: str) -> Tuple[bool, int]:
    """(존재 여부, 행 수). 테이블이 없으면 (False, 0)"""
    with get_cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS oid;", (table,))
        if cur.fetchone()["oid"] is None:
            return False, 0
        cur.execute(f"SELECT COUNT(*) AS total FROM {table};")  # 식별자라 파라미터 바인딩 불가
        return True, cur.fetchone()["total"]


# ---------------------------------------------------------------------------
# 인덱스 건강 상태
#
# 세 가지 낡음을 본다. 셋 다 조용히 나빠지는 종류라 화면에 세워두지 않으면 아무도 모른다.
#   - 검색 문장 없음 : 벡터 검색(search_units)에서 절대 안 걸린다
#   - 원문 벡터 없음 : 키워드 검색(answer_content_vectors)에서 안 걸린다
#   - 원문 벡터 낡음 : 옛 본문으로 색인되어 있다. 답변은 최신인데 검색만 과거다
# ---------------------------------------------------------------------------

# 목록은 표본만 준다 — 179개가 통째로 오면 화면에도 응답에도 부담이다
STALE_SAMPLE_SIZE = 20


def _stale_docs(cur, sql: str) -> Tuple[int, List[str]]:
    """(전체 건수, 앞에서 STALE_SAMPLE_SIZE개)"""
    cur.execute(sql)
    doc_ids = [r["doc_id"] for r in cur.fetchall()]
    return len(doc_ids), doc_ids[:STALE_SAMPLE_SIZE]


def index_status() -> Dict[str, Any]:
    with get_cursor() as cur:
        cur.execute("SELECT to_regclass('answer_content_vectors') AS oid;")
        has_content_table = cur.fetchone()["oid"] is not None

        # source_hash는 나중에 붙은 컬럼이다. 아직 없다면 그 인덱스가 어느 본문을
        # 색인한 것인지 알 방법이 없으므로, 있는 행 전부를 낡은 것으로 본다
        # (build_search_units를 한 번 돌리면 컬럼이 생기고 실제 비교로 바뀐다).
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'answer_content_vectors' AND column_name = 'source_hash';
        """)
        has_hash_column = cur.fetchone() is not None

        cur.execute("""
            SELECT COUNT(*) AS units, MAX(updated_at) AS built_at FROM answer_units;
        """)
        row = cur.fetchone()

        no_search_units = _stale_docs(cur, """
            SELECT a.doc_id FROM answer_units a
            LEFT JOIN search_units s ON s.doc_id = a.doc_id
            WHERE s.doc_id IS NULL ORDER BY a.doc_id;
        """)

        if has_content_table:
            no_content_vector = _stale_docs(cur, """
                SELECT a.doc_id FROM answer_units a
                LEFT JOIN answer_content_vectors v ON v.doc_id = a.doc_id
                WHERE v.doc_id IS NULL ORDER BY a.doc_id;
            """)
            # source_hash가 비어 있는 행도 어느 본문 기준인지 알 수 없어 낡은 것으로 본다
            outdated_content_vector = _stale_docs(cur, """
                SELECT a.doc_id FROM answer_units a
                JOIN answer_content_vectors v ON v.doc_id = a.doc_id
                WHERE v.source_hash IS DISTINCT FROM a.source_hash ORDER BY a.doc_id;
            """ if has_hash_column else """
                SELECT a.doc_id FROM answer_units a
                JOIN answer_content_vectors v ON v.doc_id = a.doc_id ORDER BY a.doc_id;
            """)
        else:
            # 테이블 자체가 없으면 "문서 전부가 키워드 검색 불가"다
            no_content_vector = (row["units"], [])
            outdated_content_vector = (0, [])

        return {
            "answer_units": row["units"],
            "built_at": row["built_at"],
            "has_content_table": has_content_table,
            "no_search_units": no_search_units,
            "no_content_vector": no_content_vector,
            "outdated_content_vector": outdated_content_vector,
        }


# ---------------------------------------------------------------------------
# 검색 문장 커버리지 (운영 콘솔)
#
# 문서 하나에 검색 문장이 몇 개 붙어 있는지, 그리고 그 문장이 지금 본문 기준인지를 본다.
#   missing  : 문장이 하나도 없다 → 벡터 검색에서 절대 안 걸린다
#   outdated : 문장은 있는데 본문이 그 뒤로 바뀌었다 → 옛 내용으로 걸린다
#   ok       : 지금 본문 기준의 문장이 있다
# ---------------------------------------------------------------------------

# 상태를 SQL 한 곳에서만 정의한다. 목록·상세·필터가 모두 이 식을 쓴다.
COVERAGE_SQL = """
    SELECT a.doc_id, a.title, a.section, a.ord_idx, a.updated_at,
           COALESCE(s.units, 0)                       AS units,
           COALESCE(s.outdated, 0)                    AS outdated,
           COALESCE(s.view_types, '{}'::jsonb)        AS view_types,
           CASE WHEN COALESCE(s.units, 0) = 0     THEN 'missing'
                WHEN COALESCE(s.outdated, 0) > 0  THEN 'outdated'
                ELSE 'ok' END                         AS status
    FROM answer_units a
    LEFT JOIN LATERAL (
        SELECT COUNT(*)::int AS units,
               COUNT(*) FILTER (
                   WHERE su.source_hash IS DISTINCT FROM a.source_hash
               )::int AS outdated,
               (SELECT jsonb_object_agg(view_type, cnt)
                  FROM (SELECT view_type, COUNT(*)::int AS cnt
                          FROM search_units
                         WHERE doc_id = a.doc_id
                         GROUP BY view_type) t) AS view_types
        FROM search_units su WHERE su.doc_id = a.doc_id
    ) s ON TRUE
"""


def search_coverage(
    limit: int,
    offset: int,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    문서별 검색 문장 현황. 손볼 것이 위로 오도록 missing → outdated → ok 순으로 준다
    (콘솔에서 먼저 보는 건 검색이 안 되는 문서다).
    """
    clauses, params = [], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if q:
        clauses.append("(doc_id ILIKE %s OR section ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_cursor() as cur:
        cur.execute(f"WITH docs AS ({COVERAGE_SQL}) SELECT COUNT(*) AS total FROM docs{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"WITH docs AS ({COVERAGE_SQL}) SELECT * FROM docs{where} "
            "ORDER BY CASE status WHEN 'missing' THEN 0 WHEN 'outdated' THEN 1 ELSE 2 END, "
            "ord_idx, doc_id LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def search_coverage_detail(doc_id: str) -> Optional[Dict[str, Any]]:
    """문서 1건의 현황 + 본문. 문장 목록은 list_doc_sentences가 따로 준다."""
    with get_cursor() as cur:
        cur.execute(
            f"WITH docs AS ({COVERAGE_SQL}) "
            "SELECT d.*, a.content, COALESCE(a.source_url, '') AS source_url "
            "FROM docs d JOIN answer_units a ON a.doc_id = d.doc_id WHERE d.doc_id = %s;",
            (doc_id,),
        )
        return cur.fetchone()


def list_doc_sentences(doc_id: str) -> List[Dict[str, Any]]:
    """그 문서의 검색 문장 전체. 문장마다 지금 본문 기준인지(outdated)를 함께 준다."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.doc_id, s.view_type, s.text, s.source, s.updated_at,
                   (s.source_hash IS DISTINCT FROM a.source_hash) AS outdated
            FROM search_units s JOIN answer_units a ON a.doc_id = s.doc_id
            WHERE s.doc_id = %s ORDER BY s.view_type, s.id;
        """, (doc_id,))
        return cur.fetchall()


def get_answer_unit_for_draft(doc_id: str) -> Optional[Dict[str, Any]]:
    """초안 생성·문장 등록에 필요한 최소 정보 (본문과 지금 본문의 해시)"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT doc_id, section, content, source_hash FROM answer_units WHERE doc_id = %s;",
            (doc_id,),
        )
        return cur.fetchone()


def get_sentence_vectors(doc_id: str) -> Dict[str, str]:
    """
    {문장: 임베딩}. 텍스트가 그대로인 문장은 임베딩도 그대로라 다시 만들지 않는다
    (유형만 고쳐 저장하는 경우가 흔하다).
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT text, embedding::text AS embedding FROM search_units WHERE doc_id = %s;",
            (doc_id,),
        )
        return {r["text"]: r["embedding"] for r in cur.fetchall()}


def replace_doc_sentences(doc_id: str, rows: List[Dict[str, Any]]) -> None:
    """
    그 문서의 검색 문장을 rows와 같은 상태로 만든다 (완전 덮어쓰기)

    삭제와 등록이 한 트랜잭션이다(get_cursor가 블록 끝에서 commit한다). 중간에 실패하면 문장이 반쯤 지워진 상태로 남지 않는다.

    JSON에 있는 문장을 지운 경우에는 다음 빌드가 그 문장을 다시 넣는다(파일이 아직 그
    문장을 갖고 있으므로). 완전히 없애려면 rag_view_sentences.json에서도 빼야 한다.
    """
    texts = [row["text"] for row in rows]
    with get_cursor() as cur:
        # 보낸 목록에 없는 문장 삭제. 빈 목록이면 이 문서의 문장이 전부 지워진다
        cur.execute(
            "DELETE FROM search_units WHERE doc_id = %s AND text <> ALL(%s);",
            (doc_id, texts),
        )
        for row in rows:
            cur.execute("""
                INSERT INTO search_units
                    (doc_id, view_type, text, text_tsv, embedding, source, source_hash)
                VALUES (%s, %s, %s, to_tsvector('simple', %s), %s::vector, 'console', %s)
                ON CONFLICT (doc_id, text) DO UPDATE SET
                    view_type   = EXCLUDED.view_type,
                    text_tsv    = EXCLUDED.text_tsv,
                    embedding   = EXCLUDED.embedding,
                    source      = 'console',
                    source_hash = EXCLUDED.source_hash,
                    updated_at  = now();
            """, (row["doc_id"], row["view_type"], row["text"], row["text_tsv"],
                  row["embedding"], row["source_hash"]))


def export_view_sentences() -> List[Dict[str, Any]]:
    """
    rag_view_sentences.json과 같은 모양·같은 순서로 문장 전체를 준다.

    정렬은 파일과 맞춘다(doc_id → view_type → 적재 순)
    """
    with get_cursor() as cur:
        cur.execute("""
            SELECT text, view_type, doc_id FROM search_units
            ORDER BY doc_id, view_type, id;
        """)
        return [dict(r) for r in cur.fetchall()]
