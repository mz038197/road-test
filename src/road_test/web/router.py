"""router 介面：路考網站跟 router 打交道的事都經過這裡，由 main 傳進 create_app。

正式環境接真的 router（RealRouter），測試和本機開發接假的 router（FakeRouter）。

  ROAD_TEST_ROUTER  real（預設）或 fake。fake 只能在本機用：在 Fly 上（有 FLY_APP_NAME）會拒絕啟動
  VCR_URL、VCR_SERVICE_CREDENTIAL  真的 router，見 key_check.py
  VCR_OAUTH_CLIENT_ID、VCR_OAUTH_CLIENT_SECRET、ROAD_TEST_PUBLIC_URL  登入，見 oauth.py
"""
import base64
import hashlib
import secrets
import time
from collections.abc import Collection, Mapping
from typing import Callable, Protocol
from urllib.parse import urlencode

from road_test.web.key_check import DEFAULT_URL, INVALID, UNAVAILABLE, Identity, KeyCheck, Refusal, read_answer
from road_test.web.oauth import (CALLBACK_PATH, REFUSED, UNAVAILABLE_LOGIN, ClassroomGrant, LoginFailure, OAuthClient,
                                 TeacherGrant)
from road_test.web.teacher_classes import ManagedClasses, TeacherClasses
from road_test.web.teacher_classes import read_answer as read_classes

# 假的 router 在本機開發時認得的金鑰（不是真的金鑰，真的 router 不認得）
DEV_STUDENT_KEY = "vcr_sk_dev_student"
DEV_SITTING_SECONDS = 3 * 3600  # 假的 router 的一堂課
_DEV_ANSWERS = {
    DEV_STUDENT_KEY: {"accepted": True, "kind": "classroom", "student_id": 1234, "class_id": 56,
                      "class_session_id": 789, "display_name": "本機學生"},
}
DEV_TEACHER = TeacherGrant(42, "本機老師")
_DEV_TEACHERS = {42: {"accepted": True, "user_id": 42, "is_admin": False, "classes": [{"id": 56, "name": "本機班級"}]}}


class Router(Protocol):
    def check_key(self, api_key: str) -> Identity | Refusal:
        """Key Check：這把金鑰是誰，或為什麼被拒（router 連不上時是 UNAVAILABLE）。"""
        ...

    def student_login_url(self, state: str, code_challenge: str) -> str:
        """學生登入（classroom scope）要轉去的授權網址，帶 state 和 PKCE（S256）。"""
        ...

    def exchange_student_code(self, code: str, code_verifier: str) -> ClassroomGrant | LoginFailure:
        """用授權碼加 PKCE verifier 換課堂金鑰和它的到期秒數。"""
        ...

    def teacher_login_url(self, state: str, code_challenge: str) -> str:
        """老師登入（openid teacher scope）要轉去的授權網址，帶 state 和 PKCE（S256）。"""
        ...

    def exchange_teacher_code(self, code: str, code_verifier: str) -> TeacherGrant | LoginFailure:
        """用授權碼換 ID token 並驗過，拿到老師的 user id 和顯示名稱。"""
        ...

    def teacher_classes(self, user_id: int) -> ManagedClasses | Refusal:
        """這位老師能管理哪些班級（admin 是全部），留 60 秒；不是老師或 router 連不上是 Refusal。"""
        ...


class RealRouter:
    """真的 router：Key Check 沿用 KeyCheck 的 60 秒快取和 3 秒逾時，登入走 OAuthClient。"""

    def __init__(self, key_check: KeyCheck, oauth: OAuthClient, teacher_classes: TeacherClasses):
        self._key_check = key_check
        self._oauth = oauth
        self._teacher_classes = teacher_classes

    def check_key(self, api_key: str) -> Identity | Refusal:
        return self._key_check.check(api_key)

    def student_login_url(self, state: str, code_challenge: str) -> str:
        return self._oauth.student_login_url(state, code_challenge)

    def exchange_student_code(self, code: str, code_verifier: str) -> ClassroomGrant | LoginFailure:
        return self._oauth.exchange_student_code(code, code_verifier)

    def teacher_login_url(self, state: str, code_challenge: str) -> str:
        return self._oauth.teacher_login_url(state, code_challenge)

    def exchange_teacher_code(self, code: str, code_verifier: str) -> TeacherGrant | LoginFailure:
        return self._oauth.exchange_teacher_code(code, code_verifier)

    def teacher_classes(self, user_id: int) -> ManagedClasses | Refusal:
        return self._teacher_classes.of(user_id)


