"""
api/repositories/units_repository.py — DB 읽기 전용 조회
"""

from typing import Any, Dict, List, Optional, Tuple

from core.db import get_cursor

ANSWER_COLUMNS = (
    "doc_id, title, section, source_type, content, ord_idx, "
    "COALESCE(source_url, '') AS source_url"
)
SEARCH_COLUMNS = "id, doc_id, view_type, text"


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
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM answer_units{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT {ANSWER_COLUMNS} FROM answer_units{where} "
            f"ORDER BY source_type, ord_idx, doc_id LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def get_answer_unit(doc_id: str) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(f"SELECT {ANSWER_COLUMNS} FROM answer_units WHERE doc_id = %s;", (doc_id,))
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
