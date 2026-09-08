"""
scripts/build_answer_units.py — 답변용 테이블(answer_units) 빌드

이용가이드(llms-full.txt)를 LLM 재작성 없이 원문 그대로 답변 단위로 적재한다.
- 답변 단위 = H2 섹션 전체 (문자 수로 자르지 않음)
- 표/코드블록을 관통해서 자르지 않음
- source_hash 기반 증분 빌드 → 변경된 doc_id만 반환 (검색 벡터 재생성용)
- 각 단위에 docs.riido.io 링크(가능하면 섹션 앵커까지)를 붙인다 → scripts/doc_links.py
"""

import re
import hashlib
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import requests
import psycopg2.extras

from core.db import connect
from domain import RawChunk
from scripts.doc_links import DocLinkError, DocLinks



GUIDE_URL = "https://docs.riido.io/llms-full.txt"
SOURCE_TYPE_GUIDE = "guide"

# H2 섹션이 이보다 길면 H3 → 문단 순으로 분할한다.
MAX_UNIT_CHARS = 2400


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

@dataclass
class BuildReport:
    created: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    relinked: List[str] = field(default_factory=list)  # 본문은 그대로고 링크만 바뀐 것

    @property
    def dirty(self) -> List[str]:
        """검색 벡터를 다시 만들어야 하는 doc_id.

        relinked는 여기 들어가지 않는다 — 임베딩은 본문에서 나오므로
        링크가 바뀌었다고 다시 임베딩할 이유가 없다.
        """
        return self.created + self.updated

    def summary(self) -> str:
        return (
            f"신규 {len(self.created)} / 변경 {len(self.updated)} / "
            f"유지 {len(self.unchanged)} / 삭제 {len(self.deleted)} / "
            f"링크만 갱신 {len(self.relinked)}"
        )


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_doc_id(source_type: str, section: str, dedup_idx: int = 0) -> str:
    """'팀 > 팀 관리' → 'guide/팀/팀-관리' (한글 유지, 공백·구분자만 정규화)"""
    normalized = unicodedata.normalize("NFC", section)
    parts = [p.strip() for p in normalized.split(">") if p.strip()]
    slugs = []
    for part in parts:
        part = re.sub(r"[\s/]+", "-", part)
        part = re.sub(r"[^\w가-힣\-.]", "", part)
        slugs.append(part.strip("-") or "section")
    doc_id = f"{source_type}/" + "/".join(slugs)
    return f"{doc_id}~{dedup_idx}" if dedup_idx else doc_id


# ---------------------------------------------------------------------------
# 1) 원문 수집
# ---------------------------------------------------------------------------

def fetch_guide(url: str = GUIDE_URL, timeout: int = 30) -> str:
    res = requests.get(url, timeout=timeout)
    res.raise_for_status()
    return res.text


# ---------------------------------------------------------------------------
# 2) 정제 — 노이즈 제거, 구조는 보존
# ---------------------------------------------------------------------------

RE_CARD_TABLE = re.compile(r"<table data-view=\"cards\">.*?</table>", re.DOTALL)
RE_FIGURE = re.compile(r"<figure>.*?</figure>", re.DOTALL)
RE_DETAILS = re.compile(r"<details>\s*<summary>(.*?)</summary>(.*?)</details>", re.DOTALL)
RE_EMBED = re.compile(r"\{%\s*embed url=\"?<?(.*?)>?\"?\s*%\}")
RE_GITBOOK = re.compile(r"\{%.*?%\}", re.DOTALL)
RE_HTML_TAG = re.compile(r"</?(?:strong|em|p|br)\s*/?>")
RE_BLANKS = re.compile(r"\n{3,}")


def convert_details_to_qa(text: str) -> str:
    """<details><summary>질문</summary>답변</details> → 'Q. 질문 / 답변'
    FAQ 블록이 검색·답변 양쪽에서 온전히 살아남게 한다."""
    def _repl(m: re.Match) -> str:
        question = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        answer = m.group(2).strip()
        return f"\n**Q. {question}**\n\n{answer}\n"

    return RE_DETAILS.sub(_repl, text)


def clean_markup(text: str) -> str:
    text = RE_CARD_TABLE.sub("", text)      # 링크 카드 표 = 순수 노이즈
    text = RE_FIGURE.sub("", text)          # 이미지 경로 제거
    text = convert_details_to_qa(text)      # FAQ 구조 보존
    text = RE_EMBED.sub(r"\1", text)        # embed 매크로는 URL만 남김
    text = RE_GITBOOK.sub("", text)         # hint/tabs/stepper/columns 태그 제거
    text = RE_HTML_TAG.sub("", text)        # 인라인 HTML 정리 (표 태그는 유지)
    text = RE_BLANKS.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 3) 분할 — H1 → H2 → (필요시) H3 → 문단
