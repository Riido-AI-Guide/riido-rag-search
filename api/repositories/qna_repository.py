"""
api/repositories/qna_repository.py — 질의응답 로그·평가 읽기/쓰기
"""

from typing import Any, Dict, List, Optional, Tuple

from core.db import get_cursor
from domain import NO_SEARCH_ANSWER_TYPE, AnswerEvaluation

LOG_COLUMNS = (
    "qna_uuid, conversation_id, raw_query, cleaned_query, "
    "answer_text, answer_type, retrieved_doc_ids, created_at"
)


# ---------------------------------------------------------------------------
# qna_logs
# ---------------------------------------------------------------------------

def insert_log(
    qna_uuid: str,
    raw_query: str,
    cleaned_query: str,
    answer_text: str,
    answer_type: str,
    retrieved_doc_ids: List[str],
    conversation_id: Optional[str] = None,
) -> None:
    """
    /ask 한 턴을 기록. 답변을 보내기 전에 동기로 부른다.
    응답 뒤 평가가 돌지 못해도 나중에 재평가할 수 있도록 로그를 남김

    같은 uuid가 이미 있는 경우 무시
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO qna_logs (
                qna_uuid, conversation_id, raw_query, cleaned_query,
                answer_text, answer_type, retrieved_doc_ids
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (qna_uuid) DO NOTHING;
            """,
            (
                qna_uuid, conversation_id, raw_query, cleaned_query,
                answer_text, answer_type, retrieved_doc_ids,
            ),
        )


# 로그 1건의 평가 상태
#   evaluated : 평가 행이 있다
#   pending   : 평가 행이 없다. 채점을 놓쳤거나 실패한 것 — 재실행 대상이다
#   skipped   : 인사·잡담 등 채점이 불필요한 경우
LOG_STATUS_SQL = f"""
    CASE
        WHEN e.qna_uuid IS NOT NULL THEN 'evaluated'
        WHEN l.answer_type = '{NO_SEARCH_ANSWER_TYPE}' THEN 'skipped'
        ELSE 'pending'
    END
"""

LOG_LIST_COLUMNS = (
    "l.qna_uuid, l.conversation_id, l.raw_query, l.cleaned_query, "
    "l.answer_text, l.answer_type, l.retrieved_doc_ids, l.created_at, "
    f"({LOG_STATUS_SQL}) AS status, e.verdict"
)

# 평가는 로그 1건에 최대 1건이라(PK가 qna_uuid) 조인해도 행이 늘지 않는다
LOG_FROM = "FROM qna_logs l LEFT JOIN answer_evaluations e ON e.qna_uuid = l.qna_uuid"


