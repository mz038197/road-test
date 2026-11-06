"""猜錯次數限制：同一個 IP 對同一個目標（某一組的組別代碼、老師密碼）1 分鐘內猜錯 10 次，就暫時擋下來。

只記在記憶體裡（網站只有一個 process），重開機就歸零。擋下來的期間連猜對也回 429，不然還是能一直猜。
Fly 的 proxy 把真正的來源 IP 放在 Fly-Client-IP 標頭；本機沒有這個標頭就用連線的 IP。
"""
import threading
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request

MAX_FAILURES = 10
WINDOW_SECONDS = 60


def client_ip(request: Request) -> str:
    return request.headers.get("Fly-Client-IP") or (request.client.host if request.client else "")


class FailureLimit:
    def __init__(self, clock: Callable[[], float], max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS):
        self.clock, self.max_failures, self.window = clock, max_failures, window
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str]) -> deque[float]:
        times = self._failures[key]
        while times and times[0] <= self.clock() - self.window:
            times.popleft()
        if not times:
            del self._failures[key]
        return times

    def check(self, request: Request, target: str) -> None:
        """猜錯太多次就丟 429。要在比對代碼、密碼之前呼叫。"""
        with self._lock:
            if len(self._recent((client_ip(request), target))) >= self.max_failures:
                raise HTTPException(429, "猜錯太多次了，請 1 分鐘後再試。")

    def failed(self, request: Request, target: str) -> None:
        with self._lock:
            self._failures[(client_ip(request), target)].append(self.clock())
