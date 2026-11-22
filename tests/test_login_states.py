"""登入的各種狀態，全部從 HTTP 看：課堂結束、router 故障、金鑰被拒、沒登入能看到什麼、顯示名稱跟著 router。"""
import hashlib
import hmac
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from road_test.web import main
from road_test.web.app import RoadTest, create_app
from road_test.web.login import _seal
from road_test.web.oauth import TeacherGrant
from road_test.web.router import FakeRouter

from conftest import CLASSROOM_KEY, DOWN_KEY, FRAME_COUNT, FakeDetector, model, router_answers, run_queue, teacher_contract

SITTING = 5400
ONE_WEEK = 7 * 24 * 3600
EXPIRED = "這堂課已經結束，或登入已失效，請重新登入"
OUTAGE = "驗證服務暫時無法使用"
KEY_COOKIE = "road_test_classroom"
MARKER_COOKIE = "road_test_signed_in"
TEACHER_SECRET = "teacher-cookie-secret-for-this-test"
ENDED_KEY = "vcr_sk_ended"
LOGIN_LINK = 'href="/auth/student/login"'


@pytest.fixture
def fake(data_dir) -> FakeRouter:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "0016E5.mp4").write_bytes(b"training video")
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mini video")
    return FakeRouter(router_answers(), down={DOWN_KEY}, login_as=CLASSROOM_KEY, expires_in=SITTING,
                      teacher_login_as=TeacherGrant(42, "陳老師"), teachers={42: teacher_contract("accepted")})


@pytest.fixture
def site(data_dir, fake):
    app = create_app(data_dir, load_detector=FakeDetector, workers=0, router=fake,
                     cookie_secret=TEACHER_SECRET,
                     road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT),
                                 "final": RoadTest("final", "Mini", FRAME_COUNT)})
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        yield client


