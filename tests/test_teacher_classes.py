"""老師用老師 cookie 管理自己班級的組別和正式路考開關（road-test ADR 0003、router ADR 0094）。

能管理的班級來自 teacher-classes，背後是回答 router 合約範例檔的假 router（見 conftest）。
"""
import logging

import pytest

from conftest import ADMIN_KEY, CLASSROOM_KEY, DOWN_KEY, NO_CLASS_KEY, TEACHER_KEY, as_student, bearer, signed_in


def test_without_a_teacher_cookie_the_teacher_api_is_refused(client) -> None:
    response = client.get("/api/teacher/classes")

    assert response.status_code == 401
    assert response.json()["detail"] == "請先登入。"


def test_a_teacher_sees_only_the_classes_the_router_lists_with_their_names(client) -> None:
    response = client.get("/api/teacher/classes", headers=signed_in(TEACHER_KEY))

    assert response.status_code == 200
    assert response.json() == {"is_admin": False, "classes": [{"id": 56, "name": "週一 程式設計"},
                                                              {"id": 57, "name": "週三 進階班"}]}


def test_an_admin_sees_every_class(client) -> None:
    body = client.get("/api/teacher/classes", headers=signed_in(ADMIN_KEY)).json()

    assert body["is_admin"] is True
    assert [c["id"] for c in body["classes"]] == [56, 57, 58]


def test_a_teacher_without_classes_has_nothing_to_manage(client) -> None:
    response = client.get("/api/teacher/classes", headers=signed_in(NO_CLASS_KEY))

    assert response.status_code == 403
    assert response.json()["detail"] == "你沒有可管理的班級"


def test_a_classroom_key_cannot_use_the_teacher_api(client) -> None:
    for method, path, body in [("get", "/api/teacher/classes", None),
                               ("get", "/api/teacher/classes/56", None),
                               ("post", "/api/teacher/classes/56/groups", {"name": "第 1 組"}),
                               ("put", "/api/teacher/classes/56/final", {"open": True})]:
        response = client.request(method, path, json=body, headers=signed_in(CLASSROOM_KEY))
        assert response.status_code == 401, f"{method.upper()} {path}：課堂金鑰不能用老師的功能"
    assert client.get("/api/teacher/classes/56", headers=signed_in(TEACHER_KEY)).json()["groups"] == []


def add_group(client, class_id: int, name: str, key: str = TEACHER_KEY, **fields):
    return client.post(f"/api/teacher/classes/{class_id}/groups", json={"name": name, **fields}, headers=signed_in(key))


def test_a_teacher_creates_groups_for_a_class_and_sets_their_final_tries(open_site) -> None:
    client = open_site()
    first = add_group(client, 56, "第 1 組")
    second = add_group(client, 56, "第 2 組", final_tries=2)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["final_tries"] == 1, "沒填就是一次正式路考"
    changed = client.put(f"/api/teacher/classes/56/groups/{first.json()['id']}", json={"final_tries": 3},
                         headers=signed_in(TEACHER_KEY))
    assert changed.status_code == 200 and changed.json()["final_tries"] == 3

    seen = open_site().get("/api/teacher/classes/56", headers=signed_in(TEACHER_KEY)).json()  # 重開機之後還在
    assert seen["name"] == "週一 程式設計", "畫面顯示班級名稱"
    assert [(g["name"], g["final_tries"]) for g in seen["groups"]] == [("第 1 組", 3), ("第 2 組", 2)]
    assert client.get("/api/teacher/classes/57", headers=signed_in(TEACHER_KEY)).json()["groups"] == [], \
        "組別依班級分開"


def test_the_same_group_name_can_be_used_in_another_class_but_not_twice_in_one(client) -> None:
    add_group(client, 56, "第 1 組")

    assert add_group(client, 56, "第 1 組").status_code == 409, "同一班不能有兩個第 1 組"
    assert add_group(client, 57, "第 1 組").status_code == 201, "不同班級的第 1 組是不同的組別"


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {"name": "第 1 組", "final_tries": 0},
                                  {"name": "第 1 組", "final_tries": 100}])
