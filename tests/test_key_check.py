"""road-test 自己的 Key Check 用戶端（router ADR 0082）：HTTP 用 httpx.MockTransport 回放 router 的合約範例檔。

範例檔在 tests/contracts/key-check/，從 vans_coding_router 的 docs/contracts/key-check/ 複製
（router commit bff594f，#94）。router 改了範例就重新複製一份。
"""
import json
import logging
from pathlib import Path

import httpx

from road_test.web.key_check import KeyCheck, Refusal

CONTRACTS = Path(__file__).parent / "contracts" / "key-check"
KEY = "vcr_sk_" + "a1b2c3d4" * 6
CREDENTIAL = "road-test-credential"
UNAVAILABLE = "驗證服務暫時無法使用，請稍後再試"


def contract(name: str) -> dict:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeRouter:
    """假的 router：記下每個請求，回答設定好的回應（或丟出設定好的例外）。"""

    def __init__(self, answer=None, status: int = 200):
        self.answer = contract("classroom-accepted") if answer is None else answer
        self.status = status
        self.raises: Exception | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return httpx.Response(self.status, json=self.answer)


def key_check(router: FakeRouter, clock=None) -> KeyCheck:
    return KeyCheck("https://router.test", CREDENTIAL, transport=httpx.MockTransport(router),
                    clock=clock or FakeClock())


def test_a_classroom_key_comes_back_as_a_student_of_its_class() -> None:
    router = FakeRouter(contract("classroom-accepted"))

    who = key_check(router).check(KEY)

    assert (who.kind, who.student_id, who.class_id, who.class_session_id) == ("classroom", 1234, 56, 789)
    request = router.requests[0]
    assert (request.method, request.url.path) == ("POST", "/internal/key-check")
    assert request.headers["Authorization"] == f"Bearer {CREDENTIAL}", "用 road-test 自己的 Service Credential"
    assert json.loads(request.content) == {"api_key": KEY}, "金鑰放在 body，不放網址"
    assert KEY not in str(request.url)


def test_a_personal_key_comes_back_with_the_classes_it_manages() -> None:
    who = key_check(FakeRouter(contract("personal-accepted"))).check(KEY)

    assert (who.kind, who.user_id, who.is_admin) == ("personal", 42, False)
    assert who.classes == ((56, "週一 程式設計"), (57, "週三 進階班")), "班級帶名稱，畫面才顯示得出來"
    assert who.manages(56) and not who.manages(99)
    assert who.is_teacher, "classes 不是空的就有老師身分"


def test_a_personal_key_without_classes_has_no_teacher_standing() -> None:
    answer = {**contract("personal-accepted"), "classes": []}

    who = key_check(FakeRouter(answer)).check(KEY)

    assert not who.is_teacher, "is_admin 為假、classes 是空的：不是老師（router ADR 0083）"
    admin = key_check(FakeRouter({**answer, "is_admin": True})).check(KEY)
    assert admin.is_teacher


def test_a_refusal_carries_the_routers_message() -> None:
    answer = {"accepted": False, "reason": "not_started", "message": "課堂尚未開始，請在開始時間後再使用"}

    refused = key_check(FakeRouter(answer)).check(KEY)

    assert refused == Refusal("not_started", "課堂尚未開始，請在開始時間後再使用"), "訊息照 router 給的顯示"
    assert key_check(FakeRouter(contract("refused"))).check(KEY) == Refusal("invalid", "無效的 API 金鑰")


def test_an_accepted_key_is_kept_for_60_seconds() -> None:
    router, clock = FakeRouter(), FakeClock()
    check = key_check(router, clock)

    check.check(KEY)
    clock.now += 59
    again = check.check(KEY)

    assert len(router.requests) == 1, "60 秒內第二次不打 router（學生輪詢結果頁）"
    assert again.student_id == 1234
    clock.now += 2
    check.check(KEY)
    assert len(router.requests) == 2, "60 秒後重新問 router，停用大約一分鐘內生效"


