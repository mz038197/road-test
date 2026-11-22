"""學生從首頁、老師從 /admin 登入（road-test ADR 0003）：/auth 底下的路徑和三張 cookie。

  road_test_login      登入途中的 state 和 PKCE verifier：簽章、10 分鐘、只限 /auth
  road_test_classroom  課堂金鑰原樣放這裡：HttpOnly、Secure、SameSite=Lax，有效到 expires_in
  road_test_teacher    網站自己簽章的老師身分（user id、顯示名稱、到期）：同樣的旗標，12 小時

課堂金鑰不出現在網址、記錄或 JavaScript 讀得到的地方。
"""
import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from road_test.web.key_check import INVALID, Identity, Refusal
from road_test.web.oauth import ClassroomGrant, LoginFailure, TeacherGrant
from road_test.web.router import Router

CLASSROOM_COOKIE = "road_test_classroom"
LOGIN_COOKIE = "road_test_login"
LOGIN_SECONDS = 600
TEACHER_COOKIE = "road_test_teacher"
TEACHER_SECONDS = 12 * 3600
TEACHER_SECRET_ENV = "ROAD_TEST_TEACHER_COOKIE_SECRET"
ADMIN_PATH = "/admin"

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


@dataclass(frozen=True)
class TeacherSession:
    """老師 cookie 說的人：router 的 user id 和顯示名稱。"""
    user_id: int
    display_name: str


class TeacherCookie:
    """網站自己簽章的老師 cookie（ADR 0003）。換掉簽章密語，所有老師都要重新登入。"""

    def __init__(self, secret: bytes, clock: Callable[[], float]):
        # 從密語導出專用的金鑰：就算跟登入途中那張 cookie 設成同一把密語，兩種 cookie 也不能互換
        self._key = hmac.new(secret, b"road-test-teacher-cookie", hashlib.sha256).digest()
        self._clock = clock

    def issue(self, grant: TeacherGrant) -> str:
        return _seal(self._key, {"u": grant.user_id, "n": grant.display_name,
                                 "e": int(self._clock()) + TEACHER_SECONDS})

    def read(self, request: Request) -> TeacherSession | None:
        payload = _open(self._key, request.cookies.get(TEACHER_COOKIE, ""))
        if payload is None or self._clock() >= payload["e"]:
            return None
        return TeacherSession(payload["u"], payload["n"])


def teacher_cookie_secret(env: Mapping[str, str]) -> str | None:
    """老師 cookie 的簽章密語。正式環境（Fly）沒設就拒絕啟動；本機沒設時 create_app 每次啟動隨機產生。"""
    secret = (env.get(TEACHER_SECRET_ENV) or "").strip()
    if not secret and env.get("FLY_APP_NAME"):
        raise SystemExit(f"正式環境一定要設定環境變數 {TEACHER_SECRET_ENV}（Fly secret），網站拒絕啟動。")
    return secret or None


def _back_office_page(target: str) -> str:
    """登入後回到哪裡：只回後台，不轉到別的網站或別的頁面。"""
    return target if target == ADMIN_PATH or (target.startswith(f"{ADMIN_PATH}?") and target.isprintable()) else ADMIN_PATH


def login_routes(router: Router, clock: Callable[[], float], secret: bytes, teacher_cookie: TeacherCookie) -> APIRouter:
    routes = APIRouter()

    def start(kind: str, login_url: Callable[[str, str], str], **extra) -> Response:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        response = RedirectResponse(login_url(state, challenge), status_code=303)
        response.set_cookie(LOGIN_COOKIE, _seal(secret, {"s": state, "v": verifier, "k": kind,
                                                         "e": int(clock()) + LOGIN_SECONDS, **extra}),
                            max_age=LOGIN_SECONDS, path="/auth", **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.get("/auth/student/login")
    def student_login():
        return start("student", router.student_login_url)

    @routes.get("/auth/teacher/login")
    def teacher_login(next: str = ADMIN_PATH):
        return start("teacher", router.teacher_login_url, n=_back_office_page(next))

    def finish(request: Request, state: str, code: str, error: str) -> tuple[Response, ClassroomGrant | TeacherGrant | None]:
        pending = _open(secret, request.cookies.get(LOGIN_COOKIE, ""))
        if (pending is None or pending.get("k") not in ("student", "teacher") or clock() > pending["e"]
                or not hmac.compare_digest(state.encode(), pending["s"].encode())):
            return go_home("login_failed"), None
        if error:
            return go_home("login_denied" if error == "access_denied" else "login_failed"), None
        if not code:
            return go_home("login_failed"), None
        teacher = pending["k"] == "teacher"
        grant = (router.exchange_teacher_code if teacher else router.exchange_student_code)(code, pending["v"])
        if isinstance(grant, LoginFailure):
            return go_home("unavailable" if grant.reason == "unavailable" else "login_failed"), None
        return RedirectResponse(_back_office_page(pending.get("n", "")) if teacher else "/brief", status_code=303), grant

    @routes.get("/auth/callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        response, grant = finish(request, state, code, error)
        response.delete_cookie(LOGIN_COOKIE, path="/auth", **_COOKIE_FLAGS)  # state 只能用一次
        if isinstance(grant, ClassroomGrant):
            response.set_cookie(CLASSROOM_COOKIE, grant.key, max_age=grant.expires_in, path="/", **_COOKIE_FLAGS)
        elif isinstance(grant, TeacherGrant):
            response.set_cookie(TEACHER_COOKIE, teacher_cookie.issue(grant), max_age=TEACHER_SECONDS, path="/",
                                **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.post("/auth/logout")
    def logout():
        response = RedirectResponse("/", status_code=303)
        clear_classroom_cookie(response)
        response.delete_cookie(TEACHER_COOKIE, path="/", **_COOKIE_FLAGS)
        return response

    return routes
