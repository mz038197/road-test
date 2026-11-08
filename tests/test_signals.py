"""road-test 的 ERROR 記錄轉送到 vans-signals（做法照 router 的 ADR 0033）。"""
import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import CLASSROOM_KEY, bearer
from road_test.web.signals import start_signal_forwarding


class _Receiver(ThreadingHTTPServer):
    """假的 vans-signals：記下收到的每一筆 POST。"""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.posts: list[dict] = []
        self.status = 200
        self.hold = threading.Event()
        self.release = threading.Event()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def close(self):
        self.release.set()
        self.shutdown()
        self.server_close()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.posts.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                  "body": json.loads(raw.decode("utf-8"))})
        if self.server.hold.is_set():
            self.server.release.wait(timeout=5)
        self.send_response(self.server.status)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def receiver():
    server = _Receiver()
    yield server
    server.close()


@pytest.fixture
def forwarding(receiver):
    forwarder = start_signal_forwarding(receiver.url, "road-test-token")
    assert forwarder is not None
    yield forwarder
    forwarder.close()


def test_an_error_log_posts_one_signal_with_source_road_test(receiver, forwarding):
    logging.getLogger("road_test.web.worker").error("交件 7 偵測計分幀時出錯")
    _wait_until(lambda: receiver.posts)

    assert len(receiver.posts) == 1
    post = receiver.posts[0]
    assert post["path"] == "/signals"
    assert post["authorization"] == "Bearer road-test-token"
    body = post["body"]
    assert body.keys() == {"log_time", "logger_name", "level", "message", "source"}
    assert body["source"] == "road-test"
    assert body["level"] == "ERROR"
    assert body["logger_name"] == "road_test.web.worker"
    assert body["message"] == "交件 7 偵測計分幀時出錯"
    assert datetime.fromisoformat(body["log_time"]).utcoffset() is not None


def test_without_url_or_token_nothing_is_sent(receiver, monkeypatch):
    monkeypatch.delenv("VANS_SIGNALS_URL", raising=False)
    monkeypatch.delenv("VANS_SIGNALS_TOKEN", raising=False)
    assert start_signal_forwarding() is None
    assert start_signal_forwarding(receiver.url, "") is None
    assert start_signal_forwarding("", "road-test-token") is None
    logging.getLogger("road_test.web.worker").error("本機出錯")
    time.sleep(0.15)
    assert receiver.posts == []


def test_settings_come_from_the_environment(receiver, monkeypatch):
    monkeypatch.setenv("VANS_SIGNALS_URL", receiver.url)
    monkeypatch.setenv("VANS_SIGNALS_TOKEN", "road-test-token")
    forwarder = start_signal_forwarding()
    try:
        logging.getLogger("road_test.web.app").error("出錯")
        _wait_until(lambda: receiver.posts)
        assert receiver.posts[0]["authorization"] == "Bearer road-test-token"
    finally:
        forwarder.close()


def test_warnings_and_other_loggers_are_not_sent(receiver, forwarding):
    logging.getLogger("road_test.web.worker").warning("只是警告")
    logging.getLogger("uvicorn.error").error("web server failed")
    logging.getLogger("vans_signals_forwarder").error("Signal post failed: URLError")
    time.sleep(0.15)
    assert receiver.posts == []


def test_the_exception_stack_is_kept(receiver, forwarding):
    try:
        raise RuntimeError("ONNX Runtime 爆掉了")
    except RuntimeError:
        logging.getLogger("road_test.web.worker").exception("交件 3 補幀時出錯")
    _wait_until(lambda: receiver.posts)
    message = receiver.posts[0]["body"]["message"]
    assert message.startswith("交件 3 補幀時出錯\n")
    assert "Traceback (most recent call last):" in message
    assert "RuntimeError: ONNX Runtime 爆掉了" in message


def test_a_failed_post_does_not_raise_and_is_not_retried(receiver, forwarding, caplog):
    receiver.status = 500
    with caplog.at_level(logging.ERROR, logger="vans_signals_forwarder"):
        logging.getLogger("road_test.web.worker").error("會送失敗")  # 不會丟出例外
        _wait_until(lambda: any(r.name == "vans_signals_forwarder" for r in caplog.records))
    time.sleep(0.3)
    assert len(receiver.posts) == 1
    failures = [r.getMessage() for r in caplog.records if r.name == "vans_signals_forwarder"]
    assert len(failures) == 1 and "road-test-token" not in failures[0]


def test_an_unreachable_destination_does_not_raise(caplog):
    forwarder = start_signal_forwarding("http://127.0.0.1:9", "road-test-token")
    try:
        with caplog.at_level(logging.ERROR, logger="vans_signals_forwarder"):
            logging.getLogger("road_test.web.worker").error("vans-signals 不在")
            _wait_until(lambda: any(r.name == "vans_signals_forwarder" for r in caplog.records))
    finally:
        forwarder.close()


def test_logging_does_not_wait_for_a_slow_destination(receiver, forwarding):
    receiver.hold.set()
    started = time.monotonic()
    logging.getLogger("road_test.web.app").error("慢的 vans-signals")
    assert time.monotonic() - started < 0.3
    _wait_until(lambda: receiver.posts)
    receiver.release.set()


def test_closing_waits_for_the_post_in_flight(receiver):
    receiver.hold.set()
    forwarder = start_signal_forwarding(receiver.url, "road-test-token")
    logging.getLogger("road_test.web.app").error("關機前的錯誤")
    _wait_until(lambda: receiver.posts)
    threading.Timer(0.3, receiver.release.set).start()
    started = time.monotonic()
    forwarder.close()
    assert time.monotonic() - started >= 0.25


def test_an_unexpected_error_in_a_request_reaches_vans_signals(receiver, forwarding, open_site, monkeypatch):
    from fastapi.testclient import TestClient

    app = open_site().app

    def broken(*args, **kwargs):
        raise RuntimeError("沒想到的錯")

    monkeypatch.setattr(app.state.store, "class_groups", broken)
    response = TestClient(app, raise_server_exceptions=False).get("/api/student?key=vcr_sk_secret",
                                                                  headers=bearer(CLASSROOM_KEY))
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    _wait_until(lambda: receiver.posts)
    body = receiver.posts[0]["body"]
    assert body["source"] == "road-test"
    assert body["logger_name"].startswith("road_test.")
    assert body["message"].startswith("GET /api/student 出錯\n")
    assert "RuntimeError: 沒想到的錯" in body["message"]
    assert "vcr_sk_secret" not in body["message"] and CLASSROOM_KEY not in body["message"]

