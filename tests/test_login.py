"""學生從首頁登入：用假的 router 跑完登入到下載，只看 HTTP、狀態碼、cookie 和導向。"""
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from road_test.web.app import RoadTest, create_app
from road_test.web.router import FakeRouter

from conftest import CLASSROOM_KEY, DOWN_KEY, FRAME_COUNT, TEACHER_KEY, FakeDetector, router_answers

SITTING = 5400
EXPIRED_NOTICE = "這堂課已經結束，或登入已失效，請重新登入"
UNAVAILABLE_NOTICE = "驗證服務暫時無法使用"


class Clock:
    now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake(data_dir) -> FakeRouter:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "0016E5.mp4").write_bytes(b"training video")
    return FakeRouter(router_answers(), down={DOWN_KEY}, login_as=CLASSROOM_KEY, expires_in=SITTING)


@pytest.fixture
def site(data_dir, fake, clock):
    app = create_app(data_dir, load_detector=FakeDetector, workers=0, router=fake, clock=clock,
                     road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT),
                                 "final": RoadTest("final", "Mini", FRAME_COUNT)})
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        yield client


def set_cookie(response, name: str) -> str:
    return next((h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")), "")


def start(client):
    response = client.get("/auth/student/login")
    assert response.status_code in (302, 303, 307), response.text
    return response, {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def sign_in(client):
    _, query = start(client)
    return client.get("/auth/callback", params={"code": query["code"], "state": query["state"]})


def notice_after(client, response) -> str:
    assert response.status_code in (302, 303), response.text
    target = urlsplit(response.headers["location"])
    assert target.path == "/"
    return client.get(f"/?{target.query}").text


def test_the_home_page_only_offers_login_when_signed_out(site) -> None:
    page = site.get("/")

    assert page.status_code == 200
    assert "路考網站" in page.text and "登入" in page.text and 'href="/auth/student/login"' in page.text
    for teacher_way_in in ("/admin", "teacher.html", "進入後台", "老師登入"):
        assert teacher_way_in not in page.text
    assert not re.search(r"<script(?![^>]*\bsrc=)", page.text), "CSP 不准 inline script"


def test_signing_in_stores_the_classroom_key_in_a_locked_down_cookie_and_opens_the_brief(site) -> None:
    response = sign_in(site)

    assert (response.status_code, response.headers["location"]) == (303, "/brief")
    cookie = set_cookie(response, "road_test_classroom")
    assert cookie.startswith(f"road_test_classroom={CLASSROOM_KEY};"), "金鑰原樣放進 cookie"
    lowered = cookie.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered
    assert f"max-age={SITTING}" in lowered, "有效到 expires_in"
    assert CLASSROOM_KEY not in response.headers["location"], "金鑰不出現在網址"
    assert CLASSROOM_KEY not in response.text
    assert site.get("/brief").status_code == 200


def test_login_uses_state_and_pkce_in_a_short_signed_cookie_on_the_auth_path(site) -> None:
    response, query = start(site)

    assert query["state"]
    cookie = set_cookie(response, "road_test_login").lower()
    assert "path=/auth" in cookie and "max-age=600" in cookie and "httponly" in cookie and "secure" in cookie
    assert "samesite=lax" in cookie
    assert query["state"] != start(site)[1]["state"], "每次登入都是新的 state"


@pytest.mark.parametrize("path", ["/brief", "/index.html"])
def test_the_brief_is_not_reachable_signed_out(site, path: str) -> None:
    response = site.get(path)

    assert response.status_code in (302, 303, 307)
    assert urlsplit(response.headers["location"]).path == "/"


def test_the_brief_has_no_public_static_copy(site) -> None:
    assert site.get("/brief.html").status_code == 404


@pytest.mark.parametrize("path", ["/downloads/0016E5.mp4", "/downloads/train_yolo.py"])
def test_downloads_need_a_login(site, path: str) -> None:
    assert site.get(path).status_code == 401
    sign_in(site)
    assert site.get(path).status_code == 200


def test_a_student_goes_from_login_to_the_download(site) -> None:
    assert site.get("/brief").status_code in (302, 303, 307)

    assert sign_in(site).headers["location"] == "/brief"
    brief = site.get("/brief")
    assert 'href="/downloads/0016E5.mp4"' in brief.text
    assert site.get("/downloads/0016E5.mp4").content == b"training video"


def test_a_signed_in_student_sees_the_way_into_the_road_test_on_the_home_page(site) -> None:
    sign_in(site)

    page = site.get("/").text

    assert "進入路考" in page and 'href="/brief"' in page and "/auth/student/login" not in page


@pytest.mark.parametrize("params", [
    {"code": "c", "state": "wrong"},
    {"code": "c"},
    {"state": "PLACEHOLDER"},
])
def test_a_bad_callback_returns_home_with_a_readable_notice(site, params) -> None:
    _, query = start(site)
    params = {k: (query["state"] if v == "PLACEHOLDER" else v) for k, v in params.items()}

    response = site.get("/auth/callback", params=params)

    assert not set_cookie(response, "road_test_classroom")
    assert "沒有完成" in notice_after(site, response)


def test_a_callback_without_the_login_cookie_returns_home(site) -> None:
    _, query = start(site)
    site.cookies.clear()

    response = site.get("/auth/callback", params={"code": query["code"], "state": query["state"]})

    assert "沒有完成" in notice_after(site, response)


def test_a_forged_login_cookie_is_refused(site) -> None:
    response, query = start(site)
    value = set_cookie(response, "road_test_login").split(";")[0].split("=", 1)[1]
    site.cookies.clear()
    forged = value[:-2] + ("aa" if value[-2:] != "aa" else "bb")

    response = site.get("/auth/callback", params={"code": query["code"], "state": query["state"]},
                        headers={"Cookie": f"road_test_login={forged}"})

    assert "沒有完成" in notice_after(site, response)


def test_a_login_that_took_too_long_returns_home(site, clock) -> None:
    _, query = start(site)
    clock.now += 601

    response = site.get("/auth/callback", params={"code": query["code"], "state": query["state"]})

    assert not set_cookie(response, "road_test_classroom")
    assert "沒有完成" in notice_after(site, response)


def test_a_login_cookie_works_only_once(site) -> None:
    _, query = start(site)
    params = {"code": query["code"], "state": query["state"]}
    assert site.get("/auth/callback", params=params).headers["location"] == "/brief"

    again = site.get("/auth/callback", params=params)

    assert "沒有完成" in notice_after(site, again)


def test_declining_at_the_router_returns_home(site) -> None:
    _, query = start(site)

    response = site.get("/auth/callback", params={"error": "access_denied", "state": query["state"]})

    assert "取消" in notice_after(site, response)


def test_when_the_router_cannot_finish_the_login_the_home_page_says_so(site, fake) -> None:
    fake.login_down = True

    response = sign_in(site)

    assert not set_cookie(response, "road_test_classroom")
    assert UNAVAILABLE_NOTICE in notice_after(site, response)


def test_an_unknown_notice_is_not_echoed(site) -> None:
    page = site.get("/", params={"notice": "<img src=x>"}).text

    assert "<img src=x>" not in page


def test_signing_out_clears_the_cookie_and_returns_home(site) -> None:
    sign_in(site)

    response = site.post("/auth/logout")

    assert (response.status_code, response.headers["location"]) == (303, "/")
    cleared = set_cookie(response, "road_test_classroom").lower()
    assert "max-age=0" in cleared and "path=/" in cleared
    assert site.get("/downloads/train_yolo.py").status_code == 401


def test_a_key_the_router_no_longer_accepts_is_cleared_and_sends_the_student_home(site, fake) -> None:
    fake.login_as = "vcr_sk_ended"
    sign_in(site)

    response = site.get("/brief")

    assert "max-age=0" in set_cookie(response, "road_test_classroom").lower()
    assert EXPIRED_NOTICE in notice_after(site, response)


def test_when_the_router_is_down_the_cookie_stays(site, fake) -> None:
    fake.login_as = DOWN_KEY
    sign_in(site)

    page, download = site.get("/brief"), site.get("/downloads/train_yolo.py")

    assert not set_cookie(page, "road_test_classroom") and not set_cookie(download, "road_test_classroom")
    assert download.status_code == 503
    assert UNAVAILABLE_NOTICE in notice_after(site, page)


def test_a_key_that_stopped_working_cannot_download_and_is_cleared(site, fake) -> None:
    fake.login_as = "vcr_sk_ended"
    sign_in(site)

    response = site.get("/downloads/train_yolo.py")

    assert response.status_code == 401
    assert "max-age=0" in set_cookie(response, "road_test_classroom").lower()


def test_a_personal_key_in_the_cookie_is_not_a_student_login(site, fake) -> None:
    fake.login_as = TEACHER_KEY
    sign_in(site)

    assert site.get("/downloads/train_yolo.py").status_code == 401


def test_the_key_is_never_in_a_page(site) -> None:
    sign_in(site)

    for path in ("/", "/brief"):
        assert CLASSROOM_KEY not in site.get(path).text
