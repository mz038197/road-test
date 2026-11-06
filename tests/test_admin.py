"""管理頁：老師看任一組、組別代碼、還正式路考次數、所有路考紀錄、清空（HTTP API）。"""
import re

import pytest

from conftest import as_group, as_teacher, run_queue, set_final, submit


def test_the_teacher_can_open_any_groups_result(client, data_dir) -> None:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mini video")
    sid = submit(client, group="01").json()["id"]
    run_queue(client)

    seen = client.get(f"/api/submissions/{sid}", headers=as_teacher())
    boxes = client.get(f"/api/submissions/{sid}/detections", headers=as_teacher())

    assert seen.status_code == 200, "老師帶老師密碼就看得到任一組的結果"
    assert seen.json()["result"]["score"] is not None
    assert boxes.status_code == 200, "老師也拿得到播放用的偵測框"
    assert client.get(seen.json()["video_url"]).status_code == 200, "老師拿到的影片網址播得了"
    assert client.get(f"/api/submissions/{sid}", headers=as_teacher("wrong")).status_code == 403, \
        "老師密碼錯誤不能看"


def test_the_teacher_sees_every_groups_records(client) -> None:
    first = submit(client, group="01").json()["id"]
    second = submit(client, group="02").json()["id"]

    records = client.get("/api/teacher/records", headers=as_teacher()).json()

    assert [r["id"] for r in records] == [second, first], "所有組的路考紀錄，新的在前面"
    assert [r["group"] for r in records] == ["02", "01"]
    assert records[0]["kind_name"] == "模擬路考" and records[0]["status"] == "queued"


def test_after_resetting_a_code_the_old_code_is_refused_and_the_new_one_works(open_site) -> None:
    client = open_site()
    new = client.post("/api/teacher/groups/01/code", headers=as_teacher()).json()["code"]

    assert new != "apple"
    assert submit(client, group="01", code="apple").status_code == 403, "舊代碼退件"
    assert submit(client, group="01", code=new).status_code == 201, "新代碼可以交"
    assert submit(client, group="02").status_code == 201, "別組的代碼不受影響"
    codes = {g["group"]: g["code"] for g in open_site().get("/api/teacher/groups", headers=as_teacher()).json()}
    assert codes == {"01": new, "02": "banana", "03": "cherry"}, "重開機之後還是新代碼"


def test_the_teacher_sets_the_number_of_groups_and_new_groups_get_readable_codes(client) -> None:
    table = client.put("/api/teacher/groups", json={"count": 4}, headers=as_teacher()).json()

    assert [g["group"] for g in table] == ["01", "02", "03", "04"]
    assert [g["code"] for g in table[:3]] == ["apple", "banana", "cherry"], "原本的組保留代碼"
    code = table[3]["code"]
    assert re.fullmatch(r"[a-z]{4}-[a-z]{4}", code), f"新代碼要好唸好抄，例如 bako-mite：{code}"
    assert client.get("/api/groups").json() == ["01", "02", "03", "04"], "交件頁的組別選單跟著變"
    copied = code.upper().replace("-", " ")
    assert submit(client, group="04", code=copied).status_code == 201, "抄成大寫、空白代替連字號也收"

    smaller = client.put("/api/teacher/groups", json={"count": 2}, headers=as_teacher()).json()
    assert [g["group"] for g in smaller] == ["01", "02"], "組數變少，多出來的組刪掉"


def test_resetting_all_codes_gives_every_group_a_new_one(client) -> None:
    table = client.post("/api/teacher/codes", headers=as_teacher()).json()

    assert [g["group"] for g in table] == ["01", "02", "03"]
    assert not {"apple", "banana", "cherry"} & {g["code"] for g in table}, "每組都換了新代碼"
    assert submit(client, group="02", code="banana").status_code == 403


