"""router 介面：正式環境接真的 router，本機開發和測試接假的 router，假的 router 在正式環境拒絕啟動。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from road_test.web import main
from road_test.web.app import RoadTest, create_app
from road_test.web.key_check import Refusal
from road_test.web.router import DEV_STUDENT_KEY, DEV_TEACHER_KEY, FakeRouter, router_from_env

from conftest import FRAME_COUNT, FakeDetector, bearer, contract

KEY = "vcr_sk_" + "a1b2c3d4" * 6
ON_FLY = {"FLY_APP_NAME": "road-test"}


def site(data_dir, router) -> TestClient:
    app = create_app(data_dir, load_detector=FakeDetector, workers=0, router=router,
                     road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT),
                                 "final": RoadTest("final", "Mini", FRAME_COUNT)})
    return TestClient(app)


def test_a_student_gets_in_through_the_router_passed_to_create_app(data_dir) -> None:
    with site(data_dir, FakeRouter({KEY: contract("classroom-accepted")})) as client:
        response = client.get("/api/student", headers=bearer(KEY))

    assert response.status_code == 200, response.text
    assert (response.json()["class_id"], response.json()["student_id"]) == (56, 1234)


def test_a_key_the_router_does_not_know_is_refused_with_its_message(data_dir) -> None:
    with site(data_dir, FakeRouter({})) as client:
        response = client.get("/api/student", headers=bearer(KEY))

    assert (response.status_code, response.json()["detail"]) == (401, "無效的 API 金鑰")


def test_when_the_router_is_down_the_site_says_the_check_is_unavailable(data_dir) -> None:
    with site(data_dir, FakeRouter({}, down={KEY})) as client:
        response = client.get("/api/student", headers=bearer(KEY))

    assert (response.status_code, response.json()["detail"]) == (503, "驗證服務暫時無法使用，請稍後再試")


def test_the_real_router_is_the_default_and_answers_through_key_check() -> None:
    asked = []

    def http(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json=contract("classroom-accepted"))

    router = router_from_env({"VCR_URL": "https://staging.test", "VCR_SERVICE_CREDENTIAL": "secret"},
                             transport=httpx.MockTransport(http))
    who = router.check_key(KEY)

    assert (who.student_id, who.display_name) == (1234, "07王小明")
    assert str(asked[0].url) == "https://staging.test/internal/key-check"
    assert json.loads(asked[0].content) == {"api_key": KEY}
    router.check_key(KEY)
    assert len(asked) == 1, "沿用 Key Check 的 60 秒快取"


def test_local_development_can_use_the_fake_router_with_a_student_and_a_teacher() -> None:
    router = router_from_env({"ROAD_TEST_ROUTER": "fake"})

    student, teacher = router.check_key(DEV_STUDENT_KEY), router.check_key(DEV_TEACHER_KEY)

    assert (student.kind, student.class_id) == ("classroom", 56)
    assert teacher.is_teacher
    assert isinstance(router.check_key(KEY), Refusal), "假的 router 不認得的金鑰一樣被拒"


@pytest.mark.parametrize("value", ["fake", " FAKE "])
def test_the_fake_router_refuses_to_start_in_production(value: str) -> None:
    with pytest.raises(SystemExit) as e:
        router_from_env({"ROAD_TEST_ROUTER": value, **ON_FLY})

    assert "ROAD_TEST_ROUTER" in str(e.value), f"錯誤訊息要說是哪個環境變數：{e.value}"


def test_the_site_does_not_start_in_production_with_the_fake_router(monkeypatch) -> None:
    monkeypatch.setenv("ROAD_TEST_ROUTER", "fake")
    monkeypatch.setenv("FLY_APP_NAME", "road-test")

    with pytest.raises(SystemExit) as e:
        main.build_app(main.settings.from_env({}, 2))

    assert "ROAD_TEST_ROUTER" in str(e.value), "在讀路考影片之前就先擋下來"


def test_production_uses_the_real_router() -> None:
    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=contract("refused"))

    router = router_from_env({**ON_FLY, "ROAD_TEST_ROUTER": "real"}, transport=httpx.MockTransport(http))

    assert router.check_key(KEY) == Refusal("invalid", "無效的 API 金鑰")


def test_an_unknown_router_setting_stops_with_a_message() -> None:
    with pytest.raises(SystemExit) as e:
        router_from_env({"ROAD_TEST_ROUTER": "fkae"})

    assert "ROAD_TEST_ROUTER" in str(e.value)
