"""真的 router 的學生登入：對照 token 交換的回應範例。

範例在 tests/contracts/oauth-token/。router 沒有這份範例，內容照 vans_coding_router 的
oauth_use_case.py（_classroom_token、TokenRequestError）和 test_oauth_authorization_server.py 寫的。
"""
import base64
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from road_test.web.router import ClassroomGrant, LoginFailure, router_from_env

CONTRACTS = Path(__file__).parent / "contracts" / "oauth-token"
SECRET = "oauth-client-secret-for-tests"
ENV = {"VCR_URL": "https://router.test/", "VCR_OAUTH_CLIENT_ID": "road-test", "VCR_OAUTH_CLIENT_SECRET": SECRET,
       "ROAD_TEST_PUBLIC_URL": "https://site.test/"}


def sample(name: str) -> dict:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def router_answering(answer):
    asked: list[httpx.Request] = []

    def http(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return router_from_env(ENV, transport=httpx.MockTransport(http)), asked


def test_the_login_url_asks_the_router_for_the_classroom_scope_with_pkce() -> None:
    router, _ = router_answering(httpx.Response(500))

    url = urlsplit(router.student_login_url("the-state", "the-challenge"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}

    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://router.test/oauth/authorize"
    assert query == {"response_type": "code", "client_id": "road-test", "scope": "classroom",
                     "redirect_uri": "https://site.test/auth/callback", "state": "the-state",
                     "code_challenge": "the-challenge", "code_challenge_method": "S256"}


def test_the_code_is_exchanged_for_the_classroom_key_and_its_lifetime() -> None:
    router, asked = router_answering(httpx.Response(200, json=sample("classroom-accepted")))

    grant = router.exchange_student_code("the-code", "the-verifier")

    assert grant == ClassroomGrant("vcr_sk_contract_sample_classroom_key", 5400)
    request = asked[0]
    assert (request.method, str(request.url)) == ("POST", "https://router.test/oauth/token")
    assert {k: v[0] for k, v in parse_qs(request.content.decode()).items()} == {
        "grant_type": "authorization_code", "code": "the-code", "code_verifier": "the-verifier",
        "redirect_uri": "https://site.test/auth/callback"}
    expected = base64.b64encode(f"road-test:{SECRET}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}", "client secret 放標頭，不放網址"


def test_a_code_the_router_refuses_is_a_failed_login() -> None:
    router, _ = router_answering(httpx.Response(400, json=sample("invalid-grant")))

    assert router.exchange_student_code("used-code", "v") == LoginFailure("refused")


@pytest.mark.parametrize("answer", [
    httpx.Response(401, json=sample("invalid-client")),
    httpx.Response(503),
    httpx.Response(200, json={"token_type": "Bearer"}),
    httpx.Response(200, json={**sample("classroom-accepted"), "expires_in": 0}),
    httpx.Response(200, text="not json"),
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("too slow"),
])
def test_a_router_that_cannot_complete_the_exchange_is_unavailable(answer, caplog) -> None:
    router, _ = router_answering(answer)

    assert router.exchange_student_code("the-code", "the-verifier") == LoginFailure("unavailable")
    logged = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert logged, "要留一筆 ERROR，維運才知道登入壞了"
    for secret in (SECRET, "the-code", "the-verifier"):
        assert secret not in logged
