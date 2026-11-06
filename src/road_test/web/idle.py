"""閒置自動關機：沒有人連進來、也沒有路考在跑，過了閒置時間就讓網站結束。

Fly 的 auto_stop 只看有沒有連線：學生關掉結果頁後，背景還在補幀也會被關機。所以 fly.toml 關掉
auto_stop，改由網站自己判斷：IdleExit 包在 FastAPI 外面記錄最後一次 HTTP 請求，watch() 每隔一段時間
檢查，真的沒事了就叫 uvicorn 收尾（main.py 設 server.should_exit；不能用 SIGTERM：uvicorn 收尾後會把
訊號再丟一次，Fly 當成當掉又重開）。程式以 0 結束，restart 政策是 on-failure，
Fly 就讓機器停著。有人再連進來時 Fly proxy 的 auto_start 會把機器開回來。
"""
import threading
import time
from collections.abc import Callable

HEALTH = "/healthz"  # fly.toml 的健康檢查路徑


class IdleExit:
    def __init__(self, app, busy: Callable[[], bool], minutes: float, clock: Callable[[], float] = time.monotonic):
        self.app = app
        self.busy = busy
        self.seconds = minutes * 60
        self.clock = clock
        self.last = clock()
        self.open = 0  # 還沒結束的請求（例如正在上傳的模型）

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if scope["path"] == HEALTH:  # Fly 每 30 秒的健康檢查不算有人在用
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            return await send({"type": "http.response.body", "body": b"ok"})
        self.open += 1
        self.last = self.clock()
        try:
            await self.app(scope, receive, send)
        finally:
            self.open -= 1
            self.last = self.clock()

    def idle(self) -> bool:
        return not self.open and self.clock() - self.last >= self.seconds and not self.busy()

    def watch(self, on_idle: Callable[[], None],
              every: float = 30) -> threading.Thread:
        def loop():
            while True:
                time.sleep(every)
                if self.idle():
                    on_idle()
                    return
        t = threading.Thread(target=loop, name="road-test-idle", daemon=True)
        t.start()
        return t
