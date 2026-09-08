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
    /ask 한 턴을 기록한다. 답변을 보내기 전에 동기로 부른다.

    평가는 응답 뒤에 돌지만 이 행은 먼저 있어야 한다 — 행이 없으면 나중에
    재평가할 대상 자체를 찾을 수 없다.

    같은 uuid가 이미 있으면 아무것도 하지 않는다. uuid4가 겹칠 일은 없고,
    재시도로 두 번 들어오는 경우에만 걸린다.
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


# 로그 1건의 평가 상태. 셋은 서로 겹치지 않고 모든 행이 셋 중 하나에 들어간다.
#   evaluated : 평가 행이 있다
#   pending   : 평가 행이 없다. 채점을 놓쳤거나 실패한 것 — 재실행 대상이다
#   skipped   : 인사·잡담이라 애초에 채점 대상이 아니다. 영원히 평가되지 않는다
#
# skipped를 따로 두지 않으면 미평가 목록이 "안녕하세요"로 가득 찬다
# (rag_service.evaluate_and_store가 no_search를 그냥 건너뛰기 때문에 절대 줄지 않는다).
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
    status: Optional[str],
    answer_type: Optional[str],
    conversation_id: Optional[str],
    q: Optional[str],
) -> Tuple[str, list]:
    clauses, params = [], []
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
    status: Optional[str] = None,
    answer_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    질의응답 로그 목록. 최근 것부터 주고, 각 행에 평가 상태를 함께 붙인다.

    status='pending'이 곧 미평가(재실행 대상) 목록이다.
    """
    where, params = _log_filters(status, answer_type, conversation_id, q)
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
    평가 결과를 저장한다. 답변 1건에 평가 1건이라 다시 돌리면 덮어쓴다.

    실패한 평가는 여기까지 오지 않는다 — core.evaluation이 예외를 올리고,
    호출자는 아무것도 저장하지 않는다(행이 없어야 미평가로 다시 잡힌다).
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

# 평가는 항상 로그 1건에 붙는다(FK + ON DELETE CASCADE). 로그 없는 평가는 존재할 수 없어
# 질문 원문을 함께 주려고 INNER JOIN을 쓴다 — 점수만 있는 목록은 콘솔에서 쓸모가 없다.
EVALUATION_FROM = "FROM answer_evaluations e JOIN qna_logs l ON l.qna_uuid = e.qna_uuid"


def _evaluation_filters(
    verdict: Optional[str],
    issue: Optional[str],
    answer_type: Optional[str],
    q: Optional[str],
) -> Tuple[str, list]:
    clauses, params = [], []
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
    verdict: Optional[str] = None,
    issue: Optional[str] = None,
    answer_type: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    저장된 평가 전체 목록. 최근 것부터 준다 — 콘솔에서 먼저 보는 건 방금 들어온 답변이다.

    같은 시각에 들어온 행이 페이지 경계에서 흔들리지 않도록 qna_uuid까지 정렬에 넣는다.
    """
    where, params = _evaluation_filters(verdict, issue, answer_type, q)
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
