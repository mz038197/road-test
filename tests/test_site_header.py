"""全站共用標題列的伺服器這一半：誰登入了、首頁帶他去哪裡、每一頁都有標題列（#6）。"""
import re

import pytest

from road_test.web.oauth import TeacherGrant
from road_test.web.router import FakeRouter

from conftest import CLASSROOM_KEY, DOWN_KEY, as_student, router_answers, teacher_contract

PAGES = ["/", "/brief", "/submit.html", "/result.html", "/admin"]


@pytest.fixture
def site(open_site, data_dir):
    (data_dir / "videos").mkdir()
    return open_site(router=FakeRouter(router_answers(), down={DOWN_KEY}, login_as=CLASSROOM_KEY,
                                       teacher_login_as=TeacherGrant(42, "陳老師"),
                                       teachers={42: teacher_contract("accepted")}))


def sign_in_student(site) -> None:
    assert site.get("/auth/student/login").url.path == "/brief"


def sign_in_teacher(site) -> None:
    assert site.get("/admin").url.path == "/admin"


def teacher_with_classroom_key(site, key: str) -> dict:
    """老師 cookie 加上一把課堂金鑰：老師用學生身分示範時的瀏覽器。"""
    return {"Cookie": f"road_test_teacher={site.cookies['road_test_teacher']}; road_test_classroom={key}"}


def test_signed_out_the_header_has_no_identity(site) -> None:
    response = site.get("/api/me")

    assert response.status_code == 200
    assert response.json() == {"role": None}


def test_a_student_without_a_group_is_shown_by_display_name(site) -> None:
    sign_in_student(site)

    assert site.get("/api/me").json() == {"role": "student", "display_name": "小明", "group_name": None}


def test_a_student_in_a_group_sees_the_group_name(site) -> None:
    sign_in_student(site)
    group = site.app.state.store.add_class_group(56, "第 3 組", 1)
    assert site.post("/api/student/group", json={"group_id": group["id"]}).status_code == 201

    assert site.get("/api/me").json() == {"role": "student", "display_name": "小明", "group_name": "第 3 組"}


def test_a_teacher_is_shown_by_display_name(site) -> None:
    sign_in_teacher(site)

    assert site.get("/api/me").json() == {"role": "teacher", "display_name": "陳老師"}


def test_a_teacher_demonstrating_as_a_student_is_still_shown_as_the_teacher(site) -> None:
    sign_in_teacher(site)
    sign_in_student(site)

    assert site.get("/api/me").json()["role"] == "teacher"


def test_who_is_signed_in_is_never_cached_and_never_contains_the_key(site) -> None:
    sign_in_student(site)

    response = site.get("/api/me")

    assert response.headers["cache-control"] == "no-store"
    assert CLASSROOM_KEY not in response.text


def test_when_the_router_is_down_the_header_is_empty_and_the_cookie_stays(site) -> None:
    response = site.get("/api/me", headers=as_student(DOWN_KEY))

    assert response.status_code == 200 and response.json() == {"role": None}
    assert "set-cookie" not in response.headers


def test_a_signed_in_teacher_sees_the_way_into_the_back_office_on_the_home_page(site) -> None:
    sign_in_teacher(site)

    page = site.get("/").text

    assert "進入後台" in page and 'href="/admin"' in page
    assert "進入路考" not in page and "/auth/student/login" not in page


def test_a_signed_in_student_has_no_teacher_entry_on_the_home_page(site) -> None:
    sign_in_student(site)

    page = site.get("/").text

    assert "進入路考" in page
    assert "進入後台" not in page and "/admin" not in page


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_the_shared_header(site, path: str) -> None:
    sign_in_teacher(site)

    page = site.get(path).text

    assert re.search(r'<header class="site-header">.*?<a class="brand" href="/">.*?<img[^>]+src="/logo\.svg"'
                     r'.*?路考網站.*?id="site-actions"', page, re.S), f"{path} 要有共用標題列"
    assert page.index('src="common.js"') < page.index('src="header.js"'), f"{path} 要先載入 common.js"
    assert not re.search(r"<script(?![^>]*\bsrc=)", page), "CSP 不准 inline script"


def test_the_logo_is_served(site) -> None:
    response = site.get("/logo.svg")

    assert response.status_code == 200 and "svg" in response.headers["content-type"]


def test_a_teacher_on_the_hand_in_page_is_told_only_students_hand_in(site) -> None:
    sign_in_teacher(site)

    response = site.get("/api/student")

    assert response.status_code == 403
    assert response.headers["X-Students-Only"] == "1"
    assert "X-Login-Notice" not in response.headers
    assert "只有學生" in response.json()["detail"]


def test_a_teacher_whose_demo_login_expired_is_told_the_same_and_the_dead_key_is_cleared(site) -> None:
    sign_in_teacher(site)

    response = site.get("/api/student", headers=teacher_with_classroom_key(site, "vcr_cls_expired"))

    assert response.status_code == 403 and response.headers["X-Students-Only"] == "1"
    assert "road_test_classroom" in response.headers["set-cookie"]


def test_a_teacher_demonstrating_as_a_student_can_still_use_the_hand_in_page(site) -> None:
    sign_in_teacher(site)
    sign_in_student(site)

    assert site.get("/api/student").status_code == 200


def test_when_the_router_is_down_a_teacher_with_a_demo_login_sees_the_outage(site) -> None:
    sign_in_teacher(site)

    response = site.get("/api/student", headers=teacher_with_classroom_key(site, DOWN_KEY))

    assert response.status_code == 503 and "X-Students-Only" not in response.headers


def test_signed_out_the_hand_in_page_still_sends_people_home(site) -> None:
    response = site.get("/api/student")

    assert response.status_code == 401 and response.headers["X-Login-Notice"] == "login_required"
    assert "X-Students-Only" not in response.headers


def test_the_hand_in_page_itself_has_no_teacher_entry(site) -> None:
    sign_in_student(site)

    assert "/admin" not in site.get("/submit.html").text


def test_the_brief_tells_students_about_login_and_not_about_pasting_a_key(site) -> None:
    sign_in_student(site)

    page = site.get("/brief").text

    assert "API Key" not in page and "貼上" not in page and "填顯示名稱" not in page
    assert "課堂金鑰" not in page, "學生不必看到或貼上課堂金鑰"
