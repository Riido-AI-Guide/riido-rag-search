"""
scripts/doc_links.py — 답변 단위를 docs.riido.io 링크에 잇는다

llms-full.txt에는 링크가 없다. 대신 llms.txt가 같은 페이지들을 **같은 순서로**
링크와 함께 나열하므로 위치로 짝지어 페이지 URL을 얻는다.
제목으로 짝지으면 안 된다 — '자동화'와 'MCP 서버'가 각각 두 번 나온다.

섹션 앵커(#...)는 계산할 수 없어서 렌더된 페이지에서 실제 id를 읽어온다.
GitBook이 한글 제목의 슬러그를 만들지 못해 id가 'undefined', 'undefined-1' …로
떨어지는데, 이 번호는 heading 순번이 아니라 페이지 안의 모든 블록(div·details 포함)이
함께 쓰는 카운터라 순번 산술로는 맞출 수 없다
(예: /integrations/slack의 세 번째 H2는 '#undefined-2'가 아니라 '#undefined-6').
ASCII가 섞인 제목은 진짜 슬러그가 나오므로(#faq, #mcp) 규칙으로 재현할 수도 없다.

그래서 링크는 규칙이 아니라 **빌드 시점 스냅샷**이다. GitBook이 슬러그를 고치면
저장된 앵커는 페이지 맨 위로 떨어질 뿐이고(링크가 깨지지는 않는다), 다시 빌드하면
스스로 최신 앵커로 복구된다.
"""

import html
import re
import unicodedata
from typing import List, Optional, Tuple

import requests

LLMS_URL = "https://docs.riido.io/llms.txt"
FETCH_TIMEOUT = 30

RE_LLMS_ENTRY = re.compile(r"^- \[(.+?)\]\((https?://[^)]+?)\)", re.M)
RE_HEADING = re.compile(r'<(h[2-6])\s+id="([^"]+)"[^>]*>(.*?)</\1>', re.S)
RE_SVG = re.compile(r"<svg.*?</svg>", re.S)
RE_HTML_TAG = re.compile(r"<[^>]+>")
RE_PIECE_SUFFIX = re.compile(r"\s*\((\d+)\)$")  # pack_blocks가 붙인 '제목 (2)'


class DocLinkError(RuntimeError):
    """llms.txt와 원문의 순서가 어긋났을 때. 조용히 틀린 링크를 붙이느니 멈춘다."""


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def normalize_heading(text: str) -> str:
    """제목 비교용 정규화 — 마크다운 볼드와 공백 차이를 지운다.

    원문의 '2️⃣ **프로젝트 현황, AI로 한눈에**'와 렌더된
    '2️⃣ 프로젝트 현황, AI로 한눈에'를 같은 제목으로 보게 한다.
    """
    return unicodedata.normalize("NFC", re.sub(r"\s+", "", text.replace("*", "")))


def page_url(link: str) -> str:
    """llms.txt는 원문(.md)을 가리킨다 — 사람이 볼 페이지는 확장자를 뗀 주소다."""
    return link[:-3] if link.endswith(".md") else link


def _tag_text(inner_html: str) -> str:
    """heading 태그 안의 텍스트만. 앵커 아이콘(svg)은 걷어낸다."""
    return html.unescape(RE_HTML_TAG.sub("", RE_SVG.sub("", inner_html))).strip()


def _split_piece_suffix(heading: str) -> Tuple[str, bool]:
    """'제목 (2)' → ('제목', True). 꼬리표가 없으면 (원문, False)"""
    m = RE_PIECE_SUFFIX.search(heading)
    return (heading[: m.start()], True) if m else (heading, False)


# ---------------------------------------------------------------------------
# 페이지 1개
# ---------------------------------------------------------------------------

