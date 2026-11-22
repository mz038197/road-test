"""學生用登入的 cookie 選組、交件、看結果（road-test ADR 0003）：只看 HTTP、狀態碼和 cookie。"""
import pytest

from road_test.web.router import FakeRouter

from conftest import (CLASSMATE_KEY, CLASSROOM_KEY, DOWN_KEY, TEACHER_KEY, as_student, bearer, contract, model,
                      router_answers, run_queue)
from test_student_groups import add_group, hand_in, join

ENDED_KEY = "vcr_sk_ended0" + "e" * 40
EXPIRED_NOTICE = "這堂課已經結束，或登入已失效，請重新登入"
UNAVAILABLE_NOTICE = "驗證服務暫時無法使用"
STUDENT_APIS = [("get", "/api/student"), ("get", "/api/student/records"), ("post", "/api/student/group"),
                ("post", "/api/submissions"), ("get", "/api/submissions/1"), ("get", "/api/submissions/1/detections"),
                ("get", "/api/submissions/1/rescore?conf=0.5")]


def cleared(response) -> bool:
    return any(h.startswith("road_test_classroom=") and "max-age=0" in h.lower()
               for h in response.headers.get_list("set-cookie"))


def test_a_signed_in_student_is_known_from_the_cookie(client) -> None:
    response = client.get("/api/student", headers=as_student(CLASSROOM_KEY))

    assert response.status_code == 200
    assert (response.json()["class_id"], response.json()["student_id"]) == (56, 1234)


def test_the_authorization_header_no_longer_identifies_a_student(client) -> None:
    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).status_code == 401
    assert client.get("/api/submissions/1", headers=bearer(CLASSROOM_KEY)).status_code == 401
    group = add_group(client, 56, "第 1 組")
    assert client.post("/api/student/group", json={"group_id": group},
                       headers=bearer(CLASSROOM_KEY)).status_code == 401
    assert client.post("/api/submissions", data={"kind": "mock"}, headers=bearer(CLASSROOM_KEY),
                       files={"model": ("best.onnx", model(), "application/octet-stream")}).status_code == 401


@pytest.mark.parametrize("method, path", STUDENT_APIS)
def test_without_a_login_the_student_apis_answer_401(client, method, path) -> None:
    response = client.request(method, path)

    assert response.status_code == 401
    assert response.headers["x-login-notice"] == "login_required", "前端照這個代碼導回首頁"


@pytest.mark.parametrize("method, path", STUDENT_APIS)
def test_a_key_the_router_refuses_is_cleared_and_answers_401(client, method, path) -> None:
    response = client.request(method, path, headers=as_student(ENDED_KEY))

    assert response.status_code == 401 and response.json()["detail"] == EXPIRED_NOTICE
    assert response.headers["x-login-notice"] == "expired"
    assert cleared(response)


@pytest.mark.parametrize("method, path", STUDENT_APIS)
def test_when_the_router_is_down_the_cookie_stays(client, method, path) -> None:
    response = client.request(method, path, headers=as_student(DOWN_KEY))

    assert response.status_code == 503 and UNAVAILABLE_NOTICE in response.json()["detail"]
    assert "set-cookie" not in response.headers and "x-login-notice" not in response.headers


def test_picking_a_group_takes_no_name_and_uses_the_routers_display_name(client) -> None:
    group = add_group(client, 56, "第 1 組")

    joined = client.post("/api/student/group", json={"group_id": group, "display_name": "我亂填的"},
                         headers=as_student(CLASSROOM_KEY))

    assert joined.status_code == 201
    assert joined.json() == {"group_id": group, "group_name": "第 1 組", "display_name": "小明"}


def test_the_display_name_follows_the_router_and_identity_stays_the_student_id(open_site) -> None:
    answers = router_answers()
    client = open_site(router=FakeRouter(answers))
    group = add_group_as_teacher(client)
    join(client, group)

    answers[CLASSROOM_KEY] = {**answers[CLASSROOM_KEY], "display_name": "王小明"}
    me = client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()

    assert me["member"]["display_name"] == "王小明"
    assert me["teammates"] == ["王小明"], "同一個學生 id，只是名字變了，不會多一個組員"
    assert client.post("/api/student/group", json={"group_id": group},
                       headers=as_student(CLASSROOM_KEY)).status_code == 409