# ---------------------------------------------------------------------------

def split_h1_sections(raw_text: str) -> List[Tuple[int, str, str]]:
    """H1 단위로 (등장 순번, 제목, 본문) 목록 반환.

    순번은 원문에 나온 H1의 순서 그대로이며, 아래에서 버리는 H1도 번호를 차지한다.
    llms.txt가 같은 페이지를 같은 순서로 나열하므로 이 번호가 곧 링크의 주소다
    (scripts/doc_links.py). 버린 H1의 번호를 건너뛰면 이후 링크가 전부 한 칸씩 밀린다.

    제목이 아니라 '제목 + 본문 해시'로 중복을 판정한다.
    llms-full.txt에는 MCP 서버가 두 번 들어있고(스크린샷 한 장만 다르고 정제 후에는
    완전히 같아서 먼저 나온 /ai/mcp만 남는다), '자동화' H1은 스프린트용·미팅용
    두 개가 서로 다른 내용이다(둘 다 보존).
    """
    # 맨 앞 H1은 앞에 개행이 없어 그냥 split하면 parts[0]에 묻혀 통째로 빠진다.
    # split_h2_sections와 같은 방식으로 개행을 하나 붙여 보정한다.
    parts = re.split(r"\n# (.+?)\n", "\n" + raw_text)
    sections: List[Tuple[int, str, str]] = []
    seen_bodies: Set[str] = set()

    for order, i in enumerate(range(1, len(parts), 2)):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        body = clean_markup(body)
        if not body:
            continue

        body_hash = sha256(f"{title}\n{body}")
        if body_hash in seen_bodies:
            continue
        seen_bodies.add(body_hash)
        sections.append((order, title, body))

    return sections


def split_h2_sections(body: str) -> List[Tuple[Optional[str], str]]:
    """H1 본문을 (H2 제목, 본문) 목록으로. 첫 도입부는 제목 None."""
    parts = re.split(r"\n#{2} (.+?)\n", "\n" + body)
    result: List[Tuple[Optional[str], str]] = []

    intro = parts[0].strip()
    if intro:
        result.append((None, intro))

    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        text = (parts[i + 1] if i + 1 < len(parts) else "").strip()
        if text:
            result.append((heading, text))

    return result


def split_h3_sections(text: str) -> List[Tuple[Optional[str], str]]:
    """H2가 너무 길 때만 사용. H3(###, ####) 경계로 나눈다."""
    parts = re.split(r"\n#{3,4} (.+?)\n", "\n" + text)
    result: List[Tuple[Optional[str], str]] = []

    intro = parts[0].strip()
    if intro:
        result.append((None, intro))

    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        chunk = (parts[i + 1] if i + 1 < len(parts) else "").strip()
        if chunk:
            result.append((heading, chunk))

    return result


def split_into_blocks(text: str) -> List[str]:
    """빈 줄 기준 블록 분리"""
    blocks: List[str] = []
    buffer: List[str] = []
    in_fence = False

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            buffer.append(line)
            continue

        if not line.strip() and not in_fence:
            if buffer:
                blocks.append("\n".join(buffer).strip())
                buffer = []
            continue

        buffer.append(line)

    if buffer:
        blocks.append("\n".join(buffer).strip())

    return [b for b in blocks if b]


def pack_blocks(text: str, max_chars: int) -> List[str]:
    """블록 단위로 묶어 max_chars 이하 조각으로"""
    blocks = split_into_blocks(text)
    packed: List[str] = []
    current: List[str] = []
    length = 0

    for block in blocks:
        block_len = len(block)
        if current and length + block_len > max_chars:
            packed.append("\n\n".join(current))
            current, length = [], 0
        current.append(block)
        length += block_len + 2

    if current:
        packed.append("\n\n".join(current))

    return packed or [text]


def split_oversized(text: str, max_chars: int = MAX_UNIT_CHARS) -> List[Tuple[Optional[str], str]]:
    """H2 섹션이 한도를 넘을 때만 H3 → 문단 순으로 분할."""
    if len(text) <= max_chars:
        return [(None, text)]

    result: List[Tuple[Optional[str], str]] = []
    for heading, chunk in split_h3_sections(text):
        if len(chunk) <= max_chars:
            result.append((heading, chunk))
            continue
        for idx, piece in enumerate(pack_blocks(chunk, max_chars)):
            suffix = f" ({idx + 1})" if idx else ""
            result.append((f"{heading}{suffix}" if heading else None, piece))

    return result


