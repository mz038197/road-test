"""路考網站測試共用的東西：小路考、假偵測器、假的 router、交件的小幫手。

假偵測器不讀真的 ONNX：交上去的「模型」是一份 JSON，寫好類別名稱和每一幀要回傳的框。
寫壞的 JSON 就當成讀不進來的模型。這樣每個測試看交了什麼，就知道偵測器會框出什麼。
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from road_test.detector import ModelRejected
from road_test.web.app import RoadTest, create_app
from road_test.web.key_check import KeyCheck

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


# 金鑰：假的 router 依金鑰回答 router 的合約範例檔（tests/contracts/key-check/，複製自 router commit bff594f）
CONTRACTS = Path(__file__).parent / "contracts" / "key-check"
TEACHER_KEY = "vcr_sk_teacher" + "t" * 40      # 個人金鑰，管理 56「週一 程式設計」、57「週三 進階班」
ADMIN_KEY = "vcr_sk_admin00" + "a" * 40        # admin 的個人金鑰：classes 是所有班級
NO_CLASS_KEY = "vcr_sk_nocls0" + "n" * 40      # 只有學生身分的人的個人金鑰：classes 是空的
CLASSROOM_KEY = "vcr_sk_class0" + "c" * 40     # 課堂金鑰，56 班的學生 1234
CLASSMATE_KEY = "vcr_sk_mate00" + "m" * 40     # 課堂金鑰，56 班的學生 1235
THIRD_KEY = "vcr_sk_third0" + "h" * 40         # 課堂金鑰，56 班的學生 1236
OTHER_CLASS_KEY = "vcr_sk_other0" + "o" * 40   # 課堂金鑰，57 班的學生 2001
DOWN_KEY = "vcr_sk_down00" + "d" * 40          # router 對這把金鑰回 503
ALL_CLASSES = [{"id": 56, "name": "週一 程式設計"}, {"id": 57, "name": "週三 進階班"}, {"id": 58, "name": "週五 別人的班"}]


def contract(name: str) -> dict:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def router_answers() -> dict[str, dict]:
    personal = contract("personal-accepted")
    return {
        TEACHER_KEY: personal,
        ADMIN_KEY: {**personal, "user_id": 1, "is_admin": True, "classes": ALL_CLASSES},
        NO_CLASS_KEY: {**personal, "user_id": 77, "classes": []},
        CLASSROOM_KEY: contract("classroom-accepted"),
        CLASSMATE_KEY: {**contract("classroom-accepted"), "student_id": 1235},
        THIRD_KEY: {**contract("classroom-accepted"), "student_id": 1236},
        OTHER_CLASS_KEY: {**contract("classroom-accepted"), "student_id": 2001, "class_id": 57, "class_session_id": 790},
    }


def fake_router_key_check() -> KeyCheck:
    """road-test 自己的 Key Check 用戶端，接到假的 router（在 HTTP 邊界上換掉）。"""
    answers = router_answers()

    def router(request: httpx.Request) -> httpx.Response:
        key = json.loads(request.content)["api_key"]
        if key == DOWN_KEY:
            return httpx.Response(503)
        return httpx.Response(200, json=answers.get(key, contract("refused")))

    return KeyCheck("https://router.test", "road-test-credential", transport=httpx.MockTransport(router))


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "answers").mkdir()
    (tmp_path / "answers" / "Mini.json").write_text(json.dumps(MINI), encoding="utf-8")
    return tmp_path


@pytest.fixture
def open_site(data_dir: Path):
    """開一個路考網站（同一個路考資料夾可以開很多次，當成重開機）。"""
    opened = []

    def open_(**options) -> TestClient:
        options.setdefault("verify_key", fake_router_key_check().check)
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


# 交件測試用的班級：56 班（TEACHER_KEY 管）有 01、02、03 三組，每組一個學生
STUDENTS = {"01": CLASSROOM_KEY, "02": CLASSMATE_KEY, "03": THIRD_KEY}
CLASS_ID = 56


def seat(client: TestClient) -> None:
    """老師替 56 班建好 01、02、03 組，每個學生選進自己那組（已經建好就不再建）。"""
    if client.get(f"/api/teacher/classes/{CLASS_ID}", headers=bearer(TEACHER_KEY)).json()["groups"]:
        return
    for group, key in STUDENTS.items():
        gid = client.post(f"/api/teacher/classes/{CLASS_ID}/groups", json={"name": group},
                          headers=bearer(TEACHER_KEY)).json()["id"]
        joined = client.post("/api/student/group", json={"group_id": gid, "display_name": f"{group} 組的學生"},
                             headers=bearer(key))
        assert joined.status_code == 201, joined.text


def submit(client: TestClient, group: str = "01", content: bytes | None = None,
           filename: str = "best.onnx", conf: float | None = None, kind: str = "mock", confirm: bool = True):
    """這組的學生用課堂金鑰交件。正式路考預設勾了「交了就不能重交」的確認（confirm=False 就是沒勾）。"""
    seat(client)
    data = {"kind": kind}
    if conf is not None:
        data["conf"] = str(conf)
    if kind == "final" and confirm:
        data["confirm"] = "true"
    return client.post("/api/submissions", data=data, headers=as_group(group),
                       files={"model": (filename, content or model(), "application/octet-stream")})


def run_queue(client: TestClient) -> None:
    """把排隊的路考全部跑完（測試裡不開背景 worker）。"""
    client.app.state.worker.run_until_idle()


def as_group(group: str) -> dict:
    """這組的學生的課堂金鑰。"""
    return bearer(STUDENTS[group])


def records(client: TestClient, group: str) -> list[dict]:
    """這組的路考紀錄（學生看的），新的在前面。"""
    seat(client)
    response = client.get("/api/student/records", headers=as_group(group))
    assert response.status_code == 200, response.text
    return response.json()


def set_final(client: TestClient, is_open: bool):
    """老師開關 56 班的正式路考。"""
    seat(client)
    return client.put(f"/api/teacher/classes/{CLASS_ID}/final", json={"open": is_open}, headers=bearer(TEACHER_KEY))

