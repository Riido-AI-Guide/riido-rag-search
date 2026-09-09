from dataclasses import dataclass
from typing import List, Optional

from domain import Answer, AnswerEvaluation, Query, RetrievedChunk


@dataclass
class QnA:
    """
    저장된 질의응답 한 턴. **질문 1 : 답변 1 : 평가 1**이다.

    답변 시도를 여러 개 두지 않는 이유: 평가는 답변을 이미 보낸 뒤에 돌기 때문에
    점수가 낮다고 그 자리에서 다시 생성할 수 없다. 나중에 재생성을 도입하면
    attempt 번호를 붙여 확장한다.

    사용자 good/bad는 여기 없다. 그건 백엔드가 자기 DB에 갖고, 이쪽은 qna_uuid로만
    이어진다(운영 콘솔에서 두 API 결과를 겹쳐 본다).

    평가 경로의 입력 객체로만 쓴다 — 로그 1건을 이 모양으로 복원해 판정자에게 넘긴다.
    목록 조회는 리포지토리가 준 행을 스키마가 그대로 받는다(통과 전용 변환을 만들지 않는다).
    """
    qna_uuid: str                       # /ask가 발급해 응답에 실어 보낸 값. 로그의 PK
    query: Query                        # 질문 원문 + 정제된 질문
    documents: List[RetrievedChunk]     # 검색된 top_k 전체. 인용된 것만이 아니다
    answer: Optional[Answer]            # 답변. 인사·잡담이면 None
    evaluation: Optional[AnswerEvaluation] = None   # 아직 평가 전이면 None
    conversation_id: Optional[str] = None           # 백엔드가 준 값. 첫 턴엔 없다
