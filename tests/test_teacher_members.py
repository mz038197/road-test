"""老師看每一組的組員和每筆交件是誰交的，能把選錯組的學生改到同一班的另一組（road-test ADR 0001）。

身分來自注入的 Key Check 用戶端，背後是回答 router 合約範例檔的假 router（見 conftest）：
TEACHER_KEY 管 56、57 班；CLASSROOM_KEY、CLASSMATE_KEY 是 56 班的學生 1234、1235；OTHER_CLASS_KEY 是 57 班的學生。
"""
from conftest import (ADMIN_KEY, CLASSMATE_KEY, CLASSROOM_KEY, NO_CLASS_KEY, OTHER_CLASS_KEY, TEACHER_KEY, bearer,
                      run_queue)
from test_student_groups import add_group, hand_in, join, records


def class_view(client, class_id: int = 56, key: str = TEACHER_KEY) -> dict:
    response = client.get(f"/api/teacher/classes/{class_id}", headers=bearer(key))
    assert response.status_code == 200
    return response.json()


def test_the_teacher_sees_the_members_of_each_group_by_display_name(open_site) -> None:
    client = open_site()
    first, second = add_group(client, 56, "第 1 組"), add_group(client, 56, "第 2 組")
    join(client, first, "小明")
    join(client, second, "小華", key=CLASSMATE_KEY)

    groups = class_view(open_site())["groups"]  # 重開機之後還在

    assert [(g["name"], g["members"]) for g in groups] == [
        ("第 1 組", [{"student_id": 1234, "display_name": "小明"}]),
        ("第 2 組", [{"student_id": 1235, "display_name": "小華"}]),
    ]


def class_records(client, class_id: int = 56, key: str = TEACHER_KEY):
    return client.get(f"/api/teacher/classes/{class_id}/records", headers=bearer(key))


def test_the_teacher_sees_which_member_handed_in_each_record_of_the_class(client) -> None:
    first, second = add_group(client, 56, "第 1 組"), add_group(client, 56, "第 2 組")
    join(client, first, "小明")
    join(client, second, "小華", key=CLASSMATE_KEY)
    join(client, add_group(client, 57, "第 1 組"), "別班的", key=OTHER_CLASS_KEY)
    mine = hand_in(client).json()["id"]
    theirs = hand_in(client, CLASSMATE_KEY).json()["id"]
    hand_in(client, OTHER_CLASS_KEY)
    run_queue(client)

    response = class_records(client)

    assert response.status_code == 200
    assert [(r["id"], r["group"], r["student_id"], r["display_name"]) for r in response.json()] == [
        (theirs, "第 2 組", 1235, "小華"), (mine, "第 1 組", 1234, "小明")], "只有這一班，新的在前面"
    assert response.json()[0]["kind_name"] == "模擬路考" and response.json()[0]["score"] is not None


def test_only_the_teacher_of_the_class_sees_its_records(client) -> None:
    join(client, add_group(client, 56, "第 1 組"))
    hand_in(client)

    assert class_records(client, 58).status_code == 404, "不是自己的班級"
    assert class_records(client, key=NO_CLASS_KEY).status_code == 403
    assert class_records(client, key=CLASSROOM_KEY).status_code == 403, "學生用 /api/student/records 看自己這組"
    assert len(class_records(client, key=ADMIN_KEY).json()) == 1, "admin 看得到所有班級"


def move(client, student_id: int, group_id: int, class_id: int = 56, key: str = TEACHER_KEY):
    return client.put(f"/api/teacher/classes/{class_id}/members/{student_id}", json={"group_id": group_id},
                      headers=bearer(key))


def test_the_teacher_moves_a_student_who_picked_the_wrong_group(client) -> None:
    wrong, right = add_group(client, 56, "第 1 組"), add_group(client, 56, "第 2 組")
    join(client, wrong, "小明")
    join(client, right, "小華", key=CLASSMATE_KEY)
    earlier = hand_in(client).json()["id"]
    run_queue(client)

    moved = move(client, 1234, right)

    assert moved.status_code == 200
    assert moved.json() == {"student_id": 1234, "group_id": right, "group_name": "第 2 組", "display_name": "小明"}
    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["member"]["group_id"] == right
    assert [g["members"] for g in class_view(client)["groups"]] == [
        [], [{"student_id": 1234, "display_name": "小明"}, {"student_id": 1235, "display_name": "小華"}]]
    later = hand_in(client).json()["id"]
    run_queue(client)
    assert [r["id"] for r in records(client, CLASSMATE_KEY)] == [later], "之後交的算新的組"
    assert client.get(f"/api/submissions/{earlier}", headers=bearer(CLASSROOM_KEY)).status_code == 404, \
        "之前交的還是原本那組的"


def test_a_student_can_only_be_moved_within_their_class(client) -> None:
    mine = add_group(client, 56, "第 1 組")
    other_class = add_group(client, 57, "第 1 組")
    join(client, mine, "小明")
    join(client, other_class, "別班的", key=OTHER_CLASS_KEY)

    assert move(client, 1234, other_class).status_code == 404, "不能改到別班的組"
    assert move(client, 2001, mine).status_code == 404, "56 班沒有這個學生"
    assert move(client, 1235, mine).status_code == 404, "還沒選組的學生不能改"
    assert move(client, 1234, mine, class_id=58).status_code == 404, "不是自己的班級"
    assert move(client, 1234, mine, key=CLASSROOM_KEY).status_code == 403, "學生不能自己換組"
    assert client.get("/api/student", headers=bearer(CLASSROOM_KEY)).json()["member"]["group_id"] == mine


def test_the_teacher_plays_back_any_groups_result_in_their_class(client, data_dir) -> None:
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "Mini.mp4").write_bytes(b"mini video")
    join(client, add_group(client, 56, "第 1 組"), "小明")
    join(client, add_group(client, 56, "第 2 組"), "小華", key=CLASSMATE_KEY)
    sids = [hand_in(client).json()["id"], hand_in(client, CLASSMATE_KEY).json()["id"]]
    run_queue(client)

    for sid in sids:
        seen = client.get(f"/api/submissions/{sid}", headers=bearer(TEACHER_KEY))
        assert seen.status_code == 200 and seen.json()["result"]["score"] is not None
        assert client.get(f"/api/submissions/{sid}/detections", headers=bearer(TEACHER_KEY)).status_code == 200, \
            "老師也拿得到播放用的偵測框"
        assert client.get(seen.json()["video_url"]).status_code == 200, "老師拿到的影片網址播得了"
        assert client.get(f"/api/submissions/{sid}", headers=bearer(NO_CLASS_KEY)).status_code == 404
