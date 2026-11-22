"""老師從 /admin 用 Google 進後台：用假的 router 跑完登入，只看 HTTP、狀態碼、cookie 和導向（road-test ADR 0003）。"""
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from road_test.web import main
from road_test.web.app import RoadTest, create_app
from road_test.web.oauth import TeacherGrant
from road_test.web.router import FakeRouter

from conftest import (ALL_CLASSES, CLASSROOM_KEY, FRAME_COUNT, OTHER_CLASS_KEY, TEACHER_KEY, FakeDetector, as_student,
                      bearer, model, router_answers, run_queue, teacher_contract)

TWELVE_HOURS = 12 * 3600
SECRET = "teacher-cookie-secret-for-this-test"
TEACHER = TeacherGrant(42, "陳老師")
ADMIN = TeacherGrant(1, "管理員")
OUTSIDER = TeacherGrant(99, "不是老師的人")


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
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mini video")
    accepted = teacher_contract("accepted")
    return FakeRouter(router_answers(), login_as=CLASSROOM_KEY, teacher_login_as=TEACHER, teachers={
        42: accepted, 1: {**accepted, "user_id": 1, "is_admin": True, "classes": ALL_CLASSES}})


def open_site(data_dir, fake, clock, secret: str = SECRET) -> TestClient:
    app = create_app(data_dir, load_detector=FakeDetector, workers=0, router=fake, clock=clock,
                     teacher_cookie_secret=secret,
                     road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT),
                                 "final": RoadTest("final", "Mini", FRAME_COUNT)})
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def site(data_dir, fake, clock):
    with open_site(data_dir, fake, clock) as client:
        yield client


def set_cookie(response, name: str) -> str:
    return next((h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")), "")


def sign_in(client, path: str = "/admin"):
    """從後台頁開始走完老師登入，回傳 callback 的回應。"""
    to_login = client.get(path)
    assert to_login.status_code in (302, 303), to_login.text
    to_router = client.get(to_login.headers["location"])
    assert to_router.status_code in (302, 303), to_router.text
    return client.get(to_router.headers["location"])


def sign_in_student(client) -> None:
    to_router = client.get("/auth/student/login")
    assert client.get(to_router.headers["location"]).headers["location"] == "/brief"


def test_signed_out_the_back_office_goes_to_the_teacher_login(site) -> None:
    response = site.get("/admin")

    assert response.status_code in (302, 303)
    assert urlsplit(response.headers["location"]).path == "/auth/teacher/login"


def test_after_login_the_teacher_lands_on_the_back_office(site) -> None:
    response = sign_in(site)

    assert (response.status_code, response.headers["location"]) == (303, "/admin")
    page = site.get("/admin")
    assert page.status_code == 200 and "後台" in page.text and "teacher.js" in page.text


def test_after_login_the_teacher_returns_to_the_page_they_wanted(site) -> None:
    assert sign_in(site, "/admin?class=57").headers["location"] == "/admin?class=57"


@pytest.mark.parametrize("target", ["https://evil.test/", "//evil.test/", "/brief", "/admin.evil", "/admin/../x",
                                    "/\\evil.test", "/admin\r\nX: y"])
def test_login_never_returns_anywhere_but_the_back_office(site, target) -> None:
    to_router = site.get("/auth/teacher/login", params={"next": target})

    assert site.get(to_router.headers["location"]).headers["location"] == "/admin"


def test_the_old_teacher_page_goes_to_the_back_office(site) -> None:
    response = site.get("/teacher.html")

    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["location"] == "/admin"


def test_the_teacher_cookie_is_locked_down_and_lasts_12_hours(site) -> None:
    cookie = set_cookie(sign_in(site), "road_test_teacher").lower()

    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert f"max-age={TWELVE_HOURS}" in cookie


def test_the_teacher_stays_in_for_12_hours_and_no_longer(site, clock) -> None:
    sign_in(site)

    clock.now += TWELVE_HOURS - 1
    assert site.get("/api/teacher/classes").status_code == 200
    clock.now += 2
    assert site.get("/api/teacher/classes").status_code == 401
    assert site.get("/admin").status_code in (302, 303)


def test_the_login_survives_a_restart_and_changing_the_secret_signs_everyone_out(data_dir, fake, clock, site) -> None:
    sign_in(site)
    cookie = site.cookies["road_test_teacher"]
    header = {"Cookie": f"road_test_teacher={cookie}"}

    with open_site(data_dir, fake, clock) as restarted:
        assert restarted.get("/api/teacher/classes", headers=header).status_code == 200
    with open_site(data_dir, fake, clock, secret="another-secret") as rotated:
        assert rotated.get("/api/teacher/classes", headers=header).status_code == 401


def test_a_forged_teacher_cookie_is_refused(site) -> None:
    sign_in(site)
    value = site.cookies["road_test_teacher"]
    site.cookies.clear()

    for forged in (value[:-2] + ("aa" if value[-2:] != "aa" else "bb"), "x" + value, "garbage", ".", "é"):
        response = site.get("/api/teacher/classes", headers={"Cookie": f"road_test_teacher={forged}".encode()})
        assert response.status_code == 401, forged


def test_a_login_cookie_cannot_be_used_as_a_teacher_cookie(data_dir, fake, clock) -> None:
    app = create_app(data_dir, load_detector=FakeDetector, workers=0, router=fake, clock=clock,
                     cookie_secret=SECRET, teacher_cookie_secret=SECRET,
                     road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT)})
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        pending = set_cookie(client.get("/auth/teacher/login"), "road_test_login").split(";")[0].split("=", 1)[1]
        client.cookies.clear()

        response = client.get("/api/teacher/classes", headers={"Cookie": f"road_test_teacher={pending}"})

    assert response.status_code == 401


