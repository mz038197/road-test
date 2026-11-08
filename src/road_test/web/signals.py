"""把 road_test 底下的 ERROR 記錄送到 vans-signals，一筆記錄一筆 Signal（照 router 的 ADR 0033）。

  VANS_SIGNALS_URL    vans-signals 的網址（Fly secret）
  VANS_SIGNALS_TOKEN  road-test 自己的 vans-signals token（Fly secret；它的 SHA-256 要加進 vans-signals 的 SIGNALS_TOKENS）

兩個都設定才送，本機開發不設定就什麼都不送。送的時候開背景執行緒，請求不用等；
大約 2 秒逾時、不重送；送失敗只記在 road_test 以外的 logger，不影響使用者的請求。
"""
import json
import logging
import os
import threading
import urllib.request
from datetime import datetime, timezone

SIGNAL_SOURCE = "road-test"
SIGNAL_TIMEOUT_SEC = 2.0
_FAILURE_LOGGER = "vans_signals_forwarder"


def _signal_message(record: logging.LogRecord) -> str:
    message = record.getMessage()
    if record.exc_info and record.exc_info[0] is not None:
        stack = logging.Formatter().formatException(record.exc_info)
        if stack:
            return f"{message}\n{stack}"
    return message


class SignalForwarder(logging.Handler):
    def __init__(self, url: str, token: str):
        super().__init__(level=logging.ERROR)
        self._url = url.rstrip("/") + "/signals"
        self._token = token
        self._inflight: list[threading.Thread] = []

    def emit(self, record: logging.LogRecord) -> None:
        payload = {
            "log_time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "logger_name": record.name,
            "level": record.levelname,
            "message": _signal_message(record),
            "source": SIGNAL_SOURCE,
        }
        worker = threading.Thread(target=self._post, args=(payload,), daemon=True)
        self._inflight = [t for t in self._inflight if t.is_alive()] + [worker]
        worker.start()

    def _post(self, payload: dict) -> None:
        request = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=SIGNAL_TIMEOUT_SEC):
                pass
        except Exception as exc:  # 送不出去也不能影響使用者
            logging.getLogger(_FAILURE_LOGGER).error("Signal post failed: %s", type(exc).__name__)

    def close(self) -> None:
        logging.getLogger("road_test").removeHandler(self)
        for worker in list(self._inflight):
            worker.join(timeout=SIGNAL_TIMEOUT_SEC + 1.0)
        super().close()


def start_signal_forwarding(url: str | None = None, token: str | None = None) -> SignalForwarder | None:
    destination = (url if url is not None else os.getenv("VANS_SIGNALS_URL") or "").strip()
    bearer = (token if token is not None else os.getenv("VANS_SIGNALS_TOKEN") or "").strip()
    if not destination or not bearer:
        return None
    forwarder = SignalForwarder(destination, bearer)
    logging.getLogger("road_test").addHandler(forwarder)
    return forwarder
