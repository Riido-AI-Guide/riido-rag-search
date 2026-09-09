"""
api/services/rag_service.py — 질의 → 검색 → 답변 오케스트레이션과, 그 뒤에 도는 평가
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Set

from domain import (
    NO_SEARCH_ANSWER_TYPE, AnswerEvaluation, AnswerSection, ConversationTurn,
    RetrievedChunk, sections_to_text,
)
from api.repositories import qna_repository, units_repository
from core.evaluation import EvaluationError, evaluate_answer
from core.generation import generate_rag_answer
from core.query_transform import normalize_title, transform_user_query
from core.search import search as rag_search

logger = logging.getLogger(__name__)

# 미검색 시 메시지
NO_SEARCH_MESSAGE = "안녕하세요! 뤼이도 이용 가이드에 대해 궁금한 점을 물어봐 주세요."

# 대화 도중 미검색 시 메시지
NO_SEARCH_FOLLOW_UP_MESSAGE = "더 궁금한 점이 있으면 말씀해 주세요."

# 인사·잡담으로 대화를 시작했을 때의 제목. 그 경로엔 답변이 없어 뽑아 쓸 제목이 없다.
NO_SEARCH_TITLE = "새 대화"


@dataclass
class AskResult:
    # 이 턴의 식별자. /ask가 발급해 응답에 실어 보내고, 로그·평가가 이 값으로 이어진다.
    # 백엔드의 메시지 id와는 다른 값이다 — 그쪽은 답변을 저장한 뒤에야 생긴다.
    qna_uuid: str

    raw_query: str
    cleaned_query: str
    needs_search: bool

    # 답변의 유형. Answer.answer_type을 그대로 실어 나르되, 검색을 건너뛴 경로만
    # 여기서 no_search로 채운다(그 경로엔 Answer가 아예 없다).
    answer_type: str

    # 이 턴의 제목. Answer.title을 다듬어 쓴다 — 제목 전용 LLM 호출은 하지 않는다.
    # 첫 턴이면 이 값이 곧 대화 제목이고, 후속 턴이면 말풍선 제목이다.
    # 첫 턴에서는 비지 않는다(비면 질문 원문이나 "새 대화"로 채운다).
    title: str

    # 답변 그 자체. Answer.sections를 그대로 실어 나른다.
    # 평문 필드를 따로 두지 않는다 — 같은 내용을 두 번 들고 있으면 반드시 어긋난다.
    # 어느 경로에서도 비지 않는다: 인사·잡담과 형식 깨짐(parse_error)도
    # label과 sources가 빈 섹션 하나로 들어온다.
    answers: List[AnswerSection]

    documents: List[RetrievedChunk] = field(default_factory=list)

    # 답변 생성에는 쓰지 않고 응답에 그대로 돌려보내기만 한다(호출자의 로그 대조용)
    conversation_id: Optional[str] = None

    @property
    def answer_text(self) -> str:
        """
        판정자에게 넘기고 qna_logs.answer_text에 저장하는 평문.
        """
        return sections_to_text(self.answers)

    @property
    def doc_ids(self) -> List[str]:
        """
        답변이 실제로 인용한 문서. 검색으로 가져온 문서 전체 X
        """
        seen = set()
        ordered: List[str] = []
        for section in self.answers:
            for ref in section.sources:
                if ref.doc_id not in seen:
                    seen.add(ref.doc_id)
                    ordered.append(ref.doc_id)
        return ordered


def ask(
    query: str,
    top_k: int,
    vector_weight: float,
    history: Optional[List[ConversationTurn]] = None,
    conversation_id: Optional[str] = None,
    max_history_turns: int = 5,
) -> AskResult:
    """
    사용자 질문 하나를 끝까지 처리한다. LLM 호출 2회 + 임베딩 1회
    (질문 재작성 1회 + 답변 생성 1회. 인사·잡담이면 재작성 1회로 끝난다).

    history를 주면 후속 질문의 대명사·생략을 앞 턴에서 풀어 검색어를 만든다.
    비어 있으면(첫 대화) 예전과 완전히 같은 단일턴 경로로 흐른다.

    conversation_id는 처리에 쓰지 않는다. 대화의 소유자는 백엔드이고 여기서는
    응답·로그에 실어 보내기만 한다(운영 콘솔에서 평가와 대화를 잇는 조인 키).

    제목(title)은 매 턴 함께 돌려준다. 답변 생성이 이미 만든 Answer.title을 그대로
    쓰므로 제목 때문에 LLM을 더 부르지 않는다. 첫 턴의 title을 대화 제목으로 삼을지는
    호출자가 정한다 — 대화의 소유자는 여기가 아니다.
    """
    # 최근 턴만 남긴다. 요청이 더 많이 보내와도 서버 정책이 상한이다.
    # (오래된 턴은 지나간 주제로 재작성을 오염시키고 비용만 늘린다)
    recent_history = (history or [])[-max_history_turns:] if max_history_turns > 0 else []

    # 첫 턴 판정(history, conversation_id가 둘 다 없을 때).
    # 제목을 뽑을 답변이 없는 인사·잡담 경로에서만 쓴다.
    is_first_turn = not history and not conversation_id

    # 이 턴의 식별자. 모든 경로에서 발급
    qna_uuid = str(uuid.uuid4())

    transformed = transform_user_query(query, history=recent_history)

    # 인사·잡담이면 검색, 생성 x
    if not transformed.needs_search:
        result = AskResult(
            qna_uuid=qna_uuid,
            raw_query=query,
            cleaned_query=transformed.cleaned_query,
            needs_search=False,
            answer_type=NO_SEARCH_ANSWER_TYPE,
            # 첫 턴이면 대화 목록에 걸 이름이 필요하지만, 후속 턴의 "고마워"에까지
            # "새 대화"를 붙일 이유는 없다 — 그쪽은 제목 없는 말풍선으로 둔다.
            title=NO_SEARCH_TITLE if is_first_turn else "",
            answers=[AnswerSection(
                label="",
                text=NO_SEARCH_FOLLOW_UP_MESSAGE if recent_history else NO_SEARCH_MESSAGE,
            )],
            conversation_id=conversation_id,
        )
        _save_log(result)
        return result

    # 검색과 생성에는 재작성(전처리)된 질문만 넘긴다. -> 이후 단계는 단일턴과 동일
    # hits(문장 단위 점수)는 응답에 싣지 않는다 — 근거는 문서 단위로만 준다
    _, documents = rag_search(
        transformed.cleaned_query, top_k=top_k, vector_weight=vector_weight
    )

    # LlmError는 잡지 않는다. 답변 생성 실패는 요청 실패이므로 그대로 올려보낸다.
    answer = generate_rag_answer(transformed.cleaned_query, documents)


    result = AskResult(
        qna_uuid=qna_uuid,
        raw_query=query,
        cleaned_query=transformed.cleaned_query,
        needs_search=True,
        answer_type=answer.answer_type,
        title=normalize_title(answer.title, query),
        answers=answer.sections,
        documents=documents,
        conversation_id=conversation_id,
    )
    _save_log(result)
    return result


# ---------------------------------------------------------------------------
# 로그와 평가
# ---------------------------------------------------------------------------

def _save_log(result: AskResult) -> None:
    """
    이 턴을 기록한다. 응답을 보내기 전에 동기로 부른다.

    평가는 응답 뒤에 돌지만 이 행은 먼저 있어야 한다 — 프로세스가 죽어 평가를 놓쳐도
    행이 남아 있으면 운영 콘솔에서 다시 돌릴 수 있고, 없으면 대상 자체를 찾을 수 없다.
    INSERT 1건이라 LLM 2회 옆에서는 무시할 만한 비용이다.

    실패해도 답변은 그대로 나간다. 로그보다 답변이 중요하다 — 다만 조용히 유실되므로
    테이블이 없는 상황은 /health가 알린다.
    """
    try:
        qna_repository.insert_log(
            qna_uuid=result.qna_uuid,
            raw_query=result.raw_query,
            cleaned_query=result.cleaned_query,
            answer_text=result.answer_text,
            answer_type=result.answer_type,
            retrieved_doc_ids=[d.doc_id for d in result.documents],
            conversation_id=result.conversation_id,
        )
    except Exception:
        logger.warning(
            "질의응답 로그 저장 실패 (답변은 정상 반환): %s", result.qna_uuid, exc_info=True
        )


class QnaLogNotFound(Exception):
    """
    재실행할 로그가 없다. 대화가 지워졌거나, 답변은 나갔지만 로그 저장이 실패한 턴이다.
    다시 시도해도 생기지 않으므로 재실행 API는 404로 답한다.
    """


class NotEvaluableError(Exception):
    """
    채점 대상이 아닌 턴(인사·잡담). 근거 문서도 답변도 없어 채점할 것이 없고,
    몇 번을 돌려도 평가 행이 생기지 않는다 — 실패가 아니라 대상이 아닌 것이다.
    """


def run_evaluation(qna_uuid: str) -> AnswerEvaluation:
    """
    로그 1건을 읽어 채점하고 저장한 뒤 결과를 돌려준다.

    실패를 예외로 올리는 쪽이다 — 운영 콘솔의 재실행처럼 **결과를 기다리는 호출자**가
    쓴다. 왜 실패했는지(로그가 없다/채점 대상이 아니다/판정자가 깨졌다)를 구분해야
    화면에 다른 말을 띄울 수 있다.

    이미 평가된 턴을 다시 돌리면 덮어쓴다(답변 1건에 평가 1건). 프롬프트를 고치고
    다시 채점하는 것이 이 함수의 두 번째 용도다.
    """
    log = qna_repository.get_log(qna_uuid)
    if log is None:
        raise QnaLogNotFound(qna_uuid)

    # 인사·잡담은 근거 문서도 답변도 없어 채점할 것이 없다
    if log["answer_type"] == NO_SEARCH_ANSWER_TYPE:
        raise NotEvaluableError(qna_uuid)

    # 로그에는 doc_id만 남기므로 본문은 여기서 다시 읽는다.
    # 검색된 순서를 지켜야 판정자가 보는 [참고 문서 N] 번호가 답변 당시와 같아진다.
    doc_ids = list(log["retrieved_doc_ids"])
    contents = units_repository.get_answer_contents(doc_ids)

    missing = [d for d in doc_ids if d not in contents]
    if missing:
        # 재색인으로 사라진 문서. 남은 것만으로 채점한다 — 그 사실은 여기 로그에만 남는다
        logger.warning("근거 문서가 사라져 일부를 빼고 평가한다 %s: %s", qna_uuid, missing)

    evaluation = evaluate_answer(
        question=log["cleaned_query"],
        context_documents=[contents[d] for d in doc_ids if d in contents],
        generated_answer=log["answer_text"],
    )
    qna_repository.upsert_evaluation(qna_uuid, evaluation)
    return evaluation


# 지금 백그라운드에서 채점 중인 턴. 같은 턴을 두 번 채점하지 않기 위한 것이다 —
# 일괄 재실행 버튼을 두 번 누르면 그만큼 판정자 LLM 비용이 그대로 두 배가 된다.
#
# 프로세스 안에서만 유효한 자물쇠다. 워커를 여러 개 띄우면 워커별로 따로 잡히지만,
# 그때도 결과는 덮어쓰기(upsert)라 망가지지 않는다 — 비용만 든다.
_in_flight: Set[str] = set()
_in_flight_lock = threading.Lock()


def evaluate_and_store(qna_uuid: str) -> None:
    """
    run_evaluation을 감싸 예외를 삼킨다. /ask 응답을 보낸 뒤 백그라운드로 도는 쪽이고,
    일괄 재실행도 이 함수를 턴 수만큼 예약한다.

    호출자가 없는 자리에서 도는 코드라 예외를 밖으로 내보내지 않는다. 실패하면
    아무것도 저장하지 않고 미평가로 남는다 — GET /qna?status=pending으로 다시 찾아
    재실행할 수 있다.

    이미 같은 턴이 돌고 있으면 아무것도 하지 않는다.
    """
    with _in_flight_lock:
        if qna_uuid in _in_flight:
            logger.info("이미 채점 중이라 건너뛴다: %s", qna_uuid)
            return
        _in_flight.add(qna_uuid)

    try:
        run_evaluation(qna_uuid)
    except QnaLogNotFound:
        logger.warning("평가할 로그가 없다: %s", qna_uuid)
    except NotEvaluableError:
        pass  # 인사·잡담. 정상 경로다
    except EvaluationError as e:
        logger.warning("답변 평가 실패 — 미평가로 남는다 %s: %s", qna_uuid, e)
    except Exception:
        logger.exception("답변 평가 중 오류 — 미평가로 남는다 %s", qna_uuid)
    finally:
        with _in_flight_lock:
            _in_flight.discard(qna_uuid)