def test_a_failed_teacher_login_returns_home_without_a_cookie(site, fake) -> None:
    fake.login_down = True

    response = sign_in(site)

    assert not set_cookie(response, "road_test_teacher")
    assert urlsplit(response.headers["location"]).path == "/"
    assert parse_qs(urlsplit(response.headers["location"]).query)["notice"] == ["unavailable"]


def test_a_student_login_flow_cannot_finish_as_a_teacher(site) -> None:
    to_router = site.get("/auth/student/login")

    response = site.get(to_router.headers["location"])

    assert not set_cookie(response, "road_test_teacher")
    assert response.headers["location"] == "/brief"


def test_signing_out_clears_the_teacher_cookie(site) -> None:
    sign_in(site)

    response = site.post("/auth/logout")

    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert "max-age=0" in set_cookie(response, "road_test_teacher").lower()
    assert site.get("/api/teacher/classes").status_code == 401


def test_the_teacher_sees_only_their_classes(site) -> None:
    sign_in(site)

    assert site.get("/api/teacher/classes").json() == {
        "is_admin": False, "classes": [{"id": 56, "name": "週一 程式設計"}, {"id": 57, "name": "週三 進階班"}]}
    assert site.get("/api/teacher/classes/58").status_code == 404


def test_an_admin_sees_every_class(site, fake) -> None:
    fake.teacher_login_as = ADMIN
    sign_in(site)

    body = site.get("/api/teacher/classes").json()

    assert body["is_admin"] is True and [c["id"] for c in body["classes"]] == [56, 57, 58]


def test_someone_the_router_no_longer_calls_a_teacher_is_refused(site, fake) -> None:
    fake.teacher_login_as = OUTSIDER
    sign_in(site)

    response = site.get("/api/teacher/classes")

    assert (response.status_code, response.json()["detail"]) == (403, "此帳號不是教師或管理員")


def test_when_the_router_is_down_the_teacher_stays_signed_in(site, fake) -> None:
    sign_in(site)
    fake.classes_down = True

    response = site.get("/api/teacher/classes")

    assert (response.status_code, response.json()["detail"]) == (503, "驗證服務暫時無法使用，請稍後再試")
    assert not set_cookie(response, "road_test_teacher")


def test_the_back_office_has_no_key_form_and_carries_the_header_with_sign_out(site) -> None:
    sign_in(site)

    page = site.get("/admin").text

    assert "API Key" not in page and 'type="password"' not in page
    assert 'id="site-actions"' in page and 'src="header.js"' in page, "登出在共用標題列（tests/js/header.test.js）"
    assert "<script>" not in page, "CSP 不准 inline script"


@pytest.mark.parametrize("path", ["/brief", "/downloads/0016E5.mp4", "/downloads/train_yolo.py", "/result.html"])
def test_a_teacher_can_open_the_brief_downloads_and_result_page(site, path: str) -> None:
    sign_in(site)

    assert site.get(path).status_code == 200


def hand_in_as_student(data_dir, fake, clock, site) -> int:
    """56 班的學生選組、交一件。"""
    gid = site.post("/api/teacher/classes/56/groups", json={"name": "第 1 組"}).json()["id"]
    with open_site(data_dir, fake, clock) as student:
        headers = as_student(CLASSROOM_KEY)
        assert student.post("/api/student/group", json={"group_id": gid}, headers=headers).status_code == 201
        sid = student.post("/api/submissions", data={"kind": "mock"}, headers=headers,
                           files={"model": ("best.onnx", model(), "application/octet-stream")}).json()["id"]
        run_queue(student)
    return sid


def test_a_teacher_sees_a_result_and_plays_its_video(data_dir, fake, clock, site) -> None:
    sign_in(site)
    sid = hand_in_as_student(data_dir, fake, clock, site)

    seen = site.get(f"/api/submissions/{sid}")

    assert seen.status_code == 200 and seen.json()["result"]["score"] is not None
    assert site.get(f"/api/submissions/{sid}/detections").status_code == 200
    assert site.get(seen.json()["video_url"]).status_code == 200


def test_only_a_teacher_is_offered_the_back_office_from_a_result(data_dir, fake, clock, site) -> None:
    sign_in(site)
    sid = hand_in_as_student(data_dir, fake, clock, site)

    assert site.get(f"/api/submissions/{sid}").json()["can_manage"] is True
    with open_site(data_dir, fake, clock) as student:
        assert student.get(f"/api/submissions/{sid}",
                           headers=as_student(CLASSROOM_KEY)).json()["can_manage"] is False
        response = student.get("/result.html", headers=as_student(CLASSROOM_KEY))
        assert response.status_code == 200
        page = response.text
    assert "管理頁" not in page and "teacher.html" not in page and "/admin" not in page, "學生看不到後台的入口"


