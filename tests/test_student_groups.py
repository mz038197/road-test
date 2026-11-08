"""學生用課堂金鑰選組、交件、看自己這組的紀錄（road-test ADR 0001、router ADR 0080）。

身分來自注入的 Key Check 用戶端，背後是回答 router 合約範例檔的假 router（見 conftest）：
CLASSROOM_KEY 是 56 班的學生 1234。
"""
from conftest import CLASSMATE_KEY, CLASSROOM_KEY, NO_CLASS_KEY, OTHER_CLASS_KEY, TEACHER_KEY, bearer, model, run_queue


def add_group(client, class_id: int, name: str, **fields) -> int:
    response = client.post(f"/api/teacher/classes/{class_id}/groups", json={"name": name, **fields},
                           headers=bearer(TEACHER_KEY))
    assert response.status_code == 201
    return response.json()["id"]


def test_a_student_whose_class_has_no_groups_is_told_the_road_test_is_not_open(client) -> None:
    response = client.get("/api/student", headers=bearer(CLASSROOM_KEY))

    assert response.status_code == 200
    body = response.json()
    assert body["groups"] == [] and body["member"] is None
    assert body["message"] == "這一班還沒開放路考"


def join(client, group_id: int, display_name: str = "小明", key: str = CLASSROOM_KEY):
    return client.post("/api/student/group", json={"group_id": group_id, "display_name": display_name},
                       headers=bearer(key))


def hand_in(client, key: str = CLASSROOM_KEY, kind: str = "mock", conf: float = 0.35):
    """用課堂金鑰交件（正式路考預設勾了確認）。"""
    data = {"kind": kind, "conf": str(conf), **({"confirm": "true"} if kind == "final" else {})}
    return client.post("/api/submissions", data=data, files={"model": ("best.onnx", model(), "application/octet-stream")},
                       headers=bearer(key))


def records(client, key: str = CLASSROOM_KEY) -> list[dict]:
    response = client.get("/api/student/records", headers=bearer(key))
    assert response.status_code == 200
    return response.json()


def test_a_student_picks_a_group_of_their_class_with_a_display_name_and_is_locked_in(open_site) -> None:
    client = open_site()
    first = add_group(client, 56, "第 1 組")
    second = add_group(client, 56, "第 2 組")

    seen = client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()
    assert [g["name"] for g in seen["groups"]] == ["第 1 組", "第 2 組"] and seen["message"] == ""
    joined = join(client, first, "  小明  ")
    assert joined.status_code == 201
    assert joined.json() == {"group_id": first, "group_name": "第 1 組", "display_name": "小明"}

    again = join(client, second, "小明")
    assert again.status_code == 409, "選好之後就鎖定，不能自己換組"
    member = open_site().get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["member"]  # 重開機之後還在
    assert member == {"group_id": first, "group_name": "第 1 組", "display_name": "小明"}


def test_a_student_cannot_pick_a_group_of_another_class(client) -> None:
    other = add_group(client, 57, "第 1 組")

    response = join(client, other)

    assert response.status_code == 404
    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["member"] is None


def test_a_student_must_give_a_display_name(client) -> None:
    group = add_group(client, 56, "第 1 組")

    assert join(client, group, "   ").status_code == 400


def test_a_student_must_pick_a_group_before_handing_in(client) -> None:
    add_group(client, 56, "第 1 組")

    response = hand_in(client)

    assert response.status_code == 409
    assert "選組" in response.json()["detail"]


def test_a_hand_in_records_the_class_the_group_and_the_student(client) -> None:
    group = add_group(client, 56, "第 1 組")
    join(client, group, "小明")
    join(client, group, "小華", key=CLASSMATE_KEY)

    sid = hand_in(client).json()["id"]
    run_queue(client)

    [record] = records(client, CLASSMATE_KEY)  # 同組的組員也看得到
    assert record["id"] == sid
    assert (record["class_id"], record["group_id"], record["group"], record["student_id"]) == (56, group, "第 1 組", 1234)
    assert record["status"] == "done" and record["score"] is not None


def open_final(client, class_id: int, is_open: bool = True) -> None:
    response = client.put(f"/api/teacher/classes/{class_id}/final", json={"open": is_open}, headers=bearer(TEACHER_KEY))
    assert response.status_code == 200