def test_after_the_teacher_returns_a_try_the_group_can_take_the_final_again(client) -> None:
    set_final(client, True)
    submit(client, group="01", kind="final")
    run_queue(client)
    assert submit(client, group="01", kind="final").status_code == 409, "還沒還次數前不能再交"

    returned = client.post("/api/teacher/groups/01/final-returns", headers=as_teacher())

    assert returned.status_code == 200
    assert returned.json()["finals_left"] == 1, "還了一次"
    assert submit(client, group="01", kind="final").status_code == 201, "還給次數後可以再交一次正式路考"
    run_queue(client)
    assert submit(client, group="01", kind="final").status_code == 409, "只多一次"
    assert client.post("/api/teacher/groups/99/final-returns", headers=as_teacher()).status_code == 404, \
        "沒有這組不能還"


def test_clearing_empties_the_records_but_keeps_codes_and_the_switch(client, data_dir) -> None:
    submit(client, group="01")
    run_queue(client)
    set_final(client, True)
    submit(client, group="02", kind="final")
    client.post("/api/teacher/groups/03/final-returns", headers=as_teacher())

    cleared = client.post("/api/teacher/clear", json={"confirm": "清空"}, headers=as_teacher())

    assert cleared.status_code == 200
    assert client.get("/api/teacher/records", headers=as_teacher()).json() == [], "清空之後路考紀錄是空的"
    assert client.get("/api/groups/01/records", headers=as_group("01")).json() == []
    assert not list((data_dir / "submissions").glob("*.onnx")), "交上來的模型檔也刪掉"
    table = client.get("/api/teacher/groups", headers=as_teacher()).json()
    assert [g["code"] for g in table] == ["apple", "banana", "cherry"], "組別代碼不清"
    assert [g["finals_left"] for g in table] == [1, 1, 1], "正式路考次數回到每組一次（還的次數也清掉）"
    assert client.get("/api/road-tests").json()["final_open"] is True, "正式路考開關不動"
    run_queue(client)  # 清空前排隊中的那件不見了，worker 不能卡住
    assert submit(client, group="02", kind="final").status_code == 201, "清空後可以重新交"


def test_clearing_needs_a_second_confirmation(client) -> None:
    submit(client, group="01")

    for body in ({}, {"confirm": "好"}):
        response = client.post("/api/teacher/clear", json=body, headers=as_teacher())
        assert response.status_code == 400, f"沒有再確認一次不清空：{body}"
    assert len(client.get("/api/teacher/records", headers=as_teacher()).json()) == 1, "路考紀錄還在"


ADMIN_APIS = [
    ("get", "/api/teacher/records", None),
    ("get", "/api/teacher/groups", None),
    ("put", "/api/teacher/groups", {"count": 5}),
    ("post", "/api/teacher/codes", None),
    ("post", "/api/teacher/groups/01/code", None),
    ("post", "/api/teacher/groups/01/final-returns", None),
    ("post", "/api/teacher/clear", {"confirm": "清空"}),
    ("get", "/api/teacher/final", None),
    ("put", "/api/teacher/final", {"open": True}),
]


@pytest.mark.parametrize("method, path, body", ADMIN_APIS)
def test_without_the_teacher_password_every_admin_api_is_refused(open_site, method, path, body) -> None:
    client = open_site()
    submit(client, group="01")
    for headers in ({}, as_teacher("wrong"), as_group("01")):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, f"{method.upper()} {path} 沒有老師密碼要拒絕：{headers}"
    unset = open_site(teacher_password=None).request(method, path, json=body, headers=as_teacher())
    assert unset.status_code == 403, f"{method.upper()} {path}：伺服器沒設定老師密碼時一律拒絕"

    teacher = client.get("/api/teacher/groups", headers=as_teacher()).json()
    assert [g["code"] for g in teacher] == ["apple", "banana", "cherry"], "被拒絕的請求什麼都沒改"
    assert len(client.get("/api/teacher/records", headers=as_teacher()).json()) == 1
    assert client.get("/api/road-tests").json()["final_open"] is False


def test_the_teacher_header_does_not_open_results_when_the_server_has_no_password(open_site) -> None:
    client = open_site(teacher_password=None)
    sid = submit(client, group="01").json()["id"]

    assert client.get(f"/api/submissions/{sid}", headers=as_teacher("")).status_code == 403
    assert client.get("/api/groups/01/records", headers=as_teacher("anything")).status_code == 403
