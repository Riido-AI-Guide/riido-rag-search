"""
api/schemas/chat.py — 질의응답(/ask) 스키마
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from domain import ConversationTurn
from api.schemas.units import AnswerUnitOut


# 요청 하나에 실어 보낼 수 있는 이전 턴의 상한.
# 비정상 페이로드를 막는 안전장치 (실제로 몇 턴을 쓸지는 Settings.history_turns가 정함)
MAX_HISTORY_TURNS = 50


class ConversationTurnIn(BaseModel):
    question: str = Field(
        min_length=1, max_length=1000,
        description="그 턴의 사용자 질문 원문",
        examples=["작업 어떻게 만들어?"],
    )
    answer: str = Field(
        default="", max_length=4000,
        description="그 턴의 답변 또는 요약. 답변에 실패한 턴이면 빈 문자열로 둔다",
        examples=["작업은 보드 화면 상단의 새 작업 버튼으로 만들 수 있습니다."],
    )

    def to_domain(self) -> ConversationTurn:
        return ConversationTurn(question=self.question, answer=self.answer)


class AskRequest(BaseModel):
    """
    질문 + 이전 대화. top_k·vector_weight 등 검색 파라미터는 서버 기본값(Settings)을 쓴다.

    첫 대화면 history와 conversation_id를 둘 다 생략하면 된다(빈 값이 곧 첫 턴이다).
    "첫 턴인지" 여부를 별도 플래그로 받지 않는다 — 같은 사실을 두 군데서 표현하면 반드시 어긋난다.
    """
    query: str = Field(min_length=1, max_length=1000, examples=["팀원을 어떻게 추가해?"])

    history: List[ConversationTurnIn] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_TURNS,
        description=(
            "이전 대화를 오래된 순으로. 첫 대화면 생략하거나 빈 배열. "
            "서버는 최근 몇 턴만 사용하므로 전부 보낼 필요는 없다."
        ),
    )

    conversation_id: Optional[str] = Field(
        default=None, max_length=100,
        description=(
            "백엔드가 발급한 대화 식별자. 첫 대화면 생략한다. "
            "답변 생성에는 쓰지 않고 품질 로그에만 기록한다(나중에 운영 콘솔에서 "
            "어떤 대화에 대한 평가인지 되짚을 조인 키). 없어도 답변은 정상 동작한다."
        ),
        examples=["conv_01H8XK..."],
    )


class AskResponse(BaseModel):
    raw_query: str = Field(description="사용자가 보낸 원문")
    cleaned_query: str = Field(
        description=(
            "query_transform이 정제한 검색어. 이전 대화가 있으면 대명사·생략을 "
            "풀어낸 결과다. 재작성 실패와 검색 실패를 구분하는 단서이므로 로그에 남긴다"
        )
    )
    needs_search: bool = Field(description="false면 검색·생성을 건너뛴다")

    conversation_id: Optional[str] = Field(
        default=None, description="요청에 담겨 온 값을 그대로 돌려준다(로그 대조용)"
    )
    history_turns_used: int = Field(
        default=0, description="재작성에 실제로 사용한 이전 턴 수. 0이면 첫 턴처럼 처리했다는 뜻"
    )

    title: str = Field(
        default="",
        description=(
            "대화 제목. **첫 턴에서만** 채워서 보낸다(history와 conversation_id가 둘 다 "
            "없는 요청). 후속 턴이면 빈 문자열이며, 이는 \"제목을 바꾸지 말라\"는 뜻이다 — "
            "빈 문자열로 대화 제목을 덮어쓰지 말 것. 제목 생성에 실패해도 빈 값이 아니라 "
            "질문 원문을 줄인 값이나 \"새 대화\"가 온다."
        ),
        examples=["팀원 추가 방법"],
    )

    answer: str
    doc_ids: List[str] = Field(description="답변의 근거가 된 answer_units 식별자")

    documents: List[AnswerUnitOut] = Field(
        default_factory=list, description="근거 문서 본문. 항상 포함한다"
    )