def test_a_group_needs_a_name_and_a_sensible_number_of_final_tries(client, body) -> None:
    response = client.post("/api/teacher/classes/56/groups", json=body, headers=signed_in(TEACHER_KEY))

    assert response.status_code in (400, 422)
    assert client.get("/api/teacher/classes/56", headers=signed_in(TEACHER_KEY)).json()["groups"] == []


def test_a_teacher_cannot_manage_a_class_the_router_does_not_list(client) -> None:
    gid = add_group(client, 58, "第 1 組", key=ADMIN_KEY).json()["id"]

    for method, path, body in [("get", "/api/teacher/classes/58", None),
                               ("post", "/api/teacher/classes/58/groups", {"name": "第 2 組"}),
                               ("put", f"/api/teacher/classes/58/groups/{gid}", {"final_tries": 5}),
                               ("put", f"/api/teacher/classes/56/groups/{gid}", {"final_tries": 5}),
                               ("put", "/api/teacher/classes/58/final", {"open": True})]:
        response = client.request(method, path, json=body, headers=signed_in(TEACHER_KEY))
        assert response.status_code == 404, f"{method.upper()} {path}：不是自己的班級"
    seen = client.get("/api/teacher/classes/58", headers=signed_in(ADMIN_KEY)).json()
    assert [(g["name"], g["final_tries"]) for g in seen["groups"]] == [("第 1 組", 1)], "被拒絕的請求什麼都沒改"
    assert seen["final_open"] is False


def test_the_final_switch_is_per_class(client) -> None:
    opened = client.put("/api/teacher/classes/56/final", json={"open": True}, headers=signed_in(TEACHER_KEY))

    assert opened.status_code == 200 and opened.json()["final_open"] is True
    assert client.get("/api/teacher/classes/56", headers=signed_in(TEACHER_KEY)).json()["final_open"] is True
    assert client.get("/api/teacher/classes/57", headers=signed_in(TEACHER_KEY)).json()["final_open"] is False, \
        "別的班級不受影響"
    closed = client.put("/api/teacher/classes/56/final", json={"open": False}, headers=signed_in(ADMIN_KEY))
    assert closed.json()["final_open"] is False, "admin 也能開關任一班"


@pytest.mark.parametrize("path", ["/", "/teacher.html", "/api/student", "/api/teacher/classes"])
def test_every_response_allows_only_the_sites_own_scripts(client, path) -> None:
    response = client.get(path, headers=signed_in(TEACHER_KEY))

    policy = response.headers.get("content-security-policy", "")
    assert "script-src 'self'" in policy, f"{path}：不載入第三方 script、不跑 inline script"


def test_the_key_never_shows_up_in_responses_or_logs(client, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    bodies = [client.get("/api/teacher/classes", headers=bearer(key)).text
              for key in (TEACHER_KEY, NO_CLASS_KEY, CLASSROOM_KEY, DOWN_KEY, "vcr_sk_" + "x" * 48)]
    bodies += [client.get("/api/student", headers=as_student(key)).text for key in (CLASSROOM_KEY, DOWN_KEY)]
    bodies.append(add_group(client, 56, "第 1 組").text)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    for key in (TEACHER_KEY, NO_CLASS_KEY, CLASSROOM_KEY, DOWN_KEY):
        assert all(key not in body for body in bodies), "回應不含金鑰"
        assert key not in logged, "記錄不含完整金鑰"
    assert DOWN_KEY[:11] in logged, "router 連不上時的記錄只寫前綴"
    stored = b"".join(p.read_bytes() for p in client.app.state.store.path.parent.rglob("*") if p.is_file())
    assert TEACHER_KEY.encode() not in stored, "路考資料夾裡不存金鑰"


def test_the_site_starts_on_a_fresh_database_and_leaves_the_old_one_alone(data_dir, open_site) -> None:
    import sqlite3
    old = sqlite3.connect(data_dir / "road_test.db")
    old.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, grp TEXT)")
    old.execute("INSERT INTO submissions VALUES (1, '01')")
    old.commit()
    old.close()

    client = open_site()

    assert client.get("/api/teacher/classes/56/records", headers=signed_in(TEACHER_KEY)).json() == [], \
        "舊資料不搬（還沒上過課），用新的結構重新開始"
    assert client.app.state.store.path != data_dir / "road_test.db"
