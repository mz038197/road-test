"""路考網站這個 OAuth Client：到 router 登入、用授權碼換課堂金鑰（road-test ADR 0003、router ADR 0094）。

  VCR_URL                  router 的網址，見 key_check.py
  VCR_OAUTH_CLIENT_ID      在 router 登記的 client id，預設 road-test
  VCR_OAUTH_CLIENT_SECRET  client secret（Fly secret，不進 git）
  ROAD_TEST_PUBLIC_URL     路考網站的公開網址，預設 https://lukao.vanscoding.com；callback 要跟 router 登記的一樣

router 回的 refresh_token 不使用：課堂金鑰的效期就是這堂課。
"""
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

import httpx
import jwt

from road_test.web.key_check import DEFAULT_URL, TIMEOUT_SECONDS, UNAVAILABLE_REASON

DEFAULT_CLIENT_ID = "road-test"
DEFAULT_PUBLIC_URL = "https://lukao.vanscoding.com"
CALLBACK_PATH = "/auth/callback"
ID_TOKEN_ALGORITHMS = ("RS256", "ES256")  # router 的簽章金鑰只會是這兩種（router 的 oauth_signing.py）
CLOCK_SKEW_SECONDS = 30  # 容許 router 和網站的時鐘差幾秒
JWKS_RETRY_SECONDS = 60

log = logging.getLogger(__name__)  # road_test 底下的 ERROR 會送到 vans-signals（signals.py）


@dataclass(frozen=True)
class ClassroomGrant:
    """換到的課堂金鑰和它還能用幾秒（到這堂課結束）。"""
    key: str
    expires_in: int

    def __repr__(self) -> str:  # 金鑰不能跟著例外或記錄跑出去
        return f"ClassroomGrant(expires_in={self.expires_in})"


@dataclass(frozen=True)
class LoginFailure:
    reason: str  # "refused"（router 不收這個授權碼）或 UNAVAILABLE_REASON（router 連不上或回應不對）


@dataclass(frozen=True)
class TeacherGrant:
    """驗過的 ID token 說的老師：router 的 user id 和顯示名稱。"""
    user_id: int
    display_name: str


REFUSED = LoginFailure("refused")
UNAVAILABLE_LOGIN = LoginFailure(UNAVAILABLE_REASON)


def _bad_token(why: str) -> LoginFailure:
    log.error("ID token 驗證失敗：%s", why)  # 不記 token 內容
    return REFUSED


