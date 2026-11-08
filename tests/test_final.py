"""正式路考：老師替班級開關、每組的次數、CONF 鎖定、優先執行（HTTP API）。

交件的是 56 班 01、02、03 組的學生（conftest 的 submit），老師用個人金鑰開關（set_final）。
"""
from conftest import as_group, records, run_queue, set_final, submit


def test_a_student_cannot_open_the_final(client) -> None:
    submit(client, group="01")
    response = client.put("/api/teacher/classes/56/final", json={"open": True}, headers=as_group("01"))

    assert response.status_code == 403, "課堂金鑰不能開關正式路考"
    assert client.get("/api/student", headers=as_group("01")).json()["final_open"] is False


def test_the_final_is_refused_until_the_teacher_opens_it(client) -> None:
    closed = submit(client, group="01", kind="final")
    assert closed.status_code == 400, "正式路考沒開放時不收件"
    assert "正式路考" in closed.json()["detail"] and "沒有開放" in closed.json()["detail"]

    assert set_final(client, True).json()["final_open"] is True, "老師帶個人金鑰就能開放"
    assert submit(client, group="01", kind="final").status_code == 201, "開放後收件"


def test_the_switch_survives_a_restart(open_site) -> None:
    set_final(open_site(), True)

    after = open_site()

    assert after.get("/api/student", headers=as_group("01")).json()["final_open"] is True, "重開機之後正式路考還是開著"


def test_while_the_final_is_open_mock_submissions_are_refused(client) -> None:
    set_final(client, True)

    response = submit(client, group="01", kind="mock")

    assert response.status_code == 400, "正式路考開放期間，模擬路考不收件"
    assert "正式路考開放期間" in response.json()["detail"], "退件原因要說是正式路考開放中"
    set_final(client, False)
    assert submit(client, group="01", kind="mock").status_code == 201, "正式路考關閉後，模擬路考恢復收件"


def test_the_final_needs_the_no_resubmission_confirmation(client) -> None:
    set_final(client, True)

    response = submit(client, group="01", kind="final", confirm=False)

    assert response.status_code == 400, "沒確認「交了就不能重交」就不收"
    assert "不能重交" in response.json()["detail"]
    assert records(client, "01") == [], "沒確認不算一次"


def test_each_group_takes_the_final_only_once(client) -> None:
    set_final(client, True)
    first = submit(client, group="01", kind="final")
    run_queue(client)

    again = submit(client, group="01", kind="final")

    assert first.status_code == 201
    assert again.status_code == 409, "正式路考每組只能交一次"
    assert "次數用完" in again.json()["detail"]
    assert submit(client, group="02", kind="final").status_code == 201, "別組不受影響"


def test_a_rejected_model_does_not_use_up_the_final(client) -> None:
    set_final(client, True)

    rejected = submit(client, group="01", kind="final", content=b"\x08 not a model")

    assert rejected.status_code == 422
    assert submit(client, group="01", kind="final").status_code == 201, "模型不合格的退件不算次數"


def test_while_open_the_final_runs_before_earlier_mock_submissions(client) -> None:
    mock = submit(client, group="01").json()["id"]
    set_final(client, True)
    final = submit(client, group="02", kind="final").json()

    assert final["position"] == 1, "正式路考排在先交的模擬路考前面"
    client.app.state.worker.run_once()

    assert client.get(f"/api/submissions/{final['id']}", headers=as_group("02")).json()["result"] is not None, \
        "worker 先做正式路考"
    assert client.get(f"/api/submissions/{mock}", headers=as_group("01")).json()["status"] == "queued"


def test_a_final_uploaded_before_closing_still_gets_its_score(client) -> None:
    set_final(client, True)
    sid = submit(client, group="01", kind="final").json()["id"]
    set_final(client, False)

    late = submit(client, group="02", kind="final")
    run_queue(client)

    assert late.status_code == 400, "關閉之後交的正式路考退件"
    result = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()
    assert result["status"] == "done" and result["result"]["score"] is not None, "關閉前收下的照樣跑完、有成績"


def test_the_final_does_not_wait_for_the_groups_mock_to_finish(client) -> None:
    submit(client, group="01")
    set_final(client, True)

    assert submit(client, group="01", kind="final").status_code == 201, \
        "正式路考開放時，這組的模擬路考還在跑也可以交正式路考"


def test_the_final_conf_is_locked_and_shown_with_the_result(client) -> None:
    set_final(client, True)
    sid = submit(client, group="01", kind="final", conf=0.5).json()["id"]
    run_queue(client)

    result = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()

    assert result["conf"] == 0.5 and result["result"]["conf"] == 0.5, "成績用交件時選的 CONF"
    assert result["conf_locked"] is True, "正式路考的 CONF 鎖定"
    set_final(client, False)
    mock = submit(client, group="02").json()["id"]
    assert client.get(f"/api/submissions/{mock}", headers=as_group("02")).json()["conf_locked"] is False, \
        "模擬路考的 CONF 沒有鎖定（#8 可以拖滑桿重算）"


def test_records_mark_mock_and_final_apart(client) -> None:
    mock = submit(client, group="01").json()["id"]
    run_queue(client)
    set_final(client, True)
    final = submit(client, group="01", kind="final").json()["id"]

    seen = {r["id"]: r for r in records(client, "01")}

    assert (seen[mock]["kind"], seen[mock]["kind_name"]) == ("mock", "模擬路考")
    assert (seen[final]["kind"], seen[final]["kind_name"]) == ("final", "正式路考")