def test_the_final_switch_belongs_to_each_class(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    join(client, add_group(client, 57, "第 1 組"), key=OTHER_CLASS_KEY)
    open_final(client, 56)

    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["final_open"] is True
    assert hand_in(client, kind="mock").status_code == 400, "56 班開放正式路考時模擬路考暫停"
    assert hand_in(client, kind="final").status_code == 201
    assert hand_in(client, OTHER_CLASS_KEY, kind="final").status_code == 400, "57 班還沒開放"
    assert hand_in(client, OTHER_CLASS_KEY, kind="mock").status_code == 201


def test_final_tries_are_counted_per_group(client) -> None:
    group = add_group(client, 56, "第 1 組")
    join(client, group, "小明")
    join(client, group, "小華", key=CLASSMATE_KEY)
    open_final(client, 56)

    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["finals_left"] == 1
    assert hand_in(client, kind="final").status_code == 201
    run_queue(client)
    refused = hand_in(client, CLASSMATE_KEY, kind="final")
    assert refused.status_code == 409, "同組的組員共用正式路考次數"
    client.put(f"/api/teacher/classes/56/groups/{group}", json={"final_tries": 2}, headers=bearer(TEACHER_KEY))
    assert client.get("/api/student", headers=bearer(CLASSMATE_KEY)).json()["finals_left"] == 1
    assert hand_in(client, CLASSMATE_KEY, kind="final").status_code == 201


def test_a_student_sees_only_their_own_groups_records_and_results(client) -> None:
    mine, theirs = add_group(client, 56, "第 1 組"), add_group(client, 56, "第 2 組")
    join(client, mine)
    join(client, theirs, "小華", key=CLASSMATE_KEY)
    ours = hand_in(client).json()["id"]
    other = hand_in(client, CLASSMATE_KEY).json()["id"]
    run_queue(client)

    assert [r["id"] for r in records(client)] == [ours]
    assert [r["id"] for r in records(client, CLASSMATE_KEY)] == [other]
    for path in (f"/api/submissions/{other}", f"/api/submissions/{other}/detections",
                 f"/api/submissions/{other}/rescore?conf=0.5"):
        assert client.get(path, headers=bearer(CLASSROOM_KEY)).status_code == 404, path
    assert client.get(f"/api/submissions/{ours}", headers=bearer(CLASSROOM_KEY)).json()["result"] is not None
    assert client.get(f"/api/submissions/{ours}", headers=bearer(OTHER_CLASS_KEY)).status_code == 404, "別班也看不到"
    assert client.get(f"/api/submissions/{ours}").status_code in (401, 403), "沒帶金鑰看不到"


def test_the_signed_video_url_goes_only_to_those_who_can_see_the_result(client, data_dir) -> None:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mp4")
    join(client, add_group(client, 56, "第 1 組"))
    join(client, add_group(client, 56, "第 2 組"), "小華", key=CLASSMATE_KEY)
    sid = hand_in(client).json()["id"]
    run_queue(client)

    url = client.get(f"/api/submissions/{sid}", headers=bearer(CLASSROOM_KEY)).json()["video_url"]
    assert client.get(url).status_code == 200, "<video> 不能帶標頭，簽章網址本身就能播"
    refused = client.get(f"/api/submissions/{sid}", headers=bearer(CLASSMATE_KEY))
    assert refused.status_code == 404 and "video_url" not in refused.json()
    assert client.get(f"/api/submissions/{sid}/video").status_code == 403, "沒有簽章不能播"


def test_a_refused_key_shows_the_routers_message(client) -> None:
    for path in ("/api/student", "/api/student/records"):
        response = client.get(path, headers=bearer("vcr_sk_" + "x" * 48))
        assert response.status_code == 401
        assert response.json()["detail"] == "無效的 API 金鑰", "直接顯示 router 給的訊息"
    refused = hand_in(client, "vcr_sk_" + "x" * 48)
    assert refused.status_code == 401 and refused.json()["detail"] == "無效的 API 金鑰"


def test_a_personal_key_cannot_join_or_hand_in(client) -> None:
    group = add_group(client, 56, "第 1 組")

    assert client.get("/api/student", headers=bearer(TEACHER_KEY)).status_code == 403
    assert join(client, group, key=TEACHER_KEY).status_code == 403
    assert hand_in(client, TEACHER_KEY).status_code == 403


def test_the_teacher_of_the_class_can_see_its_results(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    sid = hand_in(client).json()["id"]
    run_queue(client)

    seen = client.get(f"/api/submissions/{sid}", headers=bearer(TEACHER_KEY))
    assert seen.status_code == 200 and seen.json()["student_id"] == 1234
    assert client.get(f"/api/submissions/{sid}", headers=bearer(NO_CLASS_KEY)).status_code == 404