def test_a_teacher_demonstrating_as_a_student_sees_their_own_group_in_a_class_they_do_not_manage(
        data_dir, fake, clock, site) -> None:
    sign_in(site)
    gid = site.post("/api/teacher/classes/57/groups", json={"name": "第 1 組"}).json()["id"]
    fake.login_as = OTHER_CLASS_KEY
    sign_in_student(site)
    assert site.post("/api/student/group", json={"group_id": gid}).status_code == 201
    sid = site.post("/api/submissions", data={"kind": "mock"},
                    files={"model": ("best.onnx", model(), "application/octet-stream")}).json()["id"]
    fake.teachers[42] = {**fake.teachers[42], "classes": [{"id": 56, "name": "週一 程式設計"}]}

    with open_site(data_dir, fake, clock) as fresh:
        fresh.cookies.update(dict(site.cookies))
        seen = fresh.get(f"/api/submissions/{sid}")

    assert seen.status_code == 200 and seen.json()["can_manage"] is False


@pytest.mark.parametrize("path", ["/api/teacher/classes", "/api/submissions/1"])
def test_a_teacher_api_without_a_login_tells_the_page_to_go_home(site, path: str) -> None:
    response = site.get(path)

    assert response.status_code == 401
    assert response.headers["X-Login-Notice"] == "login_required"


def test_an_expired_teacher_cookie_tells_the_page_to_go_home(site, clock) -> None:
    sign_in(site)
    clock.now += TWELVE_HOURS + 1

    response = site.get("/api/teacher/classes")

    assert (response.status_code, response.headers["X-Login-Notice"]) == (401, "login_required")


def test_a_teacher_cannot_see_a_result_of_a_class_they_do_not_manage(data_dir, fake, clock, site) -> None:
    sign_in(site)
    sid = hand_in_as_student(data_dir, fake, clock, site)
    fake.teachers[42] = {**fake.teachers[42], "classes": [{"id": 57, "name": "週三 進階班"}]}

    with open_site(data_dir, fake, clock) as fresh:
        sign_in(fresh)
        assert fresh.get(f"/api/submissions/{sid}").status_code == 404


@pytest.mark.parametrize("method, path", [("get", "/api/student"), ("get", "/api/student/records"),
                                          ("post", "/api/student/group")])
def test_a_teacher_has_no_group(site, method: str, path: str) -> None:
    sign_in(site)

    response = site.request(method, path, json={"group_id": 1})

    assert response.status_code in (401, 403)


def test_a_teacher_cookie_cannot_hand_in(site) -> None:
    sign_in(site)

    response = site.post("/api/submissions", data={"kind": "mock"},
                         files={"model": ("best.onnx", model(), "application/octet-stream")})

    assert response.status_code in (401, 403)


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/teacher/classes", None), ("get", "/api/teacher/classes/56", None),
    ("post", "/api/teacher/classes/56/groups", {"name": "第 1 組"}),
    ("put", "/api/teacher/classes/56/final", {"open": True}),
])
def test_a_student_cookie_cannot_open_the_back_office_api(site, method, path, body) -> None:
    sign_in_student(site)

    assert site.request(method, path, json=body).status_code == 401


def test_a_student_cookie_does_not_open_the_back_office_page(site) -> None:
    sign_in_student(site)

    assert urlsplit(site.get("/admin").headers["location"]).path == "/auth/teacher/login"


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/teacher/classes", None), ("get", "/api/teacher/classes/56", None),
    ("get", "/api/teacher/classes/56/records", None),
    ("post", "/api/teacher/classes/56/groups", {"name": "第 1 組"}),
    ("put", "/api/teacher/classes/56/final", {"open": True}),
])
def test_the_old_authorization_header_no_longer_opens_the_back_office(site, method, path, body) -> None:
    response = site.request(method, path, json=body, headers=bearer(TEACHER_KEY))

    assert response.status_code == 401


def test_a_personal_key_no_longer_opens_a_result(data_dir, fake, clock, site) -> None:
    sign_in(site)
    sid = hand_in_as_student(data_dir, fake, clock, site)
    site.cookies.clear()

    assert site.get(f"/api/submissions/{sid}", headers=bearer(TEACHER_KEY)).status_code == 401


def test_production_refuses_to_start_without_the_signing_secret(monkeypatch) -> None:
    monkeypatch.setenv("FLY_APP_NAME", "road-test")
    monkeypatch.setenv("ROAD_TEST_ROUTER", "real")
    monkeypatch.setenv("ROAD_TEST_TEACHER_COOKIE_SECRET", "   ")

    with pytest.raises(SystemExit) as e:
        main.build_app(main.settings.from_env({}, 2))

    assert "ROAD_TEST_TEACHER_COOKIE_SECRET" in str(e.value)