def test_a_refusal_is_not_kept() -> None:
    router = FakeRouter(contract("refused"))
    check = key_check(router)

    check.check(KEY)
    check.check(KEY)

    assert len(router.requests) == 2, "拒絕不快取"


def test_the_key_is_trimmed_and_a_key_that_is_not_vcr_sk_is_refused_without_asking_the_router() -> None:
    router = FakeRouter()
    check = key_check(router)

    for wrong in ("", "   ", "hello", "sk-abc", "VCR_SK_abc"):
        assert check.check(wrong) == Refusal("invalid", "無效的 API 金鑰"), wrong
    assert router.requests == [], "不是 vcr_sk_ 開頭的不打 router"
    check.check(f"  {KEY}\n")
    assert json.loads(router.requests[0].content) == {"api_key": KEY}, "前後空白去掉再問"


def errors(caplog) -> list[logging.LogRecord]:
    """road_test 底下的 ERROR 記錄就是送到 vans-signals 的 Signal（signals.py）。"""
    return [r for r in caplog.records if r.levelno >= logging.ERROR and r.name.startswith("road_test")]


def assert_unavailable_with_one_error(answer, caplog) -> None:
    assert answer == Refusal("unavailable", UNAVAILABLE)
    sent = errors(caplog)
    assert len(sent) == 1, "每次 Key Check 失敗送一筆 ERROR Signal"
    text = sent[0].getMessage() + (logging.Formatter().formatException(sent[0].exc_info) if sent[0].exc_info else "")
    assert KEY not in text, "Signal 不含完整金鑰"
    assert KEY[:11] in text, "只帶金鑰前綴"


def test_the_router_is_asked_to_answer_within_3_seconds(caplog) -> None:
    router = FakeRouter()
    check = key_check(router)

    check.check(KEY)
    timeout = router.requests[0].extensions["timeout"]
    assert all(0 < v <= 3 for v in timeout.values()), f"最多等 3 秒：{timeout}"

    router.raises = httpx.ReadTimeout("router 太慢")
    assert_unavailable_with_one_error(key_check(router).check(KEY), caplog)


def test_when_the_router_cannot_be_reached_the_key_is_refused(caplog) -> None:
    router = FakeRouter()
    router.raises = httpx.ConnectError("connection refused")

    assert_unavailable_with_one_error(key_check(router).check(KEY), caplog)


def test_router_errors_and_a_wrong_service_credential_count_as_unavailable(caplog) -> None:
    for status in (500, 502, 503, 429, 401, 400):
        caplog.clear()
        answer = key_check(FakeRouter({"error": {"code": "x", "message": "y"}}, status=status)).check(KEY)
        assert_unavailable_with_one_error(answer, caplog)


def test_an_answer_that_is_not_the_contract_counts_as_unavailable(caplog) -> None:
    assert_unavailable_with_one_error(key_check(FakeRouter({"accepted": True})).check(KEY), caplog)


def test_an_expired_cache_is_not_extended_while_the_router_is_down(caplog) -> None:
    router, clock = FakeRouter(), FakeClock()
    check = key_check(router, clock)
    check.check(KEY)

    clock.now += 61
    router.raises = httpx.ConnectError("down")

    assert_unavailable_with_one_error(check.check(KEY), caplog)
    router.raises = None
    clock.now += 1
    assert check.check(KEY).student_id == 1234, "router 回來後重新通過"


def test_settings_come_from_vcr_url_and_vcr_service_credential() -> None:
    router = FakeRouter()
    transport = httpx.MockTransport(router)

    KeyCheck.from_env({}, transport=transport).check(KEY)
    KeyCheck.from_env({"VCR_URL": "https://staging.test/", "VCR_SERVICE_CREDENTIAL": " secret "},
                      transport=transport).check(KEY.replace("a", "b"))

    assert str(router.requests[0].url) == "https://ai.vanscoding.com/internal/key-check", "預設走 router 的公開網址"
    assert str(router.requests[1].url) == "https://staging.test/internal/key-check"
    assert router.requests[1].headers["Authorization"] == "Bearer secret"