def add_group_as_teacher(client) -> int:
    return add_group(client, 56, "第 1 組")


def test_a_router_without_display_names_does_not_erase_the_name_or_block_joining(open_site) -> None:
    answer = {k: v for k, v in contract("classroom-accepted").items() if k != "display_name"}
    answers = {**router_answers(), CLASSROOM_KEY: answer}
    client = open_site(router=FakeRouter(answers))
    group = add_group_as_teacher(client)

    assert join(client, group).json()["display_name"] == "學生 1234"

    answers[CLASSROOM_KEY] = {**answer, "display_name": "小明"}
    assert client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()["member"]["display_name"] == "小明"
    answers[CLASSROOM_KEY] = answer
    assert client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()["member"]["display_name"] == "小明"


def test_the_group_card_lists_every_member_of_my_group_only(client) -> None:
    mine, other = add_group(client, 56, "第 1 組"), add_group(client, 56, "第 2 組")
    join(client, mine)
    join(client, mine, key=CLASSMATE_KEY)

    me = client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()

    assert me["member"]["group_name"] == "第 1 組"
    assert sorted(me["teammates"]) == sorted(["小明", "小華"])
    client.put("/api/teacher/classes/56/members/1235", json={"group_id": other}, headers=bearer(TEACHER_KEY))
    assert client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()["teammates"] == ["小明"]


def test_a_student_who_has_not_picked_a_group_has_no_teammates(client) -> None:
    add_group(client, 56, "第 1 組")

    assert client.get("/api/student", headers=as_student(CLASSROOM_KEY)).json()["teammates"] == []


def test_the_records_show_who_handed_in(client) -> None:
    group = add_group(client, 56, "第 1 組")
    join(client, group)
    join(client, group, key=CLASSMATE_KEY)
    hand_in(client)
    run_queue(client)
    hand_in(client, CLASSMATE_KEY)

    records = client.get("/api/student/records", headers=as_student(CLASSROOM_KEY)).json()

    assert [r["display_name"] for r in records] == ["小華", "小明"]


def test_result_playback_rescore_and_the_signed_video_work_from_the_cookie(client, data_dir) -> None:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mini video")
    join(client, add_group(client, 56, "第 1 組"))
    sid = hand_in(client).json()["id"]
    run_queue(client)
    me = as_student(CLASSROOM_KEY)

    result = client.get(f"/api/submissions/{sid}", headers=me)

    assert result.status_code == 200 and result.json()["result"] is not None
    assert client.get(f"/api/submissions/{sid}/detections", headers=me).status_code == 200
    assert client.get(f"/api/submissions/{sid}/rescore", params={"conf": 0.5}, headers=me).status_code == 200
    video = result.json()["video_url"]
    assert CLASSROOM_KEY not in video, "金鑰不出現在網址"
    client.cookies.clear()
    assert client.get(video).content == b"mini video"


def test_a_leftover_classroom_key_in_the_tab_does_not_get_in_the_way_of_the_cookie(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    sid = hand_in(client).json()["id"]

    response = client.get(f"/api/submissions/{sid}",
                          headers={**as_student(CLASSROOM_KEY), **bearer(CLASSMATE_KEY)})

    assert response.status_code == 200


def test_a_teacher_with_a_personal_key_still_plays_results(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    sid = hand_in(client).json()["id"]

    assert client.get(f"/api/submissions/{sid}", headers=bearer(TEACHER_KEY)).status_code == 200


def test_no_page_or_answer_carries_the_key(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    me = as_student(CLASSROOM_KEY)
    sid = hand_in(client).json()["id"]

    for path in ("/api/student", "/api/student/records", f"/api/submissions/{sid}"):
        assert CLASSROOM_KEY not in client.get(path, headers=me).text


@pytest.mark.parametrize("gone", ['id="api-key"', "換一把金鑰", 'id="display-name"', "貼上"])
def test_the_hand_in_page_no_longer_asks_for_a_key_or_a_name(client, gone: str) -> None:
    assert gone not in client.get("/submit.html").text
    assert gone not in client.get("/submit.js").text
