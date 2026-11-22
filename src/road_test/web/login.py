"""學生從首頁、老師從 /admin 登入（road-test ADR 0003）：/auth 底下的路徑和四張 cookie。

  road_test_login      登入途中的 state 和 PKCE verifier：簽章、10 分鐘、只限 /auth
  road_test_classroom  課堂金鑰原樣放這裡：HttpOnly、Secure、SameSite=Lax，有效到 expires_in
  road_test_signed_in  標記「這個瀏覽器登入過」，值只有 1：比金鑰多活一天，金鑰過期後才分得出課堂結束和第一次來
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
from enum import StrEnum
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from road_test.web.key_check import UNAVAILABLE_REASON, Identity, Refusal
from road_test.web.oauth import ClassroomGrant, LoginFailure, TeacherGrant
from road_test.web.router import Router

CLASSROOM_COOKIE = "road_test_classroom"
LOGIN_COOKIE = "road_test_login"
LOGIN_SECONDS = 600
MARKER_COOKIE = "road_test_signed_in"
MARKER_EXTRA_SECONDS = 24 * 3600  # 標記比課堂金鑰多活多久：超過就當第一次來，看到「請先登入」
TEACHER_COOKIE = "road_test_teacher"
TEACHER_SECONDS = 12 * 3600
TEACHER_SECRET_ENV = "ROAD_TEST_TEACHER_COOKIE_SECRET"
COOKIE_SECRET_ENV = "ROAD_TEST_COOKIE_SECRET"
ADMIN_PATH = "/admin"
NOTICE_HEADER = "X-Login-Notice"  # 學生 API 回 401 時帶首頁提示條的代碼，前端照著導回首頁

NOTICES = {  # 首頁的提示條：網址只帶代碼，文字一律從這裡來，不把網址上的內容放進頁面
    "login_required": "請先登入。",
    "login_failed": "這次登入沒有完成或已經逾時，請再按一次「登入」。",
    "login_denied": "登入已取消，要繼續請再按一次「登入」。",
    "expired": "這堂課已經結束，或登入已失效，請重新登入",
    UNAVAILABLE_REASON: "驗證服務暫時無法使用，請稍後再試",
}
ATTEMPT_NOTICES = ("login_failed", "login_denied", UNAVAILABLE_REASON)  # 剛才那次登入的結果，比「課堂結束」優先

_COOKIE_FLAGS = {"httponly": True, "secure": True, "samesite": "lax"}


class LoginKind(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"


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
    """只清金鑰、留著標記：同一頁接下來的請求才說得出「課堂結束」。標記由首頁說過一次後清掉。"""
    response.delete_cookie(CLASSROOM_COOKIE, path="/", **_COOKIE_FLAGS)


def clear_marker(response: Response) -> None:
    response.delete_cookie(MARKER_COOKIE, path="/", **_COOKIE_FLAGS)


def has_marker(request: Request) -> bool:
    return MARKER_COOKIE in request.cookies


def signed_out_notice(request: Request) -> str:
    """沒有金鑰的人該看到哪個提示：登入過的是課堂結束，第一次來的是請先登入。"""
    return "expired" if has_marker(request) else "login_required"


def classroom_identity(request: Request, router: Router) -> Identity | Refusal | None:
    """cookie 裡的課堂金鑰是誰：沒登入是 None，Key Check 不收或連不上是 Refusal。"""
    key = request.cookies.get(CLASSROOM_COOKIE)
    return router.check_key(key) if key else None


def refused_key(who: Refusal) -> HTTPException:
    """API 怎麼回被拒的金鑰：router 連不上回 503、不清 cookie；其他回 401 並清掉金鑰。"""
    if who.is_outage:
        return HTTPException(503, who.message)
    cleared = Response()
    clear_classroom_cookie(cleared)
    return HTTPException(401, NOTICES["expired"],
                         headers={NOTICE_HEADER: "expired", "Set-Cookie": cleared.headers["set-cookie"]})


def require_student(request: Request, who: Identity | Refusal | None) -> Identity:
    """要學生身分的 API：不是學生就丟出對應的 HTTPException。"""
    if who is None:
        notice = signed_out_notice(request)
        raise HTTPException(401, NOTICES[notice], headers={NOTICE_HEADER: notice})
    if isinstance(who, Refusal):
        raise refused_key(who)
    return who


def back_home(request: Request, who: Refusal | None) -> Response:
    """頁面擋下沒登入或金鑰被拒的人（router 故障不走這裡）：回首頁說明原因，被拒的金鑰順便清掉。"""
    if who is None:
        return go_home(signed_out_notice(request))
    response = go_home("expired")
    clear_classroom_cookie(response)
    return response


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
        return _seal(self._key, {"user_id": grant.user_id, "display_name": grant.display_name,
                                 "expires": int(self._clock()) + TEACHER_SECONDS})

    def read(self, request: Request) -> TeacherSession | None:
        payload = _open(self._key, request.cookies.get(TEACHER_COOKIE, ""))
        # 舊格式的 cookie 簽章對得上但沒有這些欄位：當成沒登入，重新登入一次就好
        if payload is None or "expires" not in payload or self._clock() >= payload["expires"]:
            return None
        return TeacherSession(payload["user_id"], payload["display_name"])


def required_secret(env: Mapping[str, str], name: str) -> str | None:
    """cookie 的簽章密語。正式環境（Fly）沒設就拒絕啟動；本機沒設時 create_app 每次啟動隨機產生。"""
    secret = (env.get(name) or "").strip()
    if not secret and env.get("FLY_APP_NAME"):
        raise SystemExit(f"正式環境一定要設定環境變數 {name}（Fly secret），網站拒絕啟動。")
    return secret or None


def _back_office_page(target: str) -> str:
    """登入後回到哪裡：只回後台，不轉到別的網站或別的頁面。"""
    return target if target == ADMIN_PATH or (target.startswith(f"{ADMIN_PATH}?") and target.isprintable()) else ADMIN_PATH


def login_routes(router: Router, clock: Callable[[], float], secret: bytes, teacher_cookie: TeacherCookie) -> APIRouter:
    routes = APIRouter()

    def start(kind: LoginKind, login_url: Callable[[str, str], str], **extra) -> Response:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        response = RedirectResponse(login_url(state, challenge), status_code=303)
        response.set_cookie(LOGIN_COOKIE, _seal(secret, {"state": state, "verifier": verifier, "kind": kind,
                                                         "expires": int(clock()) + LOGIN_SECONDS, **extra}),
                            max_age=LOGIN_SECONDS, path="/auth", **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.get("/auth/student/login")
    def student_login():
        return start(LoginKind.STUDENT, router.student_login_url)

    @routes.get("/auth/teacher/login")
    def teacher_login(next: str = ADMIN_PATH):
        return start(LoginKind.TEACHER, router.teacher_login_url, next=_back_office_page(next))

    def finish(request: Request, state: str, code: str, error: str) -> tuple[Response, ClassroomGrant | TeacherGrant | None]:
        pending = _open(secret, request.cookies.get(LOGIN_COOKIE, ""))
        if (pending is None or pending.get("kind") not in tuple(LoginKind) or clock() > pending["expires"]
                or not hmac.compare_digest(state.encode(), pending["state"].encode())):
            return go_home("login_failed"), None
        if error:
            return go_home("login_denied" if error == "access_denied" else "login_failed"), None
        if not code:
            return go_home("login_failed"), None
        teacher = pending["kind"] == LoginKind.TEACHER
        grant = (router.exchange_teacher_code if teacher else router.exchange_student_code)(code, pending["verifier"])
        if isinstance(grant, LoginFailure):
            return go_home(UNAVAILABLE_REASON if grant.reason == UNAVAILABLE_REASON else "login_failed"), None
        return RedirectResponse(_back_office_page(pending.get("next", "")) if teacher else "/brief", status_code=303), grant

    @routes.get("/auth/callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        response, grant = finish(request, state, code, error)
        response.delete_cookie(LOGIN_COOKIE, path="/auth", **_COOKIE_FLAGS)  # state 只能用一次
        if isinstance(grant, ClassroomGrant):
            response.set_cookie(CLASSROOM_COOKIE, grant.key, max_age=grant.expires_in, path="/", **_COOKIE_FLAGS)
            response.set_cookie(MARKER_COOKIE, "1", max_age=grant.expires_in + MARKER_EXTRA_SECONDS, path="/",
                                **_COOKIE_FLAGS)
        elif isinstance(grant, TeacherGrant):
            response.set_cookie(TEACHER_COOKIE, teacher_cookie.issue(grant), max_age=TEACHER_SECONDS, path="/",
                                **_COOKIE_FLAGS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.post("/auth/logout")
    def logout():
        response = RedirectResponse("/", status_code=303)
        clear_classroom_cookie(response)
        clear_marker(response)
        response.delete_cookie(TEACHER_COOKIE, path="/", **_COOKIE_FLAGS)
        return response

    return routes
