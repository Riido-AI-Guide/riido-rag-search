"""
scripts/build_golden_set.py — 검색 평가용 골든셋(질문-정답 문서 쌍) 만들기

재료:
- 질문: qa_reviewed_20260804.json (실제 고객 상담 100건, 마스킹·수기검수 완료)
  → search_units 인덱스와 겹치지 않는 진짜 사용자 질문이라 유출 없이 시험 문제로 쓸 수 있다
- 정답 후보 문서 목록: answer_units 테이블 (doc_id, title, section)

흐름 (2단계):
1) propose  — LLM이 상담 질문마다 정답 문서 후보를 제안 → 검수용 CSV 출력
              python -m scripts.build_golden_set propose
2) (사람)   — golden_labels_review.csv를 열어 '검수' 열만 채운다
              비워둠 = 제안 그대로 승인 / doc_id 입력 = 정답 교체 / "제외" = 골든셋에서 뺌
3) finalize — 검수 반영해서 golden_set.json 확정 (source="real")
              python -m scripts.build_golden_set finalize
4) synthesize — 상담 질문이 커버하지 못한 문서들에 대해 원문에서
              고객 말투 질문을 생성해 골든셋에 보충 (source="synthetic")
              python -m scripts.build_golden_set synthesize

실사용(real)과 합성(synthetic)을 태그로 구분해두므로 평가 때 두 셋의
점수를 따로 볼 수 있다. 대표 숫자는 real, 문서 커버리지 확인은 synthetic.

LLM 제안 결과는 golden_labels_draft.json에 캐시되므로 propose를 다시 돌려도
이미 제안된 질문은 재호출하지 않는다(비용 절약).
"""

import os
import re
import csv
import sys
import json
from typing import Dict, List

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "dbname=riido user=postgres password=postgres host=localhost port=5432",
)

QA_PATH = "./qa_reviewed_20260804.json"
DRAFT_PATH = "./golden_labels_draft.json"     # LLM 제안 캐시
REVIEW_PATH = "./golden_labels_review.csv"    # 사람이 검수하는 파일
GOLDEN_PATH = "./golden_set.json"             # 최종 골든셋

LABEL_MODEL = os.getenv("GOLDEN_LABEL_MODEL", "gpt-4o")
ANSWER_SNIPPET_CHARS = 1500  # 상담 답변은 앞부분만 잘라서 프롬프트에 넣는다


def get_connection():
    return psycopg2.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# 재료 로드
# ---------------------------------------------------------------------------

