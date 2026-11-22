"""路考網站這個 OAuth Client：到 router 登入、用授權碼換課堂金鑰（road-test ADR 0003、router ADR 0094）。

  VCR_URL                  router 的網址，見 key_check.py
  VCR_OAUTH_CLIENT_ID      在 router 登記的 client id，預設 road-test
  VCR_OAUTH_CLIENT_SECRET  client secret（Fly secret，不進 git）
  ROAD_TEST_PUBLIC_URL     路考網站的公開網址，預設 https://lukao.vanscoding.com；callback 要跟 router 登記的一樣

router 回的 refresh_token 不使用：課堂金鑰的效期就是這堂課。
"""
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from road_test.web.key_check import DEFAULT_URL, TIMEOUT_SECONDS

DEFAULT_CLIENT_ID = "road-test"
DEFAULT_PUBLIC_URL = "https://lukao.vanscoding.com"
CALLBACK_PATH = "/auth/callback"

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
    reason: str  # "refused"（router 不收這個授權碼）或 "unavailable"（router 連不上或回應不對）


REFUSED = LoginFailure("refused")
UNAVAILABLE_LOGIN = LoginFailure("unavailable")


class OAuthClient:
    def __init__(self, router_url: str, client_id: str, client_secret: str, public_url: str,
                 *, transport: httpx.BaseTransport | None = None):
        self._router_url = router_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = public_url.rstrip("/") + CALLBACK_PATH
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, transport: httpx.BaseTransport | None = None) -> "OAuthClient":
        def setting(name: str, default: str = "") -> str:
            return (env.get(name) or "").strip() or default

        return cls(setting("VCR_URL", DEFAULT_URL), setting("VCR_OAUTH_CLIENT_ID", DEFAULT_CLIENT_ID),
                   setting("VCR_OAUTH_CLIENT_SECRET"), setting("ROAD_TEST_PUBLIC_URL", DEFAULT_PUBLIC_URL),
                   transport=transport)

    def student_login_url(self, state: str, code_challenge: str) -> str:
        query = urlencode({"response_type": "code", "client_id": self._client_id, "scope": "classroom",
                           "redirect_uri": self._redirect_uri, "state": state,
                           "code_challenge": code_challenge, "code_challenge_method": "S256"})
        return f"{self._router_url}/oauth/authorize?{query}"

    def exchange_student_code(self, code: str, code_verifier: str) -> ClassroomGrant | LoginFailure:
        try:
            response = self._http.post(
                f"{self._router_url}/oauth/token", auth=(self._client_id, self._client_secret),
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
        grant = _read_grant(response)
        if grant is None:
            log.error("OAuth token 的回應不符合合約")
            return UNAVAILABLE_LOGIN
        return grant


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
