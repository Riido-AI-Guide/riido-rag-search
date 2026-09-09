"""
scripts/build_qna_logs.py — 질의응답 로그·평가 테이블 생성

인덱스 빌드와 달리 적재할 데이터가 없다. 테이블만 만든다.
개발자마다 DB가 따로라 마이그레이션 절차가 없으므로, 컬럼이 늘면 여기에
ALTER TABLE ... ADD COLUMN IF NOT EXISTS를 덧붙인다(빌드가 스키마를 맞춘다).

실행: python -m scripts.build_qna_logs
"""

from core.db import connect


def setup_qna_tables() -> None:
    conn = connect()
    cur = conn.cursor()

    # 평가에 넣는 입력과, 그걸 되짚을 최소한만 남긴다.
    #   - cleaned_query, retrieved_doc_ids : 백엔드에 아예 없는 값이다
    #   - answer_text : 백엔드에도 있지만 대화가 삭제되면 사라진다. 채점 대상 텍스트가
    #     없어지면 과거 점수는 무엇을 채점한 건지 알 수 없는 숫자가 되므로 스냅샷을 남긴다
    #   - answer_type : 평가 로직이 직접 쓴다(no_search는 건너뛰고 no_answer는 규칙이 다르다)
    #
    # doc_id에는 FK를 걸지 않는다. build_answer_units가 사라진 문서를 지울 때
    # CASCADE로 과거 로그까지 삭제된다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qna_logs (
            qna_uuid          UUID PRIMARY KEY,
            conversation_id   TEXT,
            raw_query         TEXT NOT NULL,
            cleaned_query     TEXT NOT NULL,
            answer_text       TEXT NOT NULL,
            answer_type       TEXT NOT NULL,
            retrieved_doc_ids TEXT[] NOT NULL DEFAULT '{}',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qna_logs_created ON qna_logs (created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qna_logs_conv ON qna_logs (conversation_id);")

    # 답변 1건에 평가 1건. 재실행하면 덮어쓴다.
    # 평가에 실패하면 행을 남기지 않는다 — 행이 없어야 미평가 목록에 다시 잡히고
    # 운영 콘솔에서 다시 돌릴 수 있다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS answer_evaluations (
            qna_uuid          UUID PRIMARY KEY REFERENCES qna_logs(qna_uuid) ON DELETE CASCADE,
            faithfulness      REAL NOT NULL,
            answer_relevance  REAL NOT NULL,
            context_relevance REAL NOT NULL,
            verdict           TEXT NOT NULL,
            issues            TEXT[] NOT NULL DEFAULT '{}',
            reason            TEXT NOT NULL DEFAULT '',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    setup_qna_tables()
    print("✅ qna_logs · answer_evaluations 준비 완료")
