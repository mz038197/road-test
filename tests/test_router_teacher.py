"""真的 router 的老師登入：對照 token 交換、ID token 和 teacher-classes 的回應範例。

teacher-classes 的範例複製自 vans_coding_router 的 docs/contracts/teacher-classes/。router 沒有 ID token 的範例，
tests/contracts/id-token/claims.json 和 oauth-token/teacher-accepted.json 照它的 oauth_use_case.py（_teacher_token）
和 oauth_signing.py（JWKS、RS256／ES256、kid）寫的。
"""
import base64
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from road_test.web.key_check import ClassRef, Refusal
from road_test.web.oauth import LoginFailure, TeacherGrant
from road_test.web.router import router_from_env
from road_test.web.teacher_classes import ManagedClasses

CONTRACTS = Path(__file__).parent / "contracts"
SECRET = "oauth-client-secret-for-tests"
ENV = {"VCR_URL": "https://router.test/", "VCR_SERVICE_CREDENTIAL": "service-credential",
       "VCR_OAUTH_CLIENT_ID": "road-test", "VCR_OAUTH_CLIENT_SECRET": SECRET,
       "ROAD_TEST_PUBLIC_URL": "https://site.test/"}
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REFUSED, UNAVAILABLE = LoginFailure("refused"), LoginFailure("unavailable")


def sample(folder: str, name: str) -> dict:
    return json.loads((CONTRACTS / folder / f"{name}.json").read_text(encoding="utf-8"))


def public_jwk(key, kid: str, alg: str) -> dict:
    algorithm = jwt.algorithms.RSAAlgorithm if alg == "RS256" else jwt.algorithms.ECAlgorithm
    return {**json.loads(algorithm.to_jwk(key.public_key())), "alg": alg, "use": "sig", "kid": kid}


def id_token(key=RSA_KEY, kid: str = "k1", alg: str = "RS256", **changes) -> str:
    now = int(time.time())
    claims = {**sample("id-token", "claims"), "iat": now, "exp": now + 300, **changes}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


class Http:
    """假的 router 的 HTTP 那一層：/oauth/token 回 token，/oauth/jwks 回 keys。"""

    def __init__(self, token=None, keys=None, jwks=None):
        self.token = token if token is not None else id_token()
        self.jwks = jwks if jwks is not None else httpx.Response(
            200, json={"keys": keys if keys is not None else [public_jwk(RSA_KEY, "k1", "RS256")]})
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if request.url.path == "/oauth/jwks":
            if isinstance(self.jwks, Exception):
                raise self.jwks
            return self.jwks
        if isinstance(self.token, (Exception, httpx.Response)):
            if isinstance(self.token, Exception):
                raise self.token
            return self.token
        return httpx.Response(200, json={**sample("oauth-token", "teacher-accepted"), "id_token": self.token})

    def router(self):
        return router_from_env(ENV, transport=httpx.MockTransport(self))

    def count(self, path: str) -> int:
        return sum(r.url.path == path for r in self.asked)


def test_the_teacher_login_url_asks_for_openid_teacher_with_pkce() -> None:
    url = urlsplit(Http().router().teacher_login_url("the-state", "the-challenge"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}

    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://router.test/oauth/authorize"
    assert query == {"response_type": "code", "client_id": "road-test", "scope": "openid teacher",
                     "redirect_uri": "https://site.test/auth/callback", "state": "the-state",
                     "code_challenge": "the-challenge", "code_challenge_method": "S256"}


@pytest.mark.parametrize("key, alg", [(RSA_KEY, "RS256"), (EC_KEY, "ES256")])
def test_the_code_is_exchanged_for_a_verified_teacher(key, alg) -> None:
    http = Http(token=id_token(key, alg=alg), keys=[public_jwk(key, "k1", alg)])

    grant = http.router().exchange_teacher_code("the-code", "the-verifier")

    assert grant == TeacherGrant(42, "陳老師")
    request = next(r for r in http.asked if r.url.path == "/oauth/token")
    assert {k: v[0] for k, v in parse_qs(request.content.decode()).items()} == {
        "grant_type": "authorization_code", "code": "the-code", "code_verifier": "the-verifier",
        "redirect_uri": "https://site.test/auth/callback"}
    expected = base64.b64encode(f"road-test:{SECRET}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.parametrize("token", [
    pytest.param(lambda: id_token(OTHER_KEY), id="signed-by-another-key"),
    pytest.param(lambda: id_token(aud="another-client"), id="wrong-audience"),
    pytest.param(lambda: id_token(iss="https://evil.test"), id="wrong-issuer"),
    pytest.param(lambda: id_token(exp=int(time.time()) - 3600), id="expired"),
    pytest.param(lambda: id_token(exp=None), id="no-expiry"),
    pytest.param(lambda: id_token(sub=None), id="no-subject"),
    pytest.param(lambda: id_token(sub="not-a-number"), id="subject-not-a-user-id"),
    pytest.param(lambda: id_token(kid="unknown"), id="unknown-key-id"),
    pytest.param(lambda: jwt.encode({**sample("id-token", "claims"), "exp": int(time.time()) + 300},
                                    "a-shared-secret-that-is-long-enough-for-hs256", algorithm="HS256",
                                    headers={"kid": "k1"}), id="symmetric-algorithm"),
    pytest.param(lambda: jwt.encode({**sample("id-token", "claims"), "exp": int(time.time()) + 300},
                                    None, algorithm="none", headers={"kid": "k1"}), id="unsigned"),
    pytest.param(lambda: "not.a.token", id="garbage"),
])
def test_an_id_token_that_does_not_verify_is_a_failed_login(token, caplog) -> None:
    assert Http(token=token()).router().exchange_teacher_code("the-code", "v") == REFUSED
    assert any(r.levelno >= logging.ERROR for r in caplog.records), "要留一筆 ERROR，維運才知道"
    assert "the-code" not in caplog.text


