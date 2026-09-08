"""
api/repositories/feedback_repository.py — 사용자 피드백(app.message_feedbacks) 대조 조회

**백엔드 소유 스키마를 읽는 유일한 곳이다.** 읽기만 한다 — app 스키마에 CREATE/ALTER/DROP을
하지 않는다(그쪽의 마이그레이션이 주인이다). 파일을 따로 둔 것도 그 경계를 눈에 보이게
하기 위해서다.

/ask 경로에서는 부르지 않는다. 답변 도중에 남의 테이블을 읽으면 그쪽 장애가 답변 실패가 된다.
여기는 운영 콘솔 조회 전용이다.
"""

from typing import Any, Dict, List, Optional, Tuple

from core.db import get_cursor

FEEDBACK_TABLE = "app.message_feedbacks"


class BackendSchemaMissing(Exception):
    """이 DB에 백엔드 스키마(app)가 없다. 백엔드를 함께 띄우지 않은 개발 환경이다."""


# qna_uuid를 문자열로 맞춰 조인한다. 백엔드는 varchar, 이쪽은 uuid라 타입이 다르고,
# f.qna_uuid::uuid로 캐스팅하면 값이 uuid 형식이 아닌 행 하나에 쿼리 전체가 죽는다.
# (행 수가 적어 인덱스를 못 타는 비용은 무시할 만하다. 백엔드가 uuid로 바꾸면 정리된다)
FEEDBACK_FROM = f"""
    FROM {FEEDBACK_TABLE} f
    LEFT JOIN qna_logs l           ON l.qna_uuid::text = f.qna_uuid
    LEFT JOIN answer_evaluations e ON e.qna_uuid::text = f.qna_uuid
"""

# 사용자 평가와 판정자 평가가 같은 방향인지. 이 대조가 이 API의 존재 이유다 —
# 어긋난 건(특히 BAD인데 pass)이 프롬프트를 고칠 표본이다.
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
    피드백을 최근 것부터. 답변 본문(answer_text)은 싣지 않는다 — 단건 조회에 있다.

    피드백은 있는데 우리 로그가 없는 경우가 있다(백엔드가 qna_uuid를 저장하기 전의 메시지,
    또는 로그가 지워진 턴). 그런 행도 빼지 않고 준다 — 로그 쪽 필드가 null이고
    agreement는 unevaluated다. 조용히 사라지면 "피드백 수가 왜 다르지"가 된다.
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
    qna_uuid로 피드백 1건 + 그 턴의 로그·평가 전체(답변 본문 포함).

    메시지 1건에 피드백 1건이고(app 쪽 UNIQUE 제약) 메시지와 qna_uuid가 1:1이라 보통
    한 행이지만, 그 1:1은 DB가 강제하지 않으므로 최신 것을 준다.
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
