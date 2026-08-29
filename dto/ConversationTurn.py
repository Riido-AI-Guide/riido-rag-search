from dataclasses import dataclass


@dataclass
class ConversationTurn:
    """
    이미 끝난 대화 한 턴.

    대화의 소유자는 백엔드다. 이 서비스는 요청 바디로 받은 것만 보고,
    conversation_id로 DB를 조회하지 않는다(요청 경로에서 남의 DB를 읽으면
    그쪽 장애·마이그레이션이 답변 실패로 이어진다).

    answer는 요약본이 들어올 수 있다. 질문 재작성에 필요한 건 답변 전문이
    아니라 "무엇에 대한 얘기였는지"뿐이라, 나중에 백엔드가 요약을 보내도
    이 자리를 그대로 쓴다.
    """
    question: str          # 그 턴의 사용자 원문
    answer: str = ""       # 그 턴의 답변(또는 요약). 답변 실패한 턴은 빈 문자열
