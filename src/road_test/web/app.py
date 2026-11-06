"""路考網站（FastAPI）：交件、排隊、成績、路考紀錄，前端是 static/ 裡的純 HTML、JS、CSS。

啟動：`uv run road-test-site`（見 main.py），資料夾由 ROAD_TEST_DATA 指定。
"""
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from road_test.detector import CLASSES, Detector, ModelRejected
from road_test.web.store import Store
from road_test.web.worker import Worker

STATIC = Path(__file__).parent / "static"
KIND_NAMES = {"mock": "模擬路考", "final": "正式路考"}


@dataclass(frozen=True)
class RoadTest:
    kind: str          # "mock"（模擬路考）或 "final"（正式路考）
    sequence: str      # 影片／答案的名稱，例如 0006R0
    frame_count: int   # 影片一共幾幀


@dataclass(frozen=True)
class Viewer:
    """誰在看：目前只有組別（帶組別代碼）。#10 加老師（老師密碼），老師看得到全部。"""
    group: str | None
    teacher: bool = False

    def can_see(self, group: str) -> bool:
        return self.teacher or self.group == group


def check_classes(names) -> None:
    names, wanted = set(names), set(CLASSES)

    def listed(items: set[str]) -> str:
        items = sorted(items)
        if len(items) > 6:  # 例如交了 COCO 的模型，80 類全列出來太長
            return f"{'、'.join(items[:6])} 等 {len(items)} 類"
        return "、".join(items) or "無"

    if names != wanted:
        extra, missing = listed(names - wanted), listed(wanted - names)
        raise ModelRejected(f"模型的類別名稱跟標註規範不一樣：多了 {extra}，少了 {missing}。"
                            "類別名稱要是 car、pedestrian、bicyclist、sign（大小寫要一樣），請回 Roboflow 改好後重新訓練。")


def create_app(data_dir: Path, load_detector: Callable[[Path], Detector], road_tests: dict[str, RoadTest],
               workers: int = 2, fill_chunk: int = 500) -> FastAPI:
    """data_dir：路考資料夾；load_detector：讀模型檔的函式（讀不進來要丟 ModelRejected）；
    road_tests：{"mock": RoadTest, "final": RoadTest}；workers：背景 worker 數，0 就不開（測試自己跑 run_until_idle）。
    """
    data_dir = Path(data_dir)
    store = Store(data_dir)
    worker = Worker(store, load_detector, data_dir, fill_chunk=fill_chunk)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start(workers)
        yield
        worker.stop()

    app = FastAPI(title="路考網站", lifespan=lifespan)
    app.state.store = store
    app.state.worker = worker

    def group_codes() -> dict[str, str]:
        """組別代碼：先讀路考資料夾的 groups.json（{"01": "代碼", ...}），#10 改成管理頁產生。每次都重讀，改了不用重開。"""
        return json.loads((data_dir / "groups.json").read_text(encoding="utf-8"))

    def check_code(group: str, code: str) -> None:
        if not code or group_codes().get(group) != code:
            raise HTTPException(403, "組別代碼不對，請跟老師確認你們這組的組別代碼。")

    def viewer(x_group: str | None = Header(None), x_group_code: str | None = Header(None)) -> Viewer:
        check_code(x_group or "", x_group_code or "")
        return Viewer(group=x_group)

    def visible(sid: int, who: Viewer):
        row = store.get(sid)
        if row is None or not who.can_see(row["grp"]):
            raise HTTPException(404, "找不到這次路考。")
        return row

    def summary(row) -> dict:
        result = json.loads(row["result"]) if row["result"] else None
        return {
            "id": row["id"], "group": row["grp"], "kind": row["kind"], "kind_name": KIND_NAMES[row["kind"]],
            "sequence": row["sequence"], "conf": row["conf"], "filename": row["filename"],
            "status": row["status"], "score": result["score"] if result else None, "error": row["error"],
            "uploaded_at": row["uploaded_at"], "started_at": row["started_at"],
            "scored_at": row["scored_at"], "done_at": row["done_at"],
        }

    @app.get("/api/groups")
    def groups():
        """交件頁的組別選單（不含代碼）。"""
        return sorted(group_codes())

    @app.post("/api/submissions", status_code=201)
    def submit(group: str = Form(), code: str = Form(), kind: str = Form("mock"),
               conf: float = Form(0.35), model: UploadFile = File()):
        check_code(group, code)
        if kind not in road_tests:
            raise HTTPException(400, f"{KIND_NAMES.get(kind, kind)}目前沒有開放。")
        if not 0.05 <= conf <= 1:
            raise HTTPException(400, "CONF 要在 0.05 到 1 之間。")
        if store.active(group):
            raise HTTPException(409, "你們這組已經有一件路考在跑，等它跑完再交。")
        upload = store.models_dir / f"upload-{uuid.uuid4().hex}.tmp"
        with upload.open("wb") as f:
            while chunk := model.file.read(1 << 20):
                f.write(chunk)
        try:
            try:
                detector = load_detector(upload)
            except ModelRejected:
                raise
            except Exception as e:
                raise ModelRejected("模型讀不進來，請確認上傳的是訓練程式匯出的 .onnx 檔。") from e
            check_classes(detector.names)
            rt = road_tests[kind]
            sid = store.add(group, kind, rt.sequence, conf, model.filename or "", upload, rt.frame_count)
        except ModelRejected as e:
            raise HTTPException(422, str(e)) from e
        finally:
            upload.unlink(missing_ok=True)
        if sid is None:
            raise HTTPException(409, "你們這組已經有一件路考在跑，等它跑完再交。")
        worker.wake()
        return {"id": sid, "status": "queued", "position": store.position(sid)}

    @app.get("/api/submissions/{sid}")
    def submission(sid: int, who: Viewer = Depends(viewer)):
        """排隊與進度；有成績之後 result 是計分結果（見 results.score_result）。"""
        row = visible(sid, who)
        return {**summary(row), "position": store.position(sid),
                "progress": {"done": row["frames_done"], "total": row["frames_total"]},
                "result": json.loads(row["result"]) if row["result"] else None}

    @app.get("/api/submissions/{sid}/detections")
    def detections(sid: int, who: Viewer = Depends(viewer)):
        """每一幀（含非計分幀）的偵測框，給播放用。還沒偵測的幀是 null。"""
        row = visible(sid, who)
        answers = worker.answers(row["sequence"])
        stored = store.detections(sid)
        return {
            "sequence": row["sequence"], "fps": answers["fps"], "width": answers["width"], "height": answers["height"],
            "classes": list(CLASSES), "box_format": ["x1", "y1", "x2", "y2", "conf", "class"],
            "scoring_frames": [f["frame"] for f in answers["frames"]],
            "frames": [stored.get(n) for n in range(row["frames_total"])],
        }

    @app.get("/api/groups/{group}/records")
    def records(group: str, who: Viewer = Depends(viewer)):
        """這組的路考紀錄，新的在前面。"""
        if not who.can_see(group):
            raise HTTPException(403, "只能看自己這組的路考紀錄。")
        return [summary(r) for r in store.of_group(group)]

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
