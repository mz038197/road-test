"""排隊的 worker：先偵測計分幀、算出成績，再把其餘的幀補完。

一件交件分兩段做：
1. 偵測全部的計分幀 → 計分 → 成績馬上看得到（狀態 filling）。
2. 補完其餘的幀，每次做 fill_chunk 幀，好幾個 worker 可以一起補同一件。每做完一段就回頭看有沒有新的交件在排隊，
   有就先去偵測新交件的計分幀，所以後面交的組也是幾秒內就有成績。
worker 是背景執行緒（onnxruntime 推論時會放開 GIL），數量由 create_app 的 workers 決定。
"""
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from road_test.detector import Detector
from road_test.web.results import score_result
from road_test.web.store import Store

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: Store, load_detector: Callable[[Path], Detector], data_dir: Path, fill_chunk: int = 500):
        self.store = store
        self.load_detector = load_detector
        self.data_dir = data_dir
        self.fill_chunk = fill_chunk
        self._held: dict[int, set[int]] = {}  # 交件編號 → 正在被某個 worker 補的幀
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def answers(self, sequence: str) -> dict:
        return json.loads((self.data_dir / "answers" / f"{sequence}.json").read_text(encoding="utf-8"))

    def video(self, sequence: str) -> Path:
        return self.data_dir / "videos" / f"{sequence}.mp4"

    def model(self, row) -> Path:
        return self.store.models_dir / row["model"]

    # 一次做一件事
    def run_once(self) -> bool:
        """做一段工作：有排隊的先偵測計分幀，沒有就補一段幀。沒事可做回傳 False。"""
        row = self.store.claim_queued()
        if row is not None:
            self._score(row)
            return True
        with self._lock:
            # 同一件可以由好幾個 worker 分段一起補，一件的時候也用得到全部的 CPU
            for row in self.store.filling():
                held = self._held.setdefault(row["id"], set())
                done = self.store.done_frames(row["id"])
                todo = [n for n in range(row["frames_total"]) if n not in done and n not in held][: self.fill_chunk]
                if todo or not held:  # 沒有 todo 也沒人在補：已經補完，交給 _fill 收尾
                    held.update(todo)
                    break
            else:
                return False
        try:
            self._fill(row, todo)
        finally:
            with self._lock:
                held.difference_update(todo)
                if not held:
                    self._held.pop(row["id"], None)
        return True

    def run_until_idle(self) -> None:
        while self.run_once():
            pass

    def _score(self, row) -> None:
        sid = row["id"]
        try:
            answers = self.answers(row["sequence"])
            frames = [f["frame"] for f in answers["frames"]]
            detector = self.load_detector(self.model(row))
            self.store.save_detections(sid, dict(detector.detect(self.video(row["sequence"]), frames)))
            self.store.scored(sid, score_result(answers, self.store.detections(sid), row["conf"]))
            if self.store.get(sid)["frames_done"] >= row["frames_total"]:
                self.store.finish(sid)
        except Exception as e:  # 一件出錯不能讓 worker 停掉
            if self.store.get(sid) is None:  # 跑到一半被老師清空了
                return
            log.exception("交件 %s 偵測計分幀時出錯", sid)
            self.store.fail(sid, f"偵測時出錯：{e}")

    def _fill(self, row, todo: list[int]) -> None:
        sid = row["id"]
        try:
            if todo:
                detector = self.load_detector(self.model(row))
                self.store.save_detections(sid, dict(detector.detect(self.video(row["sequence"]), todo)))
            if self.store.get(sid)["frames_done"] >= row["frames_total"]:
                self.store.finish(sid)
        except Exception as e:
            if self.store.get(sid) is None:  # 跑到一半被老師清空了
                return
            log.exception("交件 %s 補幀時出錯", sid)
            self.store.fail(sid, f"補完其餘的幀時出錯：{e}")

    # 背景執行緒
    def wake(self) -> None:
        self._wake.set()

    def start(self, count: int) -> None:
        for i in range(count):
            t = threading.Thread(target=self._loop, name=f"road-test-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(timeout=1)
                self._wake.clear()
