"""組別代碼、老師密碼不能無限猜：同一個 IP 對同一個目標 1 分鐘內猜錯 10 次，就暫時回 429。

Fly 的 proxy 把真正的來源 IP 放在 Fly-Client-IP 標頭。
"""
from conftest import GROUPS, TEACHER, as_teacher, submit


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def ip(address: str) -> dict:
    return {"Fly-Client-IP": address}


def records(client, group: str, code: str, address: str = "1.2.3.4"):
    return client.get(f"/api/groups/{group}/records", headers={"X-Group": group, "X-Group-Code": code, **ip(address)})


def test_ten_wrong_group_codes_in_a_minute_lock_that_group_for_that_ip(open_site) -> None:
    clock = Clock()
    client = open_site(clock=clock)

    for _ in range(10):
        assert records(client, "01", "wrong").status_code == 403, "猜錯照樣是 403"

    locked = records(client, "01", GROUPS["01"])
    assert locked.status_code == 429, "1 分鐘內猜錯 10 次，連對的代碼也要先擋下來（不然還是能一直猜）"
    assert "分鐘" in locked.json()["detail"], f"要用中文說多久之後再試：{locked.json()}"
    assert records(client, "01", GROUPS["01"], address="5.6.7.8").status_code == 200, "別的 IP 不受影響"
    assert records(client, "02", GROUPS["02"]).status_code == 200, "同一個 IP 看別組不受影響"

    clock.now += 61
    assert records(client, "01", GROUPS["01"]).status_code == 200, "過了 1 分鐘就可以再試"


def test_wrong_codes_on_submission_count_too(open_site) -> None:
    client = open_site(clock=Clock())
    for _ in range(10):
        assert submit(client, group="01", code="wrong").status_code == 403

    assert records(client, "01", GROUPS["01"], address="testclient").status_code == 429, \
        "交件時猜錯也要算次數（沒有 Fly-Client-IP 時看連線的 IP）"


def test_nine_wrong_codes_do_not_lock(open_site) -> None:
    client = open_site(clock=Clock())
    for _ in range(9):
        records(client, "01", "wrong")

    assert records(client, "01", GROUPS["01"]).status_code == 200, "打錯幾次不會被鎖"


def test_ten_wrong_teacher_passwords_in_a_minute_lock_the_teacher_api_for_that_ip(open_site) -> None:
    clock = Clock()
    client = open_site(clock=clock)

    for _ in range(10):
        assert client.get("/api/teacher/groups", headers={**as_teacher("guess"), **ip("1.2.3.4")}).status_code == 403

    assert client.get("/api/teacher/groups", headers={**as_teacher(TEACHER), **ip("1.2.3.4")}).status_code == 429, \
        "老師密碼 1 分鐘內猜錯 10 次，先擋下來"
    assert client.get("/api/teacher/groups", headers={**as_teacher(TEACHER), **ip("5.6.7.8")}).status_code == 200
    clock.now += 61
    assert client.get("/api/teacher/groups", headers={**as_teacher(TEACHER), **ip("1.2.3.4")}).status_code == 200