class FakeRouter:
    """假的 router：answers 是 {金鑰: router 的 Key Check 回應}；down 裡的金鑰當成 router 連不上。

    登入時不經過登入頁，直接帶授權碼回 callback，登入成 login_as 這把金鑰。授權碼只能用一次、要對得上 PKCE，
    跟真的 router 一樣。login_down=True 時換 token 當成 router 連不上。
    老師登入成 teacher_login_as；teachers 是 {user id: router 的 teacher-classes 回應}，classes_down=True 時當成連不上。
    """

    def __init__(self, answers: Mapping[str, dict], *, down: Collection[str] = (), login_as: str = DEV_STUDENT_KEY,
                 expires_in: int = DEV_SITTING_SECONDS, login_down: bool = False,
                 teachers: Mapping[int, dict] | None = None, teacher_login_as: TeacherGrant = DEV_TEACHER):
        self._answers = answers
        self._down = down
        self.login_as = login_as
        self._expires_in = expires_in
        self.login_down = login_down
        self.teachers = dict(_DEV_TEACHERS if teachers is None else teachers)
        self.teacher_login_as = teacher_login_as
        self.classes_down = False
        self._codes: dict[str, str] = {}  # 授權碼 → code_challenge

    def check_key(self, api_key: str) -> Identity | Refusal:
        api_key = (api_key or "").strip()
        if api_key in self._down:
            return UNAVAILABLE
        answer = self._answers.get(api_key)
        return read_answer(answer) if answer else INVALID

    def student_login_url(self, state: str, code_challenge: str) -> str:
        code = secrets.token_urlsafe(16)
        self._codes[code] = code_challenge
        return f"{CALLBACK_PATH}?{urlencode({'code': code, 'state': state})}"

    def _redeem(self, code: str, code_verifier: str) -> LoginFailure | None:
        if self.login_down:
            return UNAVAILABLE_LOGIN
        challenge = self._codes.pop(code, None)
        digest = hashlib.sha256(code_verifier.encode()).digest()
        if challenge != base64.urlsafe_b64encode(digest).rstrip(b"=").decode():
            return REFUSED
        return None

    def exchange_student_code(self, code: str, code_verifier: str) -> ClassroomGrant | LoginFailure:
        return self._redeem(code, code_verifier) or ClassroomGrant(self.login_as, self._expires_in)

    def teacher_login_url(self, state: str, code_challenge: str) -> str:
        return self.student_login_url(state, code_challenge)  # 假的 router 沒有登入頁，兩種登入一樣直接回 callback

    def exchange_teacher_code(self, code: str, code_verifier: str) -> TeacherGrant | LoginFailure:
        return self._redeem(code, code_verifier) or self.teacher_login_as

    def teacher_classes(self, user_id: int) -> ManagedClasses | Refusal:
        if self.classes_down:
            return UNAVAILABLE
        answer = self.teachers.get(user_id)
        if answer is None:
            return Refusal("not_a_teacher", "此帳號不是教師或管理員")
        return read_classes(answer)


def router_from_env(env: Mapping[str, str], *, teacher_classes_clock: Callable[[], float] = time.monotonic,
                    **options) -> Router:
    """依 ROAD_TEST_ROUTER 選 router；options 傳給 KeyCheck 和 OAuthClient（測試換 transport）。"""
    choice = (env.get("ROAD_TEST_ROUTER") or "real").strip().lower()
    if choice == "real":
        url = (env.get("VCR_URL") or "").strip() or DEFAULT_URL
        credential = (env.get("VCR_SERVICE_CREDENTIAL") or "").strip()
        return RealRouter(KeyCheck.from_env(env, **options), OAuthClient.from_env(env, **options),
                          TeacherClasses(url, credential, clock=teacher_classes_clock, **options))
    if choice != "fake":
        raise SystemExit(f"環境變數 ROAD_TEST_ROUTER 要是 real 或 fake，現在是「{choice}」。")
    if env.get("FLY_APP_NAME"):
        raise SystemExit("環境變數 ROAD_TEST_ROUTER=fake 不能用在正式環境，網站拒絕啟動。")
    return FakeRouter(_DEV_ANSWERS)
