"""
데모앱 기준으로는 setup_cors() 하나만 붙이면 됩니다.
CORS는 보안이 아니라 동작 문제예요 — 이게 없으면 브라우저가 요청을 스스로 취소해서
프론트가 아예 작동하지 않습니다.

아래 require_service / require_admin 은 나중에 실서비스로 갈 때를 위해 넣어뒀습니다.
환경변수(SERVICE_API_KEY / CONSOLE_API_KEY / SUPABASE_JWT_SECRET)를 설정하지 않으면
그냥 통과시키니, 지금은 붙여둬도 아무 영향이 없습니다.

호출 경로가 두 갈래라는 걸 전제로 만들었습니다.
  - 챗봇 프론트  →  Spring Boot  →  FastAPI   (서버 간 내부 호출)
  - 운영 콘솔    →  FastAPI                    (브라우저에서 직접 호출)

main.py에서 이렇게 씁니다.

    from fastapi import Depends
    from security import setup_cors, require_service, require_admin

    app = FastAPI()
    setup_cors(app)

    @app.get("/health")                     # 인증 없이 열어둡니다
    def health():
        return {"status": "ok"}

    @app.post("/search", dependencies=[Depends(require_service)])
    def search(...):                        # Spring Boot가 부르는 경로
        ...

    @app.get("/admin/stats", dependencies=[Depends(require_admin)])
    def stats(...):                         # 운영 콘솔이 부르는 경로
        ...

requirements.txt에 추가:
    pyjwt>=2.8       # 운영 콘솔에 로그인을 붙인 뒤부터 필요합니다
"""

import os
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware


# ─────────────────────────────────────────────────────────────
# 1. CORS
# ─────────────────────────────────────────────────────────────
# 브라우저가 다른 도메인의 API를 부를 수 있게 허용하는 설정입니다.
#
# 주의: CORS는 보안 장치가 아니라 브라우저가 스스로 지키는 규칙입니다.
# curl이나 Postman은 CORS를 무시하고 그냥 호출합니다.
# 실제 차단은 아래 인증과 Azure의 IP 제한이 담당합니다.

def setup_cors(app: FastAPI) -> None:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    if not origins:
        # 비어 있을 때 전체 허용이 되지 않도록 로컬 개발 주소만 남깁니다.
        origins = ["http://localhost:5173", "http://localhost:3000"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


SERVICE_KEY = os.getenv("SERVICE_API_KEY")     # Spring Boot ↔ FastAPI
CONSOLE_KEY = os.getenv("CONSOLE_API_KEY")     # 운영 콘솔 (로그인 붙이기 전 임시)
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")  # 운영 콘솔 (로그인 붙인 뒤)


# ─────────────────────────────────────────────────────────────
# 2. 서비스 간 호출 — Spring Boot가 FastAPI를 부를 때
# ─────────────────────────────────────────────────────────────
# 이쪽은 서버끼리의 통신이라 키가 브라우저에 노출될 일이 없습니다.
# 고정 키로 충분해요.

def require_service(x_api_key: str | None = Header(default=None)):
    if not SERVICE_KEY:
        return {"caller": "local"}          # 로컬 개발
    if x_api_key != SERVICE_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증 실패")
    return {"caller": "backend"}


# ─────────────────────────────────────────────────────────────
# 3. 운영 콘솔 — 두 단계로 나눠서 봅니다
# ─────────────────────────────────────────────────────────────
# [지금] 로그인이 없는 상태
#   CONSOLE_API_KEY 를 헤더에 실어 보냅니다.
#   이 키는 프론트 번들에 박히니 개발자도구를 열면 보입니다.
#   그래서 이 단계에서는 Azure IP 제한이 실질적인 방어선입니다.
#   (가이드 4-3 단계 참고)
#
# [다음] 운영 콘솔에 Supabase 로그인을 붙인 뒤
#   로그인한 사람 중에서도 관리자만 통과시킵니다.
#   단순히 "로그인했나"로는 부족해요 — 챗봇 일반 사용자도 로그인은 하니까요.
#
# 관리자 표시는 app_metadata 에 넣습니다.
# user_metadata 는 사용자가 스스로 고칠 수 있어서 신뢰하면 안 됩니다.
#
#   -- Supabase SQL Editor에서 한 번 실행
#   update auth.users
#      set raw_app_meta_data = raw_app_meta_data || '{"role":"admin"}'::jsonb
#    where email = 'admin@example.com';

def require_admin(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    # ── 로그인 토큰이 왔다면 그쪽을 우선 ──
    if JWT_SECRET and authorization:
        import jwt  # pyjwt

        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "잘못된 토큰 형식")

        token = authorization.removeprefix("Bearer ").strip()
        try:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰 만료")
        except jwt.InvalidTokenError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "유효하지 않은 토큰")

        # 여기가 핵심입니다. 로그인 여부가 아니라 관리자인지를 봅니다.
        role = (payload.get("app_metadata") or {}).get("role")
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자 권한 필요")

        return {"user_id": payload.get("sub"), "role": role}

    # ── 아직 로그인이 없는 단계: 고정 키 ──
    if CONSOLE_KEY:
        if x_api_key != CONSOLE_KEY:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증 실패")
        return {"user_id": None, "role": "console-key"}

    # 둘 다 미설정 — 로컬 개발
    return {"user_id": None, "role": "local"}
