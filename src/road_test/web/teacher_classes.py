"""老師能管理哪些班級：用 Service Credential 問 router 的 teacher-classes（router ADR 0094），通過的留 60 秒。

回傳 ManagedClasses（admin 是所有班級）或 Refusal（不是老師，或 router 連不上）。
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from road_test.web.key_check import KEEP_SECONDS, TIMEOUT_SECONDS, UNAVAILABLE, ClassRef, Refusal

log = logging.getLogger(__name__)  # road_test 底下的 ERROR 會送到 vans-signals（signals.py）


@dataclass(frozen=True)
class ManagedClasses:
    is_admin: bool
    classes: tuple[ClassRef, ...]


class TeacherClasses:
    def __init__(self, url: str, credential: str, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, deadline: float = TIMEOUT_SECONDS):
        self._url = url.rstrip("/") + "/internal/teacher-classes"
        self._credential = credential
        self._http = httpx.Client(transport=transport, timeout=deadline)
        self._deadline = deadline
        self._clock = clock
        self._kept: dict[int, tuple[float, ManagedClasses]] = {}  # user id → (到期時間, 班級)
        self._lock = threading.Lock()

    def of(self, user_id: int) -> ManagedClasses | Refusal:
        now = self._clock()
        with self._lock:
            kept = self._kept.get(user_id)
        if kept and kept[0] > now:
            return kept[1]
        answer = self._ask(user_id)
        if isinstance(answer, ManagedClasses):
            with self._lock:
                self._kept = {u: k for u, k in self._kept.items() if k[0] > now}
                self._kept[user_id] = (now + KEEP_SECONDS, answer)
        return answer

    def _post(self, user_id: int) -> httpx.Response:
        """整次 POST 最多等 deadline 秒（httpx 的 timeout 只管單一階段），做法跟 KeyCheck 一樣。"""
        outcome: list = []

        def send() -> None:
            try:
                outcome.append(self._http.post(self._url, json={"user_id": user_id},
                                               headers={"Authorization": f"Bearer {self._credential}"}))
            except BaseException as e:  # 交回呼叫的執行緒處理
                outcome.append(e)

        worker = threading.Thread(target=send, name="teacher-classes", daemon=True)
        worker.start()
        worker.join(self._deadline)
        if not outcome:
            raise httpx.TimeoutException(f"teacher-classes 超過 {self._deadline:g} 秒")
        if isinstance(outcome[0], BaseException):
            raise outcome[0]
        return outcome[0]

    def _ask(self, user_id: int) -> ManagedClasses | Refusal:
        try:
            response = self._post(user_id)
        except httpx.HTTPError as e:
            log.error("teacher-classes 失敗（%s）：%s", self._url, type(e).__name__)
            return UNAVAILABLE
        if response.status_code != 200:  # 401 是 Service Credential 設錯
            log.error("teacher-classes 失敗（%s）：HTTP %s", self._url, response.status_code)
            return UNAVAILABLE
        try:
            return read_answer(response.json())
        except (ValueError, KeyError, TypeError) as e:
            log.error("teacher-classes 的回應不符合合約：%s", type(e).__name__)
            return UNAVAILABLE


def read_answer(body: dict) -> ManagedClasses | Refusal:
    """把 router 的回應（docs/contracts/teacher-classes）讀成 ManagedClasses 或 Refusal。"""
    if not body["accepted"]:
        return Refusal(body["reason"], body["message"])
    return ManagedClasses(bool(body["is_admin"]), tuple(ClassRef(c["id"], c["name"]) for c in body["classes"]))
