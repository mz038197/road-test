"""學生從首頁登入（road-test ADR 0003）：/auth 底下的路徑和兩張 cookie。

  road_test_login      登入途中的 state 和 PKCE verifier：簽章、10 分鐘、只限 /auth
  road_test_classroom  課堂金鑰原樣放這裡：HttpOnly、Secure、SameSite=Lax，有效到 expires_in

課堂金鑰不出現在網址、記錄或 JavaScript 讀得到的地方。
"""
import base64
import hashlib
import hmac
import json
import secrets
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from road_test.web.key_check import INVALID, Identity, Refusal
from road_test.web.oauth import ClassroomGrant
from road_test.web.router import Router

CLASSROOM_COOKIE = "road_test_classroom"
LOGIN_COOKIE = "road_test_login"
LOGIN_SECONDS = 600

NOTICES = {  # 首頁的提示條：網址只帶代碼，文字一律從這裡來，不把網址上的內容放進頁面
    "login_required": "請先登入。",
    "login_failed": "這次登入沒有完成或已經逾時，請再按一次「登入」。",
    "login_denied": "登入已取消，要繼續請再按一次「登入」。",
    "expired": "這堂課已經結束，或登入已失效，請重新登入",
    "unavailable": "驗證服務暫時無法使用，請稍後再試",
}

_COOKIE_FLAGS = {"httponly": True, "secure": True, "samesite": "lax"}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _seal(secret: bytes, payload: dict) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()}"


def _open(secret: bytes, sealed: str) -> dict | None:
    body, _, signature = sealed.partition(".")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def go_home(notice: str) -> RedirectResponse:
    return RedirectResponse(f"/?notice={notice}", status_code=303)


def clear_classroom_cookie(response: Response) -> None:
    response.delete_cookie(CLASSROOM_COOKIE, path="/", **_COOKIE_FLAGS)


def classroom_identity(request: Request, router: Router) -> Identity | Refusal | None:
    """cookie 裡的課堂金鑰是誰：沒登入是 None，Key Check 不收或連不上是 Refusal。"""
    key = request.cookies.get(CLASSROOM_COOKIE)
    if not key:
        return None
    who = router.check_key(key)
    if isinstance(who, Identity) and who.kind != "classroom":
        return INVALID  # 這張 cookie 只放課堂金鑰
    return who


def login_routes(router: Router, clock: Callable[[], float], secret: bytes) -> APIRouter:
    routes = APIRouter()

    @routes.get("/auth/student/login")
    def student_login():
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        response = RedirectResponse(router.student_login_url(state, challenge), status_code=303)
        response.set_cookie(LOGIN_COOKIE, _seal(secret, {"s": state, "v": verifier, "k": "student",
                                                         "e": int(clock()) + LOGIN_SECONDS}),
                            max_age=LOGIN_SECONDS, path="/auth", **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    def finish(request: Request, state: str, code: str, error: str) -> tuple[Response, ClassroomGrant | None]:
        pending = _open(secret, request.cookies.get(LOGIN_COOKIE, ""))
        if (pending is None or pending.get("k") != "student" or clock() > pending["e"]
                or not hmac.compare_digest(state.encode(), pending["s"].encode())):
            return go_home("login_failed"), None
        if error:
            return go_home("login_denied" if error == "access_denied" else "login_failed"), None
        if not code:
            return go_home("login_failed"), None
        grant = router.exchange_student_code(code, pending["v"])
        if not isinstance(grant, ClassroomGrant):
            return go_home("unavailable" if grant.reason == "unavailable" else "login_failed"), None
        return RedirectResponse("/brief", status_code=303), grant

    @routes.get("/auth/callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        response, grant = finish(request, state, code, error)
        response.delete_cookie(LOGIN_COOKIE, path="/auth", **_COOKIE_FLAGS)  # state 只能用一次
        if grant is not None:
            response.set_cookie(CLASSROOM_COOKIE, grant.key, max_age=grant.expires_in, path="/", **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.post("/auth/logout")
    def logout():
        response = RedirectResponse("/", status_code=303)
        clear_classroom_cookie(response)
        return response

    return routes
