"""閒置自動關機：沒有人連進來、也沒有路考在跑，過了閒置時間網站才結束（Fly 就把機器關掉）。

路考在背景補幀時沒有 HTTP 連線，Fly 的 auto_stop 會把機器關掉、補幀中斷，
所以由網站自己判斷「真的沒事了」再結束。
"""
from fastapi.testclient import TestClient

from road_test.web.idle import IdleExit
from conftest import run_queue, submit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wrap(client: TestClient, clock: Clock, minutes: float = 5) -> tuple[IdleExit, TestClient]:
    app = client.app
    guard = IdleExit(app, busy=app.state.store.busy, minutes=minutes, clock=clock)
    return guard, TestClient(guard)


def test_site_is_idle_only_after_the_idle_time_with_no_requests(client: TestClient) -> None:
    clock = Clock()
    guard, web = wrap(client, clock)

    clock.now += 4 * 60
    assert not guard.idle(), "開機 4 分鐘、閒置 5 分鐘才關"
    web.get("/api/groups")
    clock.now += 4 * 60
    assert not guard.idle(), "有人連進來就重新計時"
    clock.now += 61
    assert guard.idle(), "最後一次連線後超過 5 分鐘、沒有路考在跑：可以關機"


def test_site_stays_up_while_a_road_test_is_running(client: TestClient) -> None:
    clock = Clock()
    guard, web = wrap(client, clock)
    assert submit(web).status_code == 201

    clock.now += 60 * 60
    assert not guard.idle(), "還有路考在排隊或補幀，就算沒人連線也不能關"
    run_queue(client)
    assert guard.idle(), "路考跑完、也沒人連線：可以關機"
