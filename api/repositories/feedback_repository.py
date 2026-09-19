"""
api/repositories/feedback_repository.py — 사용자 피드백(app.message_feedbacks) 대조 조회

백엔드 소유 스키마(사용자 피드백)를 읽는 유일한 곳 (READ만 한다!)
— app 스키마에 CREATE/ALTER/DROP을 하지 않는다(그쪽의 마이그레이션이 주인이다)
"""

from typing import Any, Dict, List, Optional, Tuple

from core.db import get_cursor

FEEDBACK_TABLE = "app.message_feedbacks"


class BackendSchemaMissing(Exception):
    """이 DB에 백엔드 스키마(app)가 없다. 백엔드를 함께 띄우지 않은 개발 환경이다."""


# 백엔드도 qna_uuid를 uuid로 저장하므로 캐스팅 없이 그대로 조인한다.
# (qna_logs·answer_evaluations의 PK 인덱스를 그대로 탄다)
FEEDBACK_FROM = f"""
    FROM {FEEDBACK_TABLE} f
    LEFT JOIN qna_logs l           ON l.qna_uuid = f.qna_uuid
    LEFT JOIN answer_evaluations e ON e.qna_uuid = f.qna_uuid
"""

# 사용자 평가와 판정자 평가가 같은 방향인지 대조
AGREEMENT_SQL = """
    CASE
        WHEN e.verdict IS NULL THEN 'unevaluated'
        WHEN (f.rating = 'GOOD' AND e.verdict = 'pass')
          OR (f.rating = 'BAD'  AND e.verdict = 'fail') THEN 'match'
        ELSE 'mismatch'
    END
"""

FEEDBACK_COLUMNS = f"""
    f.id, f.message_id, f.qna_uuid, f.rating, f.reason AS feedback_reason,
    f.created_at AS feedback_created_at, f.updated_at AS feedback_updated_at,
    l.raw_query, l.cleaned_query, l.answer_type, l.conversation_id,
    l.retrieved_doc_ids, l.created_at AS answered_at,
    e.verdict, e.faithfulness, e.answer_relevance, e.context_relevance,
    e.issues, e.reason AS eval_reason,
    ({AGREEMENT_SQL}) AS agreement
"""


def _ensure_schema(cur) -> None:
    cur.execute("SELECT to_regclass(%s) AS oid;", (FEEDBACK_TABLE,))
    if cur.fetchone()["oid"] is None:
        raise BackendSchemaMissing(FEEDBACK_TABLE)


def list_feedback(
    limit: int,
    offset: int,
    rating: Optional[str] = None,
    reason: Optional[str] = None,
    agreement: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    피드백 최근 순 조회. 답변 본문(answer_text)은 미포함
    """
    clauses, params = [], []
    if rating:
        clauses.append("rating = %s")
        params.append(rating)
    if reason:
        clauses.append("feedback_reason = %s")
        params.append(reason)
    if agreement:
        clauses.append("agreement = %s")
        params.append(agreement)
    if q:
        clauses.append("(raw_query ILIKE %s OR cleaned_query ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_cursor() as cur:
        _ensure_schema(cur)
        cte = f"WITH rows AS (SELECT {FEEDBACK_COLUMNS} {FEEDBACK_FROM})"

        cur.execute(f"{cte} SELECT COUNT(*) AS total FROM rows{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"{cte} SELECT * FROM rows{where} "
            "ORDER BY feedback_created_at DESC, id DESC LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def get_feedback(qna_uuid: str) -> Optional[Dict[str, Any]]:
    """
    qna_uuid로 피드백 1건 + 그 턴의 로그·평가 전체(답변 본문 포함) 조회. (메세지와 피드백은 1:1)
    """
    with get_cursor() as cur:
        _ensure_schema(cur)
        cur.execute(
            f"SELECT {FEEDBACK_COLUMNS}, l.answer_text {FEEDBACK_FROM} "
            "WHERE f.qna_uuid = %s ORDER BY f.created_at DESC LIMIT 1;",
            (qna_uuid,),
        )
        return cur.fetchone()


def agreement_stats() -> List[Dict[str, Any]]:
    """rating × verdict 교차표. 콘솔 상단 요약용"""
    with get_cursor() as cur:
        _ensure_schema(cur)
        cur.execute(f"""
            SELECT f.rating, e.verdict, ({AGREEMENT_SQL}) AS agreement, COUNT(*) AS count
            {FEEDBACK_FROM}
            GROUP BY f.rating, e.verdict, agreement
            ORDER BY f.rating, e.verdict NULLS LAST;
        """)
        return cur.fetchall()
