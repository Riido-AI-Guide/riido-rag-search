# Riido RAG Search API — Azure Container Apps 배포용
#
# 빌드 — GitHub Actions에서 한다. ubuntu 러너가 amd64라 ACA(linux/amd64)와 아키텍처가 같다.
# Apple Silicon Mac에서 직접 빌드할 때만 플랫폼을 명시한다(안 하면 arm 이미지가 나와 ACA에서 안 뜬다):
#   docker buildx build --platform linux/amd64 -t riido-rag-api .
#
# 환경변수 — ACA에서는 secret으로 넣는다. .env는 이미지에 들어가지 않는다(.dockerignore).
#   DATABASE_URL, OPENAI_API_KEY   필수. 없으면 부팅 단계에서 바로 실패한다
#   DB_POOL_MIN, DB_POOL_MAX        선택. 레플리카 수 × DB_POOL_MAX가 DB 커넥션 한도를 넘지 않게
#   PORT                            선택. 기본 8000 — ACA ingress의 target port와 맞춘다
#   WEB_CONCURRENCY                 선택. 기본 1 — 아래 CMD 주석 참고

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken

WORKDIR /app

# 의존성을 코드보다 먼저 설치한다 — 코드만 바뀐 빌드는 이 층을 캐시에서 재사용한다.
# 컴파일러가 필요 없다. kiwipiepy-model 하나만 소스로 받지만 모델 데이터뿐이라
# 빌드가 파일 복사로 끝난다(나머지는 전부 linux/amd64 휠이 있다).
COPY requirements.txt .
RUN pip install -r requirements.txt

# OpenAIEmbeddings는 첫 임베딩 때 tiktoken 인코딩 파일을 인터넷에서 받아온다.
# 빌드 때 받아 이미지에 넣어 두면 콜드 스타트가 외부 다운로드에 걸리지 않는다.
RUN python -c "import tiktoken; tiktoken.encoding_for_model('text-embedding-3-small')"

COPY domain ./domain
COPY core ./core
COPY api ./api

# 인덱스 빌드 스크립트와 그 입력. API 서버는 쓰지 않지만, 같은 이미지를 ACA Job으로 띄워
#   python -m scripts.build_answer_units / python -m scripts.build_search_units
# 를 돌릴 수 있게 함께 넣는다(수백 KB라 이미지 크기에 영향이 없다).
COPY scripts ./scripts
COPY data ./data

RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app/.tiktoken
USER app

EXPOSE 8000

# - exec: uvicorn이 PID 1이 되어 ACA가 보내는 SIGTERM을 직접 받는다(리비전 교체·스케일 인 때
#   진행 중인 요청을 마무리하고 내려간다). sh가 PID 1이면 신호가 전달되지 않는다.
#   응답 뒤에 돌던 채점(BackgroundTasks)은 끊길 수 있지만, 저장 전이라 미평가로 남아
#   POST /api/v1/evaluations/run으로 다시 돌릴 수 있다.
# - WEB_CONCURRENCY=1: 워커마다 Kiwi 모델(~100MB)과 DB 풀을 따로 들고, 채점 중복 방지
#   자물쇠(rag_service._in_flight)도 프로세스 단위다. 워커를 늘리기보다 ACA 레플리카를 늘린다.
# - --proxy-headers: ACA ingress(Envoy) 뒤에 있으므로 X-Forwarded-* 를 믿어 원래 scheme을 쓴다.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips='*'"]
