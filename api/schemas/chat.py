"""
api/schemas/chat.py — 질의응답(/ask) 스키마
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from domain import AnswerSection, ConversationTurn, SourceRef
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


class SourceRefOut(BaseModel):
    """섹션 하나가 근거로 삼은 문서 1건"""
    doc_id: str = Field(description="답변 단위 식별자", examples=["guide/휴지통/복구-및-영구-삭제"])
    section: str = Field(description="문서 경로. 화면에 그대로 표시한다", examples=["휴지통 > 복구 및 영구 삭제"])
    url: str = Field(
        default="",
        description=(
            "근거 문서의 원문 주소. 이 값을 근거 버튼의 링크로 걸면 된다.\n\n"
            "가능하면 해당 섹션 앵커까지 붙어 있다. 앵커는 GitBook이 실제로 발행한 id를 "
            "빌드 때 읽어온 것이라 사람이 읽을 수 있는 형태가 아닐 수 있다"
            "(한글 제목은 `#undefined-2`처럼 나온다) — 화면에 그대로 노출하지 말고 "
            "표시는 section으로, 이동만 이 값으로 한다.\n\n"
            "링크를 못 붙인 문서는 빈 문자열이므로 버튼을 걸기 전에 확인할 것."
        ),
        examples=["https://docs.riido.io/data/trash#undefined-2"],
    )

    @classmethod
    def from_domain(cls, ref: SourceRef) -> "SourceRefOut":
        return cls(doc_id=ref.doc_id, section=ref.section, url=ref.url)


class AnswerSectionOut(BaseModel):
    """답변을 이루는 한 덩어리. 프론트는 이 단위로 렌더하고 sources를 근거 버튼으로 단다"""
    label: str = Field(description="섹션 이름(핵심답변/단계별방법 …). 프론트가 이 값으로 스타일을 정한다")
    text: str
    sources: List[SourceRefOut] = Field(
        default_factory=list,
        description="이 섹션의 근거 문서. 문서 안에서 중복은 제거돼 있고, 근거가 없으면 빈 배열",
    )

    @classmethod
    def from_domain(cls, sec: AnswerSection) -> "AnswerSectionOut":
        return cls(
            label=sec.label,
            text=sec.text,
            sources=[SourceRefOut.from_domain(r) for r in sec.sources],
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
    title: str = Field(
        description=(
            "이 턴의 제목. **매 턴 채워진다.** 답변 생성이 만든 제목을 그대로 쓰므로 "
            "제목 때문에 LLM을 더 부르지 않는다.\n\n"
            "쓰임이 둘이다 — 첫 턴이면 이 값을 **대화 제목으로 저장**하고, 그 뒤로는 "
            "답변 말풍선 제목으로만 쓴다. **후속 턴의 title로 대화 제목을 덮어쓰지 말 것.** "
            "매 턴 그 답변에 맞춰 달라지는 값이라 덮어쓰면 대화 제목이 계속 바뀐다. "
            "첫 턴인지는 백엔드가 conversation_id를 보냈는지로 이미 알고 있다.\n\n"
            "**첫 턴에서는 비지 않는다.** 답변에 제목이 없으면(no_answer, parse_error) "
            "질문 원문을 줄인 값이, 인사·잡담(no_search)이면 \"새 대화\"가 온다. "
            "빈 문자열은 후속 턴의 인사에서만 오며, 그때는 제목 없이 본문만 그리면 된다."
        ),
        examples=["휴지통 복구 방법"],
    )

    answer_type: str = Field(
        description=(
            "답변 유형. 프론트가 레이아웃을 고르는 데 쓴다.\n\n"
            "- 정상 답변: concept / step / judgement / troubleshoot / explore "
            "— 유형마다 answers의 label 구성이 다르다\n"
            "- no_answer: 검색은 했지만 근거 문서에 답이 없었다\n"
            "- parse_error: LLM이 형식을 깨뜨려 라벨 없는 섹션 하나에 원문만 담겨 있다\n"
            "- no_search: 인사·잡담이라 검색·생성을 건너뛰었다(needs_search=false)"
        ),
        examples=["step"],
    )

    answers: List[AnswerSectionOut] = Field(
        description=(
            "답변 본문. 섹션 단위로 렌더하고 각 섹션의 sources를 근거 버튼으로 단다. "
            "어느 경로에서도 비지 않는다 — 인사·잡담이나 답변 형식이 깨진 경우에도 "
            "label과 sources가 빈 섹션 하나에 텍스트가 담겨 온다.\n\n"
            "평문 answer 필드는 두지 않는다. 이전 턴을 history로 되돌려 보낼 때 쓸 문자열은 "
            "answers[].text를 이어붙여 만들면 된다(label은 넣지 말 것 — 재작성 프롬프트에 "
            "들어가는 건 앞부분 일부라 라벨이 자리를 잡아먹는다)"
        ),
    )
    doc_ids: List[str] = Field(
        description=(
            "답변이 실제로 인용한 answer_units 식별자. answers[].sources를 처음 등장한 "
            "순서대로 합쳐 중복을 없앤 값이다. 검색됐지만 인용되지 않은 문서는 들어가지 않는다"
        )
    )

    documents: List[AnswerUnitOut] = Field(
        default_factory=list,
        description=(
            "검색으로 가져온 문서 본문(top_k 전체). 인용되지 않은 문서도 들어 있으므로 "
            "doc_ids의 상위집합이다. 화면에 근거로 표시할 것은 answers[].sources 쪽이다"
        ),
    )
