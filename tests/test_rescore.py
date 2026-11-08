"""模擬路考的 CONF 滑桿：用新的 CONF 重算存下來的框（HTTP API）。"""
from conftest import CAR, PED, as_group, model, records, run_queue, set_final, submit

# 行人只有 0.3 的信心分數：CONF 0.35 時算漏看，CONF 0.2 時算看見；車 0.9 一直都看見。
SHY = model({2: [("pedestrian", PED, 0.3)], 5: [("pedestrian", PED, 0.3)], 8: [("car", CAR, 0.9)]})


def rescore(client, sid, conf, group="01"):
    return client.get(f"/api/submissions/{sid}/rescore", params={"conf": conf}, headers=as_group(group))


def test_a_lower_conf_rescores_the_mock_without_queueing_again(client) -> None:
    sid = submit(client, group="01", content=SHY, conf=0.35).json()["id"]
    run_queue(client)
    before = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()

    response = rescore(client, sid, 0.2)

    assert before["result"]["score"] < 100, "CONF 0.35 時行人沒看見，要扣分"
    assert response.status_code == 200, response.text
    assert response.json()["score"] == 100 and response.json()["log"] == [], "CONF 調到 0.2 行人就看見了，不扣分"
    assert response.json()["conf"] == 0.2, "成績要寫用的是哪個 CONF"
    assert response.json()["frames"][0]["video_frame"] == 2, "重算的成績也有影片幀號，播放器才畫得出來"
    after = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()
    assert after["result"] == before["result"] and after["conf"] == 0.35, "重算只是看看，不改交件的成績"
    assert after["position"] is None and after["status"] == "done", "重算不會排隊"
    assert len(records(client, "01")) == 1, "重算不會多一筆路考紀錄"
    assert submit(client, group="01").status_code == 201, "重算不算這組在跑的路考"


def test_the_final_cannot_be_rescored(client) -> None:
    set_final(client, True)
    sid = submit(client, group="01", kind="final", content=SHY, conf=0.35).json()["id"]
    run_queue(client)

    response = rescore(client, sid, 0.2)

    assert response.status_code == 400, "正式路考的 CONF 鎖定，不能重算"
    assert "鎖定" in response.json()["detail"], "拒絕原因要說 CONF 鎖定了"


def test_rescoring_waits_for_the_score_and_checks_the_conf(client) -> None:
    sid = submit(client, group="01", content=SHY).json()["id"]

    waiting = rescore(client, sid, 0.2)
    run_queue(client)

    assert waiting.status_code == 409, "成績還沒出來不能重算"
    assert "成績還沒出來" in waiting.json()["detail"]
    for conf in (0.01, 1.5):
        response = rescore(client, sid, conf)
        assert response.status_code == 400, f"CONF {conf} 超出範圍要拒絕"
        assert "0.05 到 1" in response.json()["detail"]


def test_other_groups_cannot_rescore(client) -> None:
    sid = submit(client, group="01", content=SHY).json()["id"]
    run_queue(client)

    assert rescore(client, sid, 0.2, group="02").status_code == 404, "別組看不到也不能重算"
