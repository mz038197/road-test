"""路考網站的 HTTP API（測試切入點 1）：假偵測器＋只有 3 個計分幀的小路考。"""
import pytest

from conftest import CAR, GHOST, PED, as_group, model, run_queue, submit


def test_wrong_group_code_is_rejected(client) -> None:
    response = submit(client, group="01", code="banana")

    assert response.status_code == 403, "組別代碼錯誤要退件"
    assert "組別代碼" in response.json()["detail"], "退件原因要說是組別代碼不對"


def test_after_the_queue_runs_the_group_gets_its_score_and_examiner_log(client) -> None:
    # 行人第 2 幀就是必看，第 5 幀才框到（慢 1 秒，扣 5 的一半）；車一出現就框到
    late = model({5: [("pedestrian", PED, 0.9)], 8: [("car", CAR, 0.8)]})

    response = submit(client, group="01", content=late)
    assert response.status_code == 201, f"合格的交件要收下：{response.text}"
    run_queue(client)
    result = client.get(f"/api/submissions/{response.json()['id']}", headers=as_group("01")).json()

    assert result["status"] == "done", "偵測完計分幀、補完其餘的幀之後，狀態是 done"
    assert result["result"]["score"] == 64, "扣 2.5 分、扣滿 7 分：成績 = round(100 × (1 − 2.5/7)) = 64"
    assert result["result"]["log"] == [
        {"frame": 1, "time": 1, "cls": "pedestrian", "kind": "漏看", "reaction": 1, "penalty": 2.5,
         "video_frame": 5, "video_time": 5 / 3},
    ], "考官紀錄記在第 2 個計分幀（影片第 5 幀，第 5/3 秒）"


@pytest.mark.parametrize(
    ("content", "reason"),
    [(b"\x08\x07 not a model", "讀不進來"), (model(names=["person", "bicycle", "car"]), "類別名稱")],
    ids=["讀不進來", "COCO 類別"],
)
def test_unfit_model_is_rejected_without_leaving_a_record(client, content: bytes, reason: str) -> None:
    response = submit(client, group="02", content=content)

    assert response.status_code == 422, "模型不合格要退件"
    assert reason in response.json()["detail"], f"退件原因要說明「{reason}」"
    assert client.get("/api/groups/02/records", headers=as_group("02")).json() == [], "退件不算一次路考"
    assert submit(client, group="02").status_code == 201, "退件之後可以馬上重交"


def test_each_group_has_at_most_one_submission_in_the_queue(client) -> None:
    first = submit(client, group="01")
    second = submit(client, group="01")
    other = submit(client, group="02")

    assert first.json()["position"] == 1, "第一件排第 1 位"
    assert second.status_code == 409, "同一組已經有一件在排隊，不能再交"
    assert "已經有一件" in second.json()["detail"]
    assert other.status_code == 201 and other.json()["position"] == 2, "別組照樣可以交，排在後面"

    run_queue(client)

    assert submit(client, group="01").status_code == 201, "跑完之後可以再交"


def test_groups_see_only_their_own_records(client) -> None:
    mine = submit(client, group="01").json()["id"]
    run_queue(client)
    theirs = submit(client, group="02").json()["id"]
    run_queue(client)

    records = client.get("/api/groups/01/records", headers=as_group("01")).json()

    assert [r["id"] for r in records] == [mine], "路考紀錄只列自己這組"
    assert records[0]["kind_name"] == "模擬路考" and records[0]["score"] is not None
    assert client.get("/api/groups/02/records", headers=as_group("01")).status_code == 403, "不能看別組的路考紀錄"
    assert client.get(f"/api/submissions/{theirs}", headers=as_group("01")).status_code == 404, "不能看別組的結果"
    wrong = {"X-Group": "01", "X-Group-Code": "banana"}
    assert client.get("/api/groups/01/records", headers=wrong).status_code == 403, "組別代碼不對就不能看"


def test_score_comes_first_then_every_frame_is_filled_in(client) -> None:
    sid = submit(client, content=model({2: [("pedestrian", PED, 0.9)], 3: [("car", GHOST, 0.07)]})).json()["id"]

    client.app.state.worker.run_once()  # 只做第一段：偵測計分幀、算成績
    early = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()

    assert early["status"] == "filling", "計分幀偵測完就先給成績，其餘的幀還在補"
    assert early["result"] is not None and early["progress"] == {"done": 3, "total": 10}, "先做完 3 個計分幀"

    run_queue(client)
    detections = client.get(f"/api/submissions/{sid}/detections", headers=as_group("01")).json()

    assert client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()["status"] == "done"
    assert detections["scoring_frames"] == [2, 5, 8]
    assert len(detections["frames"]) == 10 and all(f is not None for f in detections["frames"]), "每一幀都補完"
    assert detections["frames"][2] == [[*PED, 0.9, detections["classes"].index("pedestrian")]], "計分幀的框存下來"
    assert detections["frames"][3] == [[*GHOST, 0.07, detections["classes"].index("car")]], "低於 CONF 的框也要存（門檻 0.05）"


def test_a_new_submission_is_scored_before_earlier_frames_are_filled_in(open_site) -> None:
    client = open_site(fill_chunk=2)
    first = submit(client, group="01").json()["id"]
    client.app.state.worker.run_once()  # 第一件的成績出來了，還有 7 幀要補
    second = submit(client, group="02").json()["id"]

    client.app.state.worker.run_once()

    assert client.get(f"/api/submissions/{second}", headers=as_group("02")).json()["result"] is not None, \
        "新交件的計分幀要先做，不用等前一件補完全部的幀"
    assert client.get(f"/api/submissions/{first}", headers=as_group("01")).json()["status"] == "filling"


def test_submissions_and_results_survive_a_restart(open_site) -> None:
    before = open_site()
    scored = submit(before, group="01").json()["id"]
    run_queue(before)
    waiting = submit(before, group="02").json()["id"]

    after = open_site()  # 重開機：同一個路考資料夾
    run_queue(after)

    assert [r["id"] for r in after.get("/api/groups/01/records", headers=as_group("01")).json()] == [scored], \
        "重開之後路考紀錄還在"
    assert after.get(f"/api/submissions/{waiting}", headers=as_group("02")).json()["status"] == "done", \
        "重開之前還在排隊的，重開之後照樣跑完"


def test_a_model_that_fails_while_detecting_does_not_block_the_queue(client) -> None:
    broken = submit(client, group="01", content=model(crash=True)).json()["id"]
    fine = submit(client, group="02").json()["id"]

    run_queue(client)
    failed = client.get(f"/api/submissions/{broken}", headers=as_group("01")).json()

    assert failed["status"] == "failed" and "出錯" in failed["error"], "偵測時出錯要記下原因"
    assert client.get(f"/api/submissions/{fine}", headers=as_group("02")).json()["status"] == "done", "別組照樣跑完"
    assert submit(client, group="01").status_code == 201, "出錯之後這組可以重交"


def test_the_site_serves_its_pages_without_a_build_step(client) -> None:
    for page in ("/", "/result.html", "/teacher.html", "/common.js"):
        response = client.get(page)
        assert response.status_code == 200, f"{page} 要由後端直接提供"
    assert "text/html" in client.get("/").headers["content-type"]
    assert client.get("/api/groups").json() == ["01", "02", "03"], "交件頁的組別選單，不含代碼"