class PageLink:
    """문서 페이지 1개. 이 페이지에 속한 답변 단위의 링크를 순서대로 만들어 준다.

    앵커 표는 처음 필요할 때 한 번만 받아온다(도입부만 있는 페이지는 받지 않는다).
    제목을 문서 순서대로 앞에서부터 소비하므로 같은 제목이 여러 번 나와도 구분되고,
    본문이 짧아 유닛으로 쪼개지지 않은 H3를 건너뛰어도 뒤가 밀리지 않는다.
    """

    def __init__(self, title: str, url: str, session: Optional[requests.Session] = None):
        self.title = title
        self.url = url
        self._session = session or requests.Session()
        self._headings: Optional[List[Tuple[str, str]]] = None  # (정규화 제목, 앵커 id)
        self._cursor = 0
        self._last_key = ""
        self._last_link = ""

    def link_for(self, heading: Optional[str]) -> str:
        """heading에 해당하는 링크. 앵커를 못 찾으면 페이지 URL로 격하한다."""
        if not heading:
            return self.url  # H1 도입부 — 페이지 맨 위라 앵커가 필요 없다

        base, is_piece = _split_piece_suffix(heading)
        key = normalize_heading(base)
        if is_piece and key == self._last_key:
            return self._last_link  # 길어서 쪼갠 조각 — 원래 제목과 같은 앵커를 쓴다

        anchor = self._take(key)
        self._last_key = key
        self._last_link = f"{self.url}#{anchor}" if anchor else self.url
        return self._last_link

    def _take(self, key: str) -> Optional[str]:
        headings = self._load()
        for i in range(self._cursor, len(headings)):
            if headings[i][0] == key:
                self._cursor = i + 1
                return headings[i][1]
        return None

    def _load(self) -> List[Tuple[str, str]]:
        if self._headings is None:
            self._headings = self._fetch_headings()
        return self._headings

    def _fetch_headings(self) -> List[Tuple[str, str]]:
        """렌더된 페이지의 (정규화 제목, 앵커 id)를 문서 순서대로.

        실패해도 빌드를 세우지 않는다 — 그 페이지만 앵커 없이 페이지 링크로 간다.
        """
        try:
            res = self._session.get(self.url, timeout=FETCH_TIMEOUT)
            res.raise_for_status()
        except requests.RequestException as e:
            print(f"  ⚠️  앵커를 읽지 못했습니다 ({self.url}): {e} — 페이지 링크만 붙입니다")
            return []

        return [
            (normalize_heading(_tag_text(inner)), anchor)
            for _, anchor, inner in RE_HEADING.findall(res.text)
        ]


# ---------------------------------------------------------------------------
# 페이지 목록
# ---------------------------------------------------------------------------

class DocLinks:
    """llms.txt가 나열한 페이지 목록. 원문 H1의 등장 순번으로 페이지를 찾는다."""

    def __init__(self, pages: List[PageLink]):
        self.pages = pages

    def __len__(self) -> int:
        return len(self.pages)

    @classmethod
    def load(cls, url: str = LLMS_URL, timeout: int = FETCH_TIMEOUT) -> "DocLinks":
        res = requests.get(url, timeout=timeout)
        res.raise_for_status()

        session = requests.Session()  # 페이지 수십 개를 이어서 받으므로 커넥션을 재사용한다
        pages = [
            PageLink(title.strip(), page_url(link), session)
            for title, link in RE_LLMS_ENTRY.findall(res.text)
        ]
        if not pages:
            raise DocLinkError(f"{url}에서 페이지 링크를 찾지 못했습니다")
        return cls(pages)

    def page(self, index: int, title: str) -> PageLink:
        """H1 순번으로 페이지를 집는다. 제목이 어긋나면 정렬이 깨진 것이므로 멈춘다.

        위치로 짝짓기 때문에 한 칸만 밀려도 이후 모든 링크가 조용히 틀린다.
        """
        if index >= len(self.pages):
            raise DocLinkError(
                f"llms.txt에 {index + 1}번째 페이지가 없습니다 (원문 H1: {title!r}). "
                f"llms.txt {len(self.pages)}개 vs 원문 H1이 더 많습니다"
            )

        page = self.pages[index]
        if normalize_heading(page.title) != normalize_heading(title):
            raise DocLinkError(
                f"{index + 1}번째 페이지가 어긋났습니다 — "
                f"llms.txt {page.title!r} vs 원문 H1 {title!r}. "
                "위치로 짝짓기 때문에 이대로 두면 이후 링크가 모두 밀립니다"
            )
        return page
