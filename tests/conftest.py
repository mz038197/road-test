"""路考網站測試共用的東西：小路考、假偵測器、交件的小幫手。

假偵測器不讀真的 ONNX：交上去的「模型」是一份 JSON，寫好類別名稱和每一幀要回傳的框。
寫壞的 JSON 就當成讀不進來的模型。這樣每個測試看交了什麼，就知道偵測器會框出什麼。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from road_test.detector import ModelRejected
from road_test.web.app import RoadTest, create_app

GROUPS = {"01": "apple", "02": "banana", "03": "cherry"}
TEACHER = "teacher-secret"
CLASSES = ["car", "pedestrian", "bicyclist", "sign"]
PED = [100, 100, 140, 200]
CAR = [300, 200, 400, 260]
GHOST = [600, 50, 640, 130]

# 小路考：10 幀的影片，第 2、5、8 幀是計分幀（每秒一幀，fps = 3）。
# 行人第 2 幀起就是必看，一直到第 5 幀；車第 8 幀才出現。
MINI = {
    "sequence": "Mini", "width": 960, "height": 720, "fps": 3,
    "frames": [
        {"name": "Mini_f00002.png", "frame": 2, "objects": [{"cls": "pedestrian", "box": PED, "relevant": True}]},
        {"name": "Mini_f00005.png", "frame": 5, "objects": [{"cls": "pedestrian", "box": PED, "relevant": True}]},
        {"name": "Mini_f00008.png", "frame": 8, "objects": [{"cls": "car", "box": CAR, "relevant": True}]},
    ],
}
FRAME_COUNT = 10


class FakeDetector:
    def __init__(self, path: Path):
        try:
            spec = json.loads(Path(path).read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ModelRejected("模型讀不進來，請確認上傳的是匯出的 .onnx 檔。") from e
        self.names = spec["names"]
        self.boxes = spec.get("boxes", {})
        self.crash = spec.get("crash", False)

    def detect(self, video: Path, frames):
        if self.crash:
            raise RuntimeError("ONNX Runtime 爆掉了")
        for n in frames:
            yield n, [{"cls": c, "box": b, "conf": conf} for c, b, conf in self.boxes.get(str(n), [])]


def model(boxes: dict | None = None, names: list[str] = CLASSES, crash: bool = False) -> bytes:
    """假模型：boxes = {影片幀號: [(類別, 框, 信心分數), ...]}；crash：收件時正常、偵測時才出錯。"""
    boxes = {str(k): v for k, v in (boxes or {}).items()}
    return json.dumps({"names": names, "boxes": boxes, "crash": crash}).encode()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "answers").mkdir()
    (tmp_path / "answers" / "Mini.json").write_text(json.dumps(MINI), encoding="utf-8")
    (tmp_path / "groups.json").write_text(json.dumps(GROUPS), encoding="utf-8")
    return tmp_path


@pytest.fixture
def open_site(data_dir: Path):
    """開一個路考網站（同一個路考資料夾可以開很多次，當成重開機）。"""
    opened = []

    def open_(**options) -> TestClient:
        options.setdefault("teacher_password", TEACHER)
        app = create_app(data_dir, load_detector=FakeDetector,
                         road_tests={"mock": RoadTest("mock", "Mini", FRAME_COUNT),
                                     "final": RoadTest("final", "Mini", FRAME_COUNT)}, workers=0, **options)
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c

    yield open_
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def client(open_site) -> TestClient:
    return open_site()


def submit(client: TestClient, group: str = "01", code: str | None = None, content: bytes | None = None,
           filename: str = "best.onnx", conf: float | None = None, kind: str = "mock", confirm: bool = True):
    """交件。正式路考預設勾了「交了就不能重交」的確認（confirm=False 就是沒勾）。"""
    data = {"group": group, "code": GROUPS.get(group, "") if code is None else code, "kind": kind}
    if conf is not None:
        data["conf"] = str(conf)
    if kind == "final" and confirm:
        data["confirm"] = "true"
    return client.post("/api/submissions", data=data, files={"model": (filename, content or model(), "application/octet-stream")})


def run_queue(client: TestClient) -> None:
    """把排隊的路考全部跑完（測試裡不開背景 worker）。"""
    client.app.state.worker.run_until_idle()


def as_group(group: str) -> dict:
    return {"X-Group": group, "X-Group-Code": GROUPS[group]}


def as_teacher(password: str = TEACHER) -> dict:
    return {"X-Teacher-Password": password}


def set_final(client: TestClient, is_open: bool):
    """老師開關正式路考。"""
    return client.put("/api/teacher/final", json={"open": is_open}, headers=as_teacher())