# ---------------------------------------------------------------------------
# 4) 답변 단위 조립
# ---------------------------------------------------------------------------

def build_answer_units(raw_text: str, links: Optional[DocLinks] = None) -> List[RawChunk]:
    """links를 주면 각 단위에 docs.riido.io 링크를 붙인다. 없으면 source_url이 빈 문자열."""
    units: List[RawChunk] = []
    used_ids: Dict[str, int] = {}
    ord_idx = 0

    for order, title, body in split_h1_sections(raw_text):
        page = links.page(order, title) if links else None

        for h2, h2_text in split_h2_sections(body):
            for h3, chunk in split_oversized(h2_text):
                path_parts = [p for p in (title, h2, h3) if p]
                section = " > ".join(path_parts)

                base_id = make_doc_id(SOURCE_TYPE_GUIDE, section)
                dedup_idx = used_ids.get(base_id, 0)
                used_ids[base_id] = dedup_idx + 1
                doc_id = make_doc_id(SOURCE_TYPE_GUIDE, section, dedup_idx)

                # 답변 단위 안에 경로를 남겨 LLM이 맥락을 잃지 않게 한다.
                content = f"[{section}]\n\n{chunk.strip()}"

                # 가장 안쪽 제목의 앵커를 받는다. 제목이 없는 도입부는 페이지 링크가 된다.
                # (page.link_for는 제목을 문서 순서대로 소비하므로 호출 순서가 곧 문서 순서여야 한다)
                source_url = page.link_for(h3 or h2) if page else ""

                units.append(RawChunk(
                    title=title,
                    section=section,
                    content=content,
                    source_type=SOURCE_TYPE_GUIDE,
                    doc_id=doc_id,
                    ord_idx=ord_idx,
                    source_hash=sha256(content),
                    source_url=source_url,
                ))
                ord_idx += 1

    return units


# ---------------------------------------------------------------------------
# 5) 적재 — 스키마 · 증분 upsert · 정리
# ---------------------------------------------------------------------------

def setup_answer_table() -> None:
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS answer_units (
            doc_id      TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            section     TEXT NOT NULL,
            source_type TEXT NOT NULL,
            content     TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            ord_idx     INTEGER NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    # 개발자마다 DB가 따로라 별도 마이그레이션 절차가 없다. 빌드가 스키마를 맞춘다.
    # (search_units 빌드도 이 함수를 부르므로 어느 쪽을 돌려도 컬럼이 생긴다)
    cur.execute("ALTER TABLE answer_units ADD COLUMN IF NOT EXISTS source_url TEXT;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_units_type ON answer_units (source_type);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_units_ord ON answer_units (ord_idx);")
    conn.commit()
    cur.close()
    conn.close()


def fetch_existing_units(source_type: str) -> Dict[str, Tuple[str, str]]:
    """{doc_id: (본문 해시, 링크)}. 링크가 아직 없는 행은 빈 문자열."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT doc_id, source_hash, COALESCE(source_url, '') "
        "FROM answer_units WHERE source_type = %s;",
        (source_type,),
    )
    rows = {doc_id: (source_hash, url) for doc_id, source_hash, url in cur.fetchall()}
    cur.close()
    conn.close()
    return rows


def upsert_answer_units(units: List[RawChunk], existing: Dict[str, Tuple[str, str]]) -> BuildReport:
    """본문이 바뀐 것만 upsert하고, 링크만 달라진 것은 링크만 갱신한다.

    링크 변경은 dirty로 올리지 않는다 — 임베딩은 본문에서 나오므로 이미 임베딩된
    문서를 링크 때문에 다시 임베딩할 이유가 없다.
    """
    report = BuildReport()
    changed: List[RawChunk] = []
    relinked: List[RawChunk] = []

    for unit in units:
        prev = existing.get(unit.doc_id)
        if prev is None:
            report.created.append(unit.doc_id)
            changed.append(unit)
        elif prev[0] != unit.source_hash:
            report.updated.append(unit.doc_id)
            changed.append(unit)
        elif unit.source_url and unit.source_url != prev[1]:
            report.relinked.append(unit.doc_id)
            relinked.append(unit)
        else:
            report.unchanged.append(unit.doc_id)

    if not changed and not relinked:
        return report

    conn = connect()
    cur = conn.cursor()

    if changed:
        # 링크가 비어 있으면(매핑 실패) 이미 저장된 링크를 지우지 않는다 —
        # 본문 적재가 링크를 날리는 부작용을 갖지 않게 한다.
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO answer_units
                (doc_id, title, section, source_type, content, source_hash,
                 ord_idx, source_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (doc_id) DO UPDATE SET
                title       = EXCLUDED.title,
                section     = EXCLUDED.section,
                content     = EXCLUDED.content,
                source_hash = EXCLUDED.source_hash,
                ord_idx     = EXCLUDED.ord_idx,
                source_url  = COALESCE(NULLIF(EXCLUDED.source_url, ''), answer_units.source_url),
                updated_at  = now();
        """, [
            (u.doc_id, u.title, u.section, u.source_type,
             u.content, u.source_hash, u.ord_idx, u.source_url)
            for u in changed
        ])

    if relinked:
        psycopg2.extras.execute_batch(cur, """
            UPDATE answer_units SET source_url = %s, updated_at = now()
            WHERE doc_id = %s;
        """, [(u.source_url, u.doc_id) for u in relinked])

    conn.commit()
    cur.close()
    conn.close()

    return report