def load_qa_items() -> List[Dict]:
    with open(QA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["items"]


def fetch_doc_catalog() -> List[Dict]:
    """answer_units에서 정답 후보 문서 목록(doc_id, title, section)을 가져온다."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT doc_id, title, section FROM answer_units ORDER BY doc_id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        raise RuntimeError("answer_units가 비어 있습니다. python -m scripts.build_answer_units를 먼저 실행하세요.")
    return rows


def catalog_text(catalog: List[Dict]) -> str:
    return "\n".join(f"- {r['doc_id']} | {r['title']} | {r['section']}" for r in catalog)


# ---------------------------------------------------------------------------
# 1) propose — LLM이 정답 문서 후보 제안
# ---------------------------------------------------------------------------

LABEL_SYSTEM_PROMPT = """
당신은 IT 프로젝트 관리 툴의 고객지원 QA 데이터를 검수하는 전문가입니다.

[문서 목록]에 있는 가이드 문서 중, [상담 질문]에 대한 답을 담고 있는 문서를 고르세요.
[실제 상담 답변]은 상담원이 실제로 안내한 내용이므로, 어떤 문서가 정답인지 판단하는 힌트로 쓰세요.

반드시 JSON 형식으로만 응답하세요.

[판단 기준]
1. `answerable`: 질문이 가이드 문서로 답할 수 있는 일반적인 사용법/기능/정책 질문이면 true.
   특정 계정에 대한 개별 처리 요청(환불, 데이터 복구, 계정 문제 조사 등)이나
   문서 목록에 관련 문서가 전혀 없는 질문이면 false.
2. `golden_doc_id`: answerable이 true일 때, 답을 담고 있는 가장 적합한 문서의 doc_id.
   반드시 [문서 목록]에 있는 doc_id를 그대로 복사할 것. false면 null.
3. `alt_doc_id`: 정답이 될 수 있는 두 번째 문서가 있으면 그 doc_id, 없으면 null.
4. `confidence`: 판단 확신도. "high" / "medium" / "low".
5. `reason`: 판단 근거를 한 문장으로.

[JSON 응답 형식]
{"answerable": true, "golden_doc_id": "guide/...", "alt_doc_id": null, "confidence": "high", "reason": "..."}
"""


def propose_label(client: OpenAI, catalog_str: str, item: Dict) -> Dict:
    answer_snippet = (item.get("answer") or "")[:ANSWER_SNIPPET_CHARS]
    user_prompt = f"""[문서 목록]
{catalog_str}

[상담 질문]
{item["question"]}

[실제 상담 답변 (일부)]
{answer_snippet}
"""
    try:
        response = client.chat.completions.create(
            model=LABEL_MODEL,
            messages=[
                {"role": "system", "content": LABEL_SYSTEM_PROMPT.strip()},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        res = json.loads(response.choices[0].message.content)
        return {
            "answerable": bool(res.get("answerable", False)),
            "golden_doc_id": res.get("golden_doc_id"),
            "alt_doc_id": res.get("alt_doc_id"),
            "confidence": res.get("confidence", "low"),
            "reason": res.get("reason", ""),
        }
    except Exception as e:
        return {
            "answerable": False,
            "golden_doc_id": None,
            "alt_doc_id": None,
            "confidence": "low",
            "reason": f"LLM 호출 오류: {e}",
        }


def load_draft() -> Dict[str, Dict]:
    if os.path.exists(DRAFT_PATH):
        with open(DRAFT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_draft(draft: Dict[str, Dict]) -> None:
    with open(DRAFT_PATH, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)


def question_preview(text: str, limit: int = 150) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def run_propose() -> None:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    items = load_qa_items()
    catalog = fetch_doc_catalog()
    catalog_str = catalog_text(catalog)
    valid_doc_ids = {r["doc_id"] for r in catalog}
    print(f"✅ 상담 질문 {len(items)}개 / 후보 문서 {len(catalog)}개 로드 (모델: {LABEL_MODEL})")

    draft = load_draft()
    todo = [it for it in items if it["sample_id"] not in draft]
    if todo:
        print(f"🤖 LLM 제안 생성: {len(todo)}개 (캐시됨 {len(draft)}개)")
    else:
        print(f"🤖 전부 캐시에 있음 — LLM 호출 없이 CSV만 다시 만든다")

    for i, item in enumerate(todo, 1):
        proposal = propose_label(client, catalog_str, item)
        # LLM이 목록에 없는 doc_id를 지어냈으면 표시해 둔다
        if proposal["golden_doc_id"] and proposal["golden_doc_id"] not in valid_doc_ids:
            proposal["confidence"] = "low"
            proposal["reason"] = f"⚠️ 목록에 없는 doc_id 제안됨({proposal['golden_doc_id']}) / " + proposal["reason"]
            proposal["golden_doc_id"] = None
        draft[item["sample_id"]] = proposal
        save_draft(draft)  # 중간에 끊겨도 이어서 돌릴 수 있게 매번 저장
        print(f"  [{i}/{len(todo)}] {item['sample_id']}: "
              f"{'답변가능' if proposal['answerable'] else '제외후보'} → {proposal['golden_doc_id']} ({proposal['confidence']})")

    # 검수용 CSV — confidence 낮은 것부터 위로 올려서 검수 우선순위를 보여준다
    order = {"low": 0, "medium": 1, "high": 2}
    rows = []
    for item in items:
        p = draft[item["sample_id"]]
        rows.append({
            "sample_id": item["sample_id"],
            "answerable": "O" if p["answerable"] else "X",
            "confidence": p["confidence"],
            "proposed_doc_id": p["golden_doc_id"] or "",
            "alt_doc_id": p["alt_doc_id"] or "",
            "검수": "",
            "question_preview": question_preview(item["question"]),
            "reason": p["reason"],
        })
    rows.sort(key=lambda r: (order.get(r["confidence"], 0), r["sample_id"]))

    with open(REVIEW_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    n_answerable = sum(1 for r in rows if r["answerable"] == "O")
    print(f"\n📝 검수 파일 생성: {REVIEW_PATH}")
    print(f"   답변가능 후보 {n_answerable}개 / 제외 후보 {len(rows) - n_answerable}개")
    print("   → 엑셀로 열어 '검수' 열만 채우세요:")
    print("     비워둠 = 제안 승인 / 다른 doc_id 입력 = 정답 교체 / '제외' = 골든셋에서 뺌")
    print("   → 끝나면: python -m scripts.build_golden_set finalize")


# ---------------------------------------------------------------------------
# 2) finalize — 검수 결과를 golden_set.json으로 확정
# ---------------------------------------------------------------------------

def run_finalize(max_per_doc: int = 3) -> None:
    """검수 CSV → golden_set.json.

    실제 상담엔 단골 문의(탈퇴 등)가 여러 건씩 있어서 그대로 쓰면
    특정 문서가 점수를 지배한다. 그래서:
    - 완전히 똑같은 질문 원문은 하나만 남기고
    - 같은 정답 문서를 가리키는 질문은 문서당 max_per_doc개까지만 넣는다 (0 = 제한 없음)
    표현이 다른 중복은 자연 패러프레이즈라서 몇 개씩은 오히려 평가에 도움이 된다.
    """
    if not os.path.exists(REVIEW_PATH):
        raise RuntimeError(f"{REVIEW_PATH}가 없습니다. propose를 먼저 실행하세요.")

    items = {it["sample_id"]: it for it in load_qa_items()}
    valid_doc_ids = {r["doc_id"] for r in fetch_doc_catalog()}

    golden, excluded, invalid = [], 0, []
    with open(REVIEW_PATH, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            review = (row.get("검수") or "").strip()

            if review in ("제외", "x", "X"):
                excluded += 1
                continue

            doc_id = review if review else row["proposed_doc_id"].strip()
            if not review and row["answerable"] != "O":
                excluded += 1  # 제안이 '답변불가'였고 검수자도 뒤집지 않음
                continue
            if not doc_id:
                excluded += 1
                continue
            if doc_id not in valid_doc_ids:
                invalid.append((row["sample_id"], doc_id))
                continue

            golden.append({
                "sample_id": row["sample_id"],
                "query": items[row["sample_id"]]["question"],  # 상담 질문 원문 전체
                "golden_doc_id": doc_id,
                "source": "real",  # 실제 상담 유래 — 합성 질문(synthetic)과 구분
            })

    # 1) 질문 원문이 완전히 같은 중복 제거 (공백 정규화 기준)
    seen_q, unique, dup_exact = set(), [], 0
    for g in golden:
        key = re.sub(r"\s+", " ", g["query"]).strip()
        if key in seen_q:
            dup_exact += 1
            continue
        seen_q.add(key)
        unique.append(g)

    # 2) 문서별 편중 완화: 같은 정답 문서는 최대 max_per_doc개까지만
    by_doc: Dict[str, List[Dict]] = {}
    for g in unique:
        by_doc.setdefault(g["golden_doc_id"], []).append(g)

    final, capped_out = [], 0
    for doc_id, group in by_doc.items():
        keep = group if not max_per_doc else group[:max_per_doc]
        capped_out += len(group) - len(keep)
        final.extend(keep)
    final.sort(key=lambda x: x["sample_id"])

    # 이미 synthesize로 넣어둔 합성 질문이 있으면 보존하고 real만 갈아끼운다
    if os.path.exists(GOLDEN_PATH):
        with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
            existing_syn = [g for g in json.load(f) if g.get("source") == "synthetic"]
        final.extend(existing_syn)

    with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    n_real = sum(1 for g in final if g.get("source") == "real")
    print(f"✅ 골든셋 확정: {GOLDEN_PATH} — 총 {len(final)}개 (real {n_real} / synthetic {len(final) - n_real})")
    print(f"   (검수 제외 {excluded} / 동일 질문 중복 제거 {dup_exact} / 문서당 {max_per_doc}개 초과분 제외 {capped_out})")

    multi = sorted(((d, len(v)) for d, v in by_doc.items() if len(v) > 1),
                   key=lambda x: -x[1])
    if multi:
        print("   문서별 질문 수 (2개 이상인 것):")
        for doc_id, n in multi:
            kept = min(n, max_per_doc) if max_per_doc else n
            print(f"   - {doc_id}: {n}건 → {kept}건 사용")
    if invalid:
        print(f"⚠️ 목록에 없는 doc_id라 건너뜀 {len(invalid)}건 — 검수 값을 확인하세요:")
        for sid, d in invalid:
            print(f"   - {sid}: {d}")


# ---------------------------------------------------------------------------
# 3) synthesize — 상담이 커버하지 못한 문서에 대해 원문에서 질문 생성
# ---------------------------------------------------------------------------

VIEW_SENTENCES_PATH = "./rag_view_sentences.json"
DOC_CONTENT_CHARS = 2000  # 문서 원문은 앞부분만 프롬프트에 넣는다

SYNTH_SYSTEM_PROMPT = """
당신은 IT 프로젝트 관리 툴의 고객이 되어 질문을 만드는 역할입니다.

[문서 원문]을 읽고, 이 문서가 답이 되는 질문을 실제 고객 말투로 {n}개 만드세요.

[중요한 규칙]
1. [기존 인덱스 문장]과 표현·어휘가 겹치지 않는 새로운 질문일 것.
   (기존 문장을 베끼거나 살짝 바꾼 수준이면 안 됨 — 완전히 다른 각도/말투로)
2. 문서 속 용어를 그대로 복사하지 말고, 그 기능을 모르는 고객이 쓸 법한
   일상적인 표현으로 물을 것. (구어체, 줄임말, 가벼운 오타 섞여도 좋음)
3. 이 문서만 읽으면 답할 수 있는 질문일 것.

[JSON 응답 형식]
{{"questions": ["질문1", "질문2"]}}
"""


def run_synthesize(per_doc: int = 1) -> None:
    """golden_set.json에 없는(=상담 질문이 커버하지 못한) 문서마다 합성 질문을 만들어 보충한다."""
    if not os.path.exists(GOLDEN_PATH):
        raise RuntimeError(f"{GOLDEN_PATH}가 없습니다. finalize를 먼저 실행하세요.")
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        golden = json.load(f)
    covered = {g["golden_doc_id"] for g in golden}

    # 기존 인덱스 문장(hypo_q·real_q 등)을 문서별로 모아 "겹치지 말 것" 예시로 준다
    index_sentences: Dict[str, List[str]] = {}
    if os.path.exists(VIEW_SENTENCES_PATH):
        with open(VIEW_SENTENCES_PATH, "r", encoding="utf-8") as f:
            for row in json.load(f):
                index_sentences.setdefault(row["doc_id"], []).append(row["text"])

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT doc_id, title, section, content FROM answer_units ORDER BY doc_id;")
    docs = cur.fetchall()
    cur.close()
    conn.close()

    targets = [d for d in docs if d["doc_id"] not in covered]
    print(f"✅ 전체 문서 {len(docs)}개 중 상담 질문이 커버한 {len(covered)}개 제외 → {len(targets)}개에 질문 생성")
    if not targets:
        print("   보충할 문서가 없습니다.")
        return

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    added = 0
    for i, doc in enumerate(targets, 1):
        existing = "\n".join(f"- {t}" for t in index_sentences.get(doc["doc_id"], [])) or "(없음)"
        user_prompt = f"""[문서 원문] (doc_id: {doc["doc_id"]} / {doc["title"]} / {doc["section"]})
{doc["content"][:DOC_CONTENT_CHARS]}

[기존 인덱스 문장] — 이것들과 겹치지 않게 만들 것
{existing}
"""
        try:
            response = client.chat.completions.create(
                model=LABEL_MODEL,
                messages=[
                    {"role": "system", "content": SYNTH_SYSTEM_PROMPT.strip().format(n=per_doc)},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,  # 표현 다양성을 위해 높게
            )
            questions = json.loads(response.choices[0].message.content).get("questions", [])[:per_doc]
        except Exception as e:
            print(f"  ⚠️ {doc['doc_id']}: 생성 실패 ({e}) — 건너뜀")
            continue

        for j, q in enumerate(questions, 1):
            golden.append({
                "sample_id": f"syn-{doc['doc_id']}-{j}",
                "query": q,
                "golden_doc_id": doc["doc_id"],
                "source": "synthetic",
            })
            added += 1
            print(f"  [{i}/{len(targets)}] {doc['doc_id']}: {q}")

        # 중간에 끊겨도 진행분은 남도록 매번 저장 (재실행 시 covered에 잡혀 중복 생성 안 됨)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, indent=2)

    n_real = sum(1 for g in golden if g.get("source") == "real")
    print(f"\n✅ 합성 질문 {added}개 추가 → 골든셋 총 {len(golden)}개 (real {n_real} / synthetic {len(golden) - n_real})")
    print("   출력된 질문들을 훑어보고 어색한 건 golden_set.json에서 지우면 됩니다.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "propose"
    if mode == "propose":
        run_propose()
    elif mode == "finalize":
        # 문서당 최대 질문 수. 예: python -m scripts.build_golden_set finalize 5 / 제한 없애려면 0
        cap = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        run_finalize(max_per_doc=cap)
    elif mode == "synthesize":
        # 문서당 생성할 질문 수. 예: python -m scripts.build_golden_set synthesize 2
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        run_synthesize(per_doc=n)
    else:
        print("사용법: python -m scripts.build_golden_set [propose|finalize [문서당_최대]|synthesize [문서당_생성수]]")