def set_cookie(response, name: str) -> str:
    return next((h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")), "")


def cleared(response, name: str) -> bool:
    return "max-age=0" in set_cookie(response, name).lower()


def sign_in_student(client):
    to_router = client.get("/auth/student/login")
    query = parse_qs(urlsplit(to_router.headers["location"]).query)
    return client.get("/auth/callback", params={"code": query["code"][0], "state": query["state"][0]})


def sign_in_teacher(client) -> None:
    to_login = client.get("/admin")
    to_router = client.get(to_login.headers["location"])
    assert client.get(to_router.headers["location"]).headers["location"] == "/admin"


def class_ended(client) -> None:
    """瀏覽器在 expires_in 到了之後把課堂金鑰的 cookie 丟掉，標記還在。"""
    client.cookies.delete(KEY_COOKIE)


def notice_of(response) -> str:
    assert response.status_code in (302, 303), response.text
    assert urlsplit(response.headers["location"]).path == "/"
    return parse_qs(urlsplit(response.headers["location"]).query)["notice"][0]


def header_of(page: str) -> str:
    return re.search(r'<header class="site-header">.*?</header>', page, re.S).group(0)


# 課堂正常結束


def test_signing_in_leaves_a_marker_that_says_nothing_and_outlives_the_key_by_a_bounded_time(site) -> None:
    response = sign_in_student(site)

    marker = set_cookie(response, MARKER_COOKIE)
    lowered = marker.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert marker.startswith(f"{MARKER_COOKIE}=1;"), "標記不放金鑰也不放身分"
    max_age = int(re.search(r"max-age=(\d+)", lowered).group(1))
    assert SITTING < max_age <= SITTING + ONE_WEEK


def test_after_the_class_ends_the_home_page_says_so_once(site) -> None:
    sign_in_student(site)
    class_ended(site)

    first = site.get("/")

    assert EXPIRED in first.text and LOGIN_LINK in first.text
    assert cleared(first, MARKER_COOKIE)
    assert EXPIRED not in site.get("/").text, "說過一次就好"


@pytest.mark.parametrize("path", ["/brief", "/submit.html", "/result.html"])
def test_after_the_class_ends_a_page_sends_the_student_home_with_the_reason(site, path: str) -> None:
    sign_in_student(site)
    class_ended(site)

    assert notice_of(site.get(path)) == "expired"


def test_after_the_class_ends_the_api_tells_the_page_the_reason(site) -> None:
    sign_in_student(site)
    class_ended(site)

    response = site.get("/api/student")

    assert (response.status_code, response.headers["X-Login-Notice"]) == (401, "expired")
    assert response.json()["detail"] == EXPIRED


def test_the_header_check_does_not_use_up_the_class_ended_message(site) -> None:
    sign_in_student(site)
    class_ended(site)

    assert not set_cookie(site.get("/api/me"), MARKER_COOKIE), "同一頁的其他請求還要靠標記說明原因"


@pytest.mark.parametrize("path", ["/brief", "/submit.html"])
def test_a_first_time_visitor_is_asked_to_log_in(site, path: str) -> None:
    assert notice_of(site.get(path)) == "login_required"
    assert "請先登入" in site.get("/?notice=login_required").text


def test_signing_out_clears_the_key_and_the_marker(site) -> None:
    sign_in_student(site)

    response = site.post("/auth/logout")

    assert cleared(response, KEY_COOKIE) and cleared(response, MARKER_COOKIE)
    assert notice_of(site.get("/brief")) == "login_required", "自己登出不是課堂結束"


# router 故障不是登出


def test_during_an_outage_the_header_says_so_and_keeps_the_cookie(site, fake) -> None:
    fake.login_as = DOWN_KEY
    sign_in_student(site)

    response = site.get("/api/me")

    assert response.status_code == 200
    assert response.json()["role"] is None and OUTAGE in response.json()["message"]
    assert "set-cookie" not in response.headers


def test_during_an_outage_the_home_page_says_so_and_does_not_offer_login(site, fake) -> None:
    fake.login_as = DOWN_KEY
    sign_in_student(site)

    page = site.get("/")

    assert OUTAGE in page.text and LOGIN_LINK not in page.text
    assert "set-cookie" not in page.headers


@pytest.mark.parametrize("path", ["/brief", "/submit.html", "/result.html"])
def test_during_an_outage_a_page_stays_put_and_says_so(site, fake, path: str) -> None:
    fake.login_as = DOWN_KEY
    sign_in_student(site)

    page = site.get(path)

    assert page.status_code == 503 and "location" not in page.headers
    assert OUTAGE in page.text and LOGIN_LINK not in page.text
    assert "set-cookie" not in page.headers


# 沒登入只看得到首頁


@pytest.mark.parametrize("path", ["/submit.html", "/result.html", "/submit", "/result", "/submit.js", "/result.js",
                                  "/teacher.js", "/player.js", "/playback.js", "/step-nav.js",
                                  "/%73ubmit.html", "/./submit.html", "/x/../submit.html", "/SUBMIT.HTML"])
def test_signed_out_no_other_page_or_script_is_served(site, path: str) -> None:
    response = site.get(path)

    assert response.status_code != 200, f"{path} 沒登入不能看"
    assert 'id="submit"' not in response.text


@pytest.mark.parametrize("path", ["/style.css", "/common.js", "/header.js", "/logo.svg"])
def test_what_the_home_page_needs_stays_public(site, path: str) -> None:
    assert site.get(path).status_code == 200


def test_everything_the_home_page_links_to_loads_signed_out(site) -> None:
    for link in re.findall(r'<(?:link|script|img)[^>]+(?:href|src)="([^"#?]+)', site.get("/").text):
        assert site.get(f"/{link.lstrip('/')}").status_code == 200, link


@pytest.mark.parametrize("path", ["/submit.html", "/result.html", "/submit.js"])
def test_signed_in_the_pages_open(site, path: str) -> None:
    sign_in_student(site)

    assert site.get(path).status_code == 200


def test_a_teacher_opens_the_pages_too(site) -> None:
    sign_in_teacher(site)

    assert site.get("/result.html").status_code == 200


# 被拒的金鑰到處都清


@pytest.mark.parametrize("path", ["/", "/api/me", "/brief", "/submit.html", "/api/student", "/downloads/train_yolo.py"])
def test_a_refused_key_is_cleared_everywhere(site, fake, path: str) -> None:
    fake.login_as = ENDED_KEY
    sign_in_student(site)

    assert cleared(site.get(path), KEY_COOKIE), f"{path} 要清掉被拒的金鑰"


def test_the_home_page_explains_a_refused_key(site, fake) -> None:
    fake.login_as = ENDED_KEY
    sign_in_student(site)

    page = site.get("/")

    assert EXPIRED in page.text and LOGIN_LINK in page.text
    assert cleared(page, MARKER_COOKIE)


def test_a_refused_key_seen_by_the_header_still_explains_itself_on_the_next_page(site, fake) -> None:
    fake.login_as = ENDED_KEY
    sign_in_student(site)

    assert site.get("/api/me").json() == {"role": None}

    assert notice_of(site.get("/brief")) == "expired"


def test_a_teacher_with_a_refused_demo_key_has_it_cleared_and_stays_a_teacher(site, fake) -> None:
    sign_in_teacher(site)
    both = {"Cookie": f"road_test_teacher={site.cookies['road_test_teacher']}; {KEY_COOKIE}={ENDED_KEY}"}

    for path in ("/", "/api/me"):
        assert cleared(site.get(path, headers=both), KEY_COOKIE), path
    assert site.get("/api/me", headers=both).json()["role"] == "teacher"


# 老師的入口在標題列


def test_after_login_a_teacher_sees_the_back_office_in_the_header_of_the_home_page(site) -> None:
    sign_in_teacher(site)

    header = header_of(site.get("/").text)

    assert "進入後台" in header and 'href="/admin"' in header


@pytest.mark.parametrize("sign_in", [sign_in_student, lambda client: None])
def test_nobody_else_sees_the_back_office_on_the_home_page(site, sign_in) -> None:
    sign_in(site)

    page = site.get("/").text

    assert "進入後台" not in page and "/admin" not in page


# 正式環境只要一把 cookie 簽章密語


def test_production_starts_with_the_one_cookie_secret(monkeypatch, data_dir) -> None:
    monkeypatch.setenv("FLY_APP_NAME", "road-test")
    monkeypatch.setenv("ROAD_TEST_ROUTER", "real")
    monkeypatch.setenv("ROAD_TEST_DATA", str(data_dir))
    monkeypatch.setenv("ROAD_TEST_TEACHER_COOKIE_SECRET", "set")
    monkeypatch.delenv("ROAD_TEST_COOKIE_SECRET", raising=False)
    monkeypatch.setattr(main, "frame_count", lambda video: FRAME_COUNT)

    app = main.build_app(main.settings.from_env({}, 2))

    assert app.title


# 顯示名稱每次 Key Check 都跟著 router


def joined(site) -> None:
    group = site.app.state.store.add_class_group(56, "第 1 組", 1)
    assert site.post("/api/student/group", json={"group_id": group["id"]}).status_code == 201


def stored_name(site) -> str:
    return next(m["display_name"] for m in site.app.state.store.members(56) if m["student_id"] == 1234)


def renamed(fake, name: str) -> None:
    fake._answers[CLASSROOM_KEY] = {**fake._answers[CLASSROOM_KEY], "display_name": name}


@pytest.mark.parametrize("path", ["/api/me", "/", "/brief", "/submit.html"])
def test_the_display_name_updates_wherever_the_key_is_checked(site, fake, path: str) -> None:
    sign_in_student(site)
    joined(site)
    renamed(fake, "王小明")

    site.get(path)

    assert stored_name(site) == "王小明"


def test_the_display_name_updates_when_opening_a_result(site, fake) -> None:
    sign_in_student(site)
    joined(site)
    sid = site.post("/api/submissions", data={"kind": "mock"},
                    files={"model": ("best.onnx", model(), "application/octet-stream")}).json()["id"]
    run_queue(site)
    renamed(fake, "王小明")

    assert site.get(f"/api/submissions/{sid}").status_code == 200

    assert stored_name(site) == "王小明"


def test_an_unchanged_display_name_writes_nothing(site, monkeypatch) -> None:
    sign_in_student(site)
    joined(site)
    statements: list[str] = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", traced)

    assert site.get("/api/me").status_code == 200

    assert not [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "BEGIN IMMEDIATE"))]


# 舊格式的 cookie


def test_a_teacher_cookie_in_the_old_format_is_just_signed_out(site) -> None:
    key = hmac.new(TEACHER_SECRET.encode(), b"road-test-teacher-cookie", hashlib.sha256).digest()
    old = _seal(key, {"u": 42, "n": "陳老師", "e": 9_999_999_999})

    response = site.get("/api/me", headers={"Cookie": f"road_test_teacher={old}"})

    assert (response.status_code, response.json()) == (200, {"role": None})