def prune_removed(source_type: str, keep_ids: Set[str]) -> List[str]:
    """원문에서 사라진 섹션 삭제. retrieval_vectors는 FK CASCADE로 함께 정리된다."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT doc_id FROM answer_units WHERE source_type = %s;",
        (source_type,),
    )
    stale = [row[0] for row in cur.fetchall() if row[0] not in keep_ids]

    if stale:
        cur.execute("DELETE FROM answer_units WHERE doc_id = ANY(%s);", (stale,))
        conn.commit()

    cur.close()
    conn.close()
    return stale


# ---------------------------------------------------------------------------
# 6) 오케스트레이션
# ---------------------------------------------------------------------------

def load_doc_links() -> Optional[DocLinks]:
    """페이지 링크 표. 못 받아오면 None — 링크는 본문의 부속이지 전제가 아니다."""
    try:
        links = DocLinks.load()
        print(f"🔗 페이지 링크 {len(links)}개 (llms.txt)")
        return links
    except (requests.RequestException, DocLinkError) as e:
        print(f"⚠️  링크 매핑 생략 — {e}")
        return None


def build_guide_answer_units(url: str = GUIDE_URL) -> BuildReport:
    setup_answer_table()

    raw_text = fetch_guide(url)
    links = load_doc_links()

    try:
        units = build_answer_units(raw_text, links)
    except DocLinkError as e:
        # 원문과 llms.txt의 순서가 어긋난 경우. 링크만 포기하고 본문은 정상 적재한다
        # (이미 저장된 링크는 upsert가 덮어쓰지 않으므로 그대로 남는다).
        print(f"⚠️  링크 매핑 생략 — {e}")
        units = build_answer_units(raw_text)

    linked = sum(1 for u in units if u.source_url)
    anchored = sum(1 for u in units if "#" in u.source_url)
    print(f"✅ 이용가이드 {len(units)}개 답변 단위 생성 "
          f"(링크 {linked}개, 그중 앵커 {anchored}개)")

    existing = fetch_existing_units(SOURCE_TYPE_GUIDE)
    report = upsert_answer_units(units, existing)
    report.deleted = prune_removed(SOURCE_TYPE_GUIDE, {u.doc_id for u in units})

    print(f"📦 {report.summary()}")
    return report


def print_stats() -> None:
    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT source_type,
               COUNT(*)                  AS units,
               AVG(LENGTH(content))::int AS avg_len,
               MAX(LENGTH(content))      AS max_len,
               COUNT(*) FILTER (WHERE COALESCE(source_url, '') <> '') AS linked,
               COUNT(*) FILTER (WHERE source_url LIKE '%#%')          AS anchored
        FROM answer_units GROUP BY source_type ORDER BY source_type;
    """)
    for row in cur.fetchall():
        print(f"  [{row['source_type']}] {row['units']}개 "
              f"(평균 {row['avg_len']}자, 최대 {row['max_len']}자)")
        print(f"      링크 {row['linked']}개 / 그중 섹션 앵커 {row['anchored']}개")
    cur.close()
    conn.close()


if __name__ == "__main__":
    report = build_guide_answer_units()
    print_stats()

    if report.dirty:
        # 본문이 바뀐 문서의 검색 인덱스는 여기서 갱신되지 않는다. 낡았다는 사실은
        # answer_content_vectors.source_hash에 남으므로, 아래 명령이 그 문서만 다시 만든다.
        print(f"\n🔁 검색 인덱스 재생성 필요: {len(report.dirty)}건")
        for doc_id in report.dirty[:10]:
            print(f"  - {doc_id}")
        print("   → python -m scripts.build_search_units")

    if report.relinked:
        print(f"\n🔗 링크만 갱신: {len(report.relinked)}건 (재임베딩 불필요)")