class OAuthClient:
    def __init__(self, router_url: str, client_id: str, client_secret: str, public_url: str,
                 *, transport: httpx.BaseTransport | None = None):
        self._router_url = router_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = public_url.rstrip("/") + CALLBACK_PATH
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)
        self._jwks: dict[str, dict] = {}  # kid → router 的公鑰
        self._missing_kid: tuple[str, float] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, transport: httpx.BaseTransport | None = None) -> "OAuthClient":
        def setting(name: str, default: str = "") -> str:
            return (env.get(name) or "").strip() or default

        return cls(setting("VCR_URL", DEFAULT_URL), setting("VCR_OAUTH_CLIENT_ID", DEFAULT_CLIENT_ID),
                   setting("VCR_OAUTH_CLIENT_SECRET"), setting("ROAD_TEST_PUBLIC_URL", DEFAULT_PUBLIC_URL),
                   transport=transport)

    def _login_url(self, scope: str, state: str, code_challenge: str) -> str:
        query = urlencode({"response_type": "code", "client_id": self._client_id, "scope": scope,
                           "redirect_uri": self._redirect_uri, "state": state,
                           "code_challenge": code_challenge, "code_challenge_method": "S256"})
        return f"{self._router_url}/oauth/authorize?{query}"

    def student_login_url(self, state: str, code_challenge: str) -> str:
        return self._login_url("classroom", state, code_challenge)

    def teacher_login_url(self, state: str, code_challenge: str) -> str:
        return self._login_url("openid teacher", state, code_challenge)

    def _redeem(self, code: str, code_verifier: str) -> httpx.Response | LoginFailure:
        try:
            response = self._http.post(
                f"{self._router_url}/oauth/token",
                # RFC 6749 §2.3.1：id 和 secret 先表單編碼再放進 Basic，router 會用 unquote_plus 解回來
                auth=(quote_plus(self._client_id), quote_plus(self._client_secret)),
                data={"grant_type": "authorization_code", "code": code, "code_verifier": code_verifier,
                      "redirect_uri": self._redirect_uri})
        except httpx.HTTPError as e:
            log.error("OAuth token 交換失敗：%s", type(e).__name__)  # 不記例外內容，避免帶到授權碼
            return UNAVAILABLE_LOGIN
        if response.status_code == 400 and _error_code(response) == "invalid_grant":
            return REFUSED  # 授權碼過期、用過、或 PKCE 對不上
        if response.status_code != 200:
            log.error("OAuth token 交換失敗：HTTP %s（%s）", response.status_code, _error_code(response))
            return UNAVAILABLE_LOGIN
        return response

    def exchange_student_code(self, code: str, code_verifier: str) -> ClassroomGrant | LoginFailure:
        response = self._redeem(code, code_verifier)
        if isinstance(response, LoginFailure):
            return response
        grant = _read_grant(response)
        if grant is None:
            log.error("OAuth token 的回應不符合合約")
            return UNAVAILABLE_LOGIN
        return grant

    def exchange_teacher_code(self, code: str, code_verifier: str) -> TeacherGrant | LoginFailure:
        """換 ID token，驗簽章（router 的 JWKS）、issuer、audience、到期，拿到老師的 user id 和顯示名稱。"""
        response = self._redeem(code, code_verifier)
        if isinstance(response, LoginFailure):
            return response
        try:
            token = response.json()["id_token"]
        except (ValueError, KeyError, TypeError):
            token = None
        if not isinstance(token, str) or not token:
            log.error("OAuth token 的回應不符合合約")
            return UNAVAILABLE_LOGIN
        return self._verify(token)

    def _verify(self, token: str) -> TeacherGrant | LoginFailure:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            return _bad_token(type(e).__name__)
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ID_TOKEN_ALGORITHMS or not isinstance(kid, str):
            return _bad_token("演算法或 kid 不對")
        jwk = self._signing_key(kid)
        if isinstance(jwk, LoginFailure):
            return jwk
        if jwk is None or jwk.get("alg") != alg:  # 演算法看 JWKS 登記的，不照 token 自己說的
            return _bad_token("JWKS 沒有對應的金鑰")
        try:
            claims = jwt.decode(token, jwt.PyJWK(jwk, alg).key, algorithms=[alg], audience=self._client_id,
                                issuer=self._router_url, leeway=CLOCK_SKEW_SECONDS,
                                options={"require": ["exp", "iss", "aud", "sub"]})
        except (jwt.InvalidTokenError, jwt.PyJWKError) as e:
            return _bad_token(type(e).__name__)
        subject, name = claims["sub"], claims.get("name", "")
        if not (isinstance(subject, str) and subject.isascii() and subject.isdigit()) or not isinstance(name, str):
            return _bad_token("sub 或 name 不符合合約")
        return TeacherGrant(int(subject), name)

    def _signing_key(self, kid: str) -> dict | None | LoginFailure:
        """這個 kid 的公鑰；沒看過就重抓 JWKS（router 換金鑰），找不到的 kid 一分鐘內不重抓。"""
        if kid in self._jwks:
            return self._jwks[kid]
        now = time.monotonic()
        if self._missing_kid and self._missing_kid[0] == kid and now < self._missing_kid[1]:
            return None
        try:
            response = self._http.get(f"{self._router_url}/oauth/jwks")
            if response.status_code != 200:
                log.error("JWKS 失敗：HTTP %s", response.status_code)
                return UNAVAILABLE_LOGIN
            self._jwks = {key["kid"]: key for key in response.json()["keys"]}
        except httpx.HTTPError as e:
            log.error("JWKS 失敗：%s", type(e).__name__)
            return UNAVAILABLE_LOGIN
        except (ValueError, KeyError, TypeError):
            log.error("JWKS 的回應不符合合約")
            return UNAVAILABLE_LOGIN
        if kid not in self._jwks:
            self._missing_kid = (kid, now + JWKS_RETRY_SECONDS)
        return self._jwks.get(kid)


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or "")[:40]
    except (ValueError, AttributeError):
        return ""


def _read_grant(response: httpx.Response) -> ClassroomGrant | None:
    try:
        body = response.json()
        key, expires_in = body["access_token"], body["expires_in"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(key, str) or not key or type(expires_in) is not int or expires_in <= 0:
        return None
    return ClassroomGrant(key, expires_in)