def test_a_key_of_another_type_under_the_same_key_id_is_refused() -> None:
    """header 說 ES256、JWKS 裡這個 kid 是 RS256：不能照 header 選演算法。"""
    http = Http(token=id_token(EC_KEY, alg="ES256"), keys=[public_jwk(RSA_KEY, "k1", "RS256")])

    assert http.router().exchange_teacher_code("c", "v") == REFUSED


def test_the_signing_keys_are_kept_and_fetched_again_when_the_router_rotates() -> None:
    http = Http()
    router = http.router()
    assert isinstance(router.exchange_teacher_code("c", "v"), TeacherGrant)
    assert isinstance(router.exchange_teacher_code("c", "v"), TeacherGrant)
    assert http.count("/oauth/jwks") == 1, "同一把簽章金鑰不用每次抓"

    http.token = id_token(EC_KEY, kid="k2", alg="ES256")
    http.jwks = httpx.Response(200, json={"keys": [public_jwk(EC_KEY, "k2", "ES256")]})

    assert isinstance(router.exchange_teacher_code("c", "v"), TeacherGrant), "router 換金鑰後照樣能登入"
    assert http.count("/oauth/jwks") == 2


def test_unknown_key_ids_do_not_hammer_the_router() -> None:
    http = Http(token=id_token(kid="unknown"))
    router = http.router()

    for _ in range(3):
        assert router.exchange_teacher_code("c", "v") == REFUSED

    assert http.count("/oauth/jwks") == 1


@pytest.mark.parametrize("change", [
    {"token": httpx.Response(400, json=sample("oauth-token", "invalid-grant"))},
])
def test_a_code_the_router_refuses_is_a_failed_login(change) -> None:
    assert Http(**change).router().exchange_teacher_code("used-code", "v") == REFUSED


@pytest.mark.parametrize("change", [
    {"token": httpx.Response(401, json=sample("oauth-token", "invalid-client"))},
    {"token": httpx.Response(503)},
    {"token": httpx.Response(200, json={"token_type": "Bearer"})},
    {"token": httpx.ConnectError("connection refused")},
    {"jwks": httpx.ConnectError("connection refused")},
    {"jwks": httpx.Response(503)},
    {"jwks": httpx.Response(200, text="not json")},
])
def test_a_router_that_cannot_complete_the_teacher_login_is_unavailable(change, caplog) -> None:
    assert Http(**change).router().exchange_teacher_code("the-code", "the-verifier") == UNAVAILABLE
    logged = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert logged
    for secret in (SECRET, "the-code", "the-verifier"):
        assert secret not in logged


class Classes:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def router(self, clock=None):
        options = {"teacher_classes_clock": clock} if clock else {}
        return router_from_env(ENV, transport=httpx.MockTransport(self), **options)


def test_the_classes_a_teacher_manages_come_from_teacher_classes() -> None:
    http = Classes(httpx.Response(200, json=sample("teacher-classes", "accepted")))

    answer = http.router().teacher_classes(42)

    assert answer == ManagedClasses(False, (ClassRef(56, "週一 程式設計"), ClassRef(57, "週三 進階班")))
    request = http.asked[0]
    assert (request.method, str(request.url)) == ("POST", "https://router.test/internal/teacher-classes")
    assert request.headers["Authorization"] == "Bearer service-credential"
    assert json.loads(request.content) == {"user_id": 42}


def test_the_answer_is_kept_for_60_seconds() -> None:
    now = [100.0]
    http = Classes(httpx.Response(200, json=sample("teacher-classes", "accepted")))
    router = http.router(clock=lambda: now[0])

    router.teacher_classes(42)
    now[0] += 59
    router.teacher_classes(42)
    assert len(http.asked) == 1
    router.teacher_classes(43)
    assert len(http.asked) == 2, "每位老師分開留"

    now[0] += 2
    router.teacher_classes(42)
    assert len(http.asked) == 3, "超過 60 秒要重問，班級被拿走才會一分鐘內生效"


def test_someone_who_is_not_a_teacher_is_refused_with_the_routers_message() -> None:
    answer = Classes(httpx.Response(200, json=sample("teacher-classes", "refused"))).router().teacher_classes(42)

    assert answer == Refusal("not_a_teacher", "此帳號不是教師或管理員")


@pytest.mark.parametrize("response", [
    httpx.Response(401, json=sample("teacher-classes", "unauthorized")),
    httpx.Response(400, json=sample("teacher-classes", "bad-request")),
    httpx.Response(503),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"accepted": True}),
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("too slow"),
])
def test_a_router_that_cannot_answer_is_unavailable_and_is_not_kept(response, caplog) -> None:
    http = Classes(response)
    router = http.router()

    answer = router.teacher_classes(42)

    assert answer == Refusal("unavailable", "驗證服務暫時無法使用，請稍後再試")
    assert "service-credential" not in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    router.teacher_classes(42)
    assert len(http.asked) == 2, "故障的回答不留"
