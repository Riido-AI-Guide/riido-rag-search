"""
api/repositories/qna_repository.py — 질의응답 로그·평가 읽기/쓰기
"""

from typing import Any, Dict, List, Optional

from core.db import get_cursor
from domain import AnswerEvaluation

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