def _log_filters(
    qna_uuids: Optional[List[str]],
    status: Optional[str],
    answer_type: Optional[str],
    conversation_id: Optional[str],
    q: Optional[str],
) -> Tuple[str, list]:
    clauses, params = [], []
    if qna_uuids:
        clauses.append("l.qna_uuid = ANY(%s::uuid[])")
        params.append(list(qna_uuids))
    if status:
        # 계산식을 WHERE에 그대로 쓴다 — 별칭은 WHERE에서 참조할 수 없다
        clauses.append(f"({LOG_STATUS_SQL}) = %s")
        params.append(status)
    if answer_type:
        clauses.append("l.answer_type = %s")
        params.append(answer_type)
    if conversation_id:
        clauses.append("l.conversation_id = %s")
        params.append(conversation_id)
    if q:
        clauses.append("(l.raw_query ILIKE %s OR l.cleaned_query ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_logs(
    limit: int,
    offset: int,
    qna_uuids: Optional[List[str]] = None,
    status: Optional[str] = None,
    answer_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    질의응답 로그 목록. (최근 순)  + 평가 상태

    qna_uuids 목록을 주면 그 턴들만 한 번에 가져온다
    """
    where, params = _log_filters(qna_uuids, status, answer_type, conversation_id, q)
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total {LOG_FROM}{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT {LOG_LIST_COLUMNS} {LOG_FROM}{where} "
            f"ORDER BY l.created_at DESC, l.qna_uuid LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def get_log(qna_uuid: str) -> Optional[Dict[str, Any]]:
    """로그 1건. 없으면 None (평가 대상이 사라졌거나 애초에 기록되지 않은 경우)"""
    with get_cursor() as cur:
        cur.execute(f"SELECT {LOG_COLUMNS} FROM qna_logs WHERE qna_uuid = %s;", (qna_uuid,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# answer_evaluations
# ---------------------------------------------------------------------------

def upsert_evaluation(qna_uuid: str, evaluation: AnswerEvaluation) -> None:
    """
    평가 결과를 저장. 재평가 시 덮어쓰기
    평가 실패 시 저장 x
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO answer_evaluations (
                qna_uuid, faithfulness, answer_relevance, context_relevance,
                verdict, issues, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (qna_uuid) DO UPDATE SET
                faithfulness      = EXCLUDED.faithfulness,
                answer_relevance  = EXCLUDED.answer_relevance,
                context_relevance = EXCLUDED.context_relevance,
                verdict           = EXCLUDED.verdict,
                issues            = EXCLUDED.issues,
                reason            = EXCLUDED.reason,
                updated_at        = now();
            """,
            (
                qna_uuid,
                evaluation.faithfulness,
                evaluation.answer_relevance,
                evaluation.context_relevance,
                evaluation.verdict,
                evaluation.issues,
                evaluation.reason,
            ),
        )


EVALUATION_COLUMNS = (
    "e.qna_uuid, e.faithfulness, e.answer_relevance, e.context_relevance, "
    "e.verdict, e.issues, e.reason, e.created_at, e.updated_at, "
    "l.raw_query, l.cleaned_query, l.answer_type, l.conversation_id"
)

# 평가는 항상 로그 1건에 붙는다(FK + ON DELETE CASCADE)
EVALUATION_FROM = "FROM answer_evaluations e JOIN qna_logs l ON l.qna_uuid = e.qna_uuid"


def _evaluation_filters(
    qna_uuids: Optional[List[str]],
    conversation_id: Optional[str],
    verdict: Optional[str],
    issue: Optional[str],
    answer_type: Optional[str],
    q: Optional[str],
) -> Tuple[str, list]:
    clauses, params = [], []
    if qna_uuids:
        # 화면에 그린 메시지 여러 개의 평가를 한 번에 가져오는 경로
        clauses.append("e.qna_uuid = ANY(%s::uuid[])")
        params.append(list(qna_uuids))
    if conversation_id:
        clauses.append("l.conversation_id = %s")
        params.append(conversation_id)
    if verdict:
        clauses.append("e.verdict = %s")
        params.append(verdict)
    if issue:
        clauses.append("%s = ANY(e.issues)")
        params.append(issue)
    if answer_type:
        clauses.append("l.answer_type = %s")
        params.append(answer_type)
    if q:
        clauses.append("(l.raw_query ILIKE %s OR l.cleaned_query ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_evaluations(
    limit: int,
    offset: int,
    qna_uuids: Optional[List[str]] = None,
    conversation_id: Optional[str] = None,
    verdict: Optional[str] = None,
    issue: Optional[str] = None,
    answer_type: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    저장된 평가 전체 목록 (최근 순)

    qna_uuids 목록을 주면 그 턴들의 평가만 한 번에 가져온다.
    """
    where, params = _evaluation_filters(
        qna_uuids, conversation_id, verdict, issue, answer_type, q
    )
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total {EVALUATION_FROM}{where};", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT {EVALUATION_COLUMNS} {EVALUATION_FROM}{where} "
            f"ORDER BY e.created_at DESC, e.qna_uuid LIMIT %s OFFSET %s;",
            params + [limit, offset],
        )
        return total, cur.fetchall()


def get_evaluation(qna_uuid: str) -> Optional[Dict[str, Any]]:
    """평가 1건. 아직 채점되지 않았거나 채점에 실패했으면 None"""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {EVALUATION_COLUMNS} {EVALUATION_FROM} WHERE e.qna_uuid = %s;",
            (qna_uuid,),
        )
        return cur.fetchone()
