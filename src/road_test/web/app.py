"""路考網站（FastAPI）：交件、排隊、成績、路考紀錄，前端是 static/ 裡的純 HTML、JS、CSS。

啟動：`uv run road-test-site`（見 main.py），資料夾由 ROAD_TEST_DATA 指定。
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from road_test import paths
from road_test.detector import CLASSES, Detector, ModelRejected
from road_test.web.groups import GroupCodes
from road_test.web.limiter import FailureLimit
from road_test.web.upload_limit import UploadLimit
from road_test.web.results import score_result
from road_test.web.settings import DEFAULT_MAX_UPLOAD_MB
from road_test.web.store import Busy, NoFinalLeft, Store
from road_test.web.teacher import TARGET as TEACHER_TARGET, is_teacher, teacher_check
from road_test.web.worker import Worker

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
KIND_NAMES = {"mock": "模擬路考", "final": "正式路考"}
TRAINING_VIDEO = "0016E5"  # 訓練影片，在路考資料夾的 videos/ 裡；不會出現在路考裡
VIDEO_URL_HOURS = 6  # 路考影片網址幾小時後失效（結果頁每次拿成績都會拿到新的網址）


@dataclass(frozen=True)
class RoadTest:
    kind: str          # "mock"（模擬路考）或 "final"（正式路考）
    sequence: str      # 影片／答案的名稱，例如 0006R0
    frame_count: int   # 影片一共幾幀


@dataclass(frozen=True)
class Viewer:
    """誰在看：組別（帶組別代碼）或老師（帶老師密碼，看得到全部）。"""
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


def _video_key(data_dir: Path) -> bytes:
    """影片網址簽章用的金鑰，放在路考資料夾（video.key），第一次啟動時產生，重開機後舊網址照樣能用。"""
    path = data_dir / "video.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return bytes.fromhex(path.read_text(encoding="utf-8").strip())


def create_app(data_dir: Path, load_detector: Callable[[Path], Detector], road_tests: dict[str, RoadTest], *,
               workers: int, fill_chunk: int = 500, teacher_password: str | None = None,
               student_dir: Path | None = None, clock: Callable[[], float] = time.time,
               max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1_000_000) -> FastAPI:
    """data_dir：路考資料夾；load_detector：讀模型檔的函式（讀不進來要丟 ModelRejected）；
    road_tests：{"mock": RoadTest, "final": RoadTest}；workers：背景 worker 數（main 用 settings.from_env 的預設），0 就不開（測試自己跑 run_until_idle）；
    teacher_password：老師密碼（main 從 ROAD_TEST_TEACHER_PASSWORD 讀），None 就是老師的 API 一律拒絕；
    student_dir：發給學生的訓練程式（預設 paths.student_dir()）；clock：現在幾秒（測試換成假時鐘）；
    max_upload_bytes：交件的大小上限（超過回 413，見 upload_limit.py）。
    """
    data_dir = Path(data_dir)
    student_dir = Path(student_dir) if student_dir else paths.student_dir()
    store = Store(data_dir)
    worker = Worker(store, load_detector, data_dir, fill_chunk=fill_chunk)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start(workers)
        yield
        worker.stop()

    app = FastAPI(title="路考網站", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(UploadLimit, max_bytes=max_upload_bytes)
    app.state.worker = worker

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # 記在 road_test 底下才會送到 vans-signals（見 signals.py）；不記查詢字串，網址上可能有簽章
        log.error("%s %s 出錯", request.method, request.url.path, exc_info=exc)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    guesses = FailureLimit(clock)  # 組別代碼、老師密碼猜錯太多次就暫時 429
    teacher = teacher_check(teacher_password, guesses)

    def final_open() -> bool:
        return bool(store.setting("final_open", False))

    def road_test_state() -> dict:
        is_open = final_open()
        return {"final_open": is_open, "kinds": [
            {"kind": k, "name": KIND_NAMES[k], "open": (k == "final") == is_open} for k in road_tests]}

    video_key = _video_key(data_dir)

    def video_signature(row, expires: int) -> str:
        """影片網址的簽章：綁定這次路考（編號＋上傳時間）和到期時間，清空後編號重用也不會對到舊網址，
        改網址上的到期時間簽章就對不上。"""
        message = f"{row['id']}:{row['sequence']}:{row['uploaded_at']}:{expires}".encode()
        return hmac.new(video_key, message, hashlib.sha256).hexdigest()[:32]

    def video_url(row) -> str:
        expires = int(clock()) + VIDEO_URL_HOURS * 3600
        return f"/api/submissions/{row['id']}/video?e={expires}&t={video_signature(row, expires)}"

    codes = GroupCodes(data_dir / "groups.json")  # 組別代碼，管理頁產生、重設（見 groups.py）

    def check_code(request: Request, group: str, code: str) -> None:
        target = f"group:{group}"
        guesses.check(request, target)
        if not codes.matches(group, code):
            guesses.failed(request, target)
            raise HTTPException(403, "組別代碼不對，請跟老師確認你們這組的組別代碼。")

    def viewer(request: Request, x_group: str | None = Header(None), x_group_code: str | None = Header(None),
               x_teacher_password: str | None = Header(None)) -> Viewer:
        """帶老師密碼的是老師（看得到全部，管理頁播放任一組用），不然要帶對的組別代碼。"""
        if teacher_password and x_teacher_password:
            guesses.check(request, TEACHER_TARGET)
            if is_teacher(teacher_password, x_teacher_password):
                return Viewer(None, teacher=True)
            guesses.failed(request, TEACHER_TARGET)
        check_code(request, x_group or "", x_group_code or "")
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
            "sequence": row["sequence"], "conf": row["conf"], "conf_locked": row["kind"] == "final",
            "filename": row["filename"],
            "status": row["status"], "score": result["score"] if result else None, "error": row["error"],
            "uploaded_at": row["uploaded_at"], "started_at": row["started_at"],
            "scored_at": row["scored_at"], "done_at": row["done_at"],
        }

    @app.get("/api/groups")
    def groups():
        """交件頁的組別選單（不含代碼）。"""
        return sorted(codes.load())

    @app.get("/api/road-tests")
    def road_test_status():
        """哪一種路考現在收件：正式路考開放時只收正式路考，關著時只收模擬路考。"""
        return road_test_state()

    class FinalSwitch(BaseModel):
        open: bool

    @app.get("/api/teacher/final", dependencies=[Depends(teacher)])
    def final_state():
        return road_test_state()

    @app.put("/api/teacher/final", dependencies=[Depends(teacher)])
    def switch_final(body: FinalSwitch):
        """老師開關正式路考。關閉前已經收下的正式路考照樣跑完。"""
        store.set_setting("final_open", body.open)
        return road_test_state()

    @app.post("/api/submissions", status_code=201)
    def submit(request: Request, group: str = Form(), code: str = Form(), kind: str = Form("mock"),
               conf: float = Form(0.35), confirm: bool = Form(False), model: UploadFile = File()):
        """交件。正式路考要 confirm=true（學生確認了「交了就不能重交」），交出的 CONF 就鎖定。"""
        check_code(request, group, code)
        if kind not in road_tests:
            raise HTTPException(400, f"{KIND_NAMES.get(kind, kind)}目前沒有開放。")
        # 以收到交件的時間為準：開關在這一刻是什麼就照什麼，收下之後老師再關也照樣跑完
        if kind == "final" and not final_open():
            raise HTTPException(400, "正式路考目前沒有開放，要等老師在課堂上開放才能交。")
        if kind == "mock" and final_open():
            raise HTTPException(400, "正式路考開放期間，模擬路考暫停收件，等老師關閉正式路考後再交。")
        if not 0.05 <= conf <= 1:
            raise HTTPException(400, "CONF 要在 0.05 到 1 之間。")
        if kind == "final" and not confirm:
            raise HTTPException(400, "正式路考交了就不能重交，請先勾選確認。")
        busy = HTTPException(409, "你們這組已經有一件路考在跑，等它跑完再交。")
        no_final = HTTPException(409, "正式路考每組只能交一次，你們這組已經交過了。要重交請找老師。")
        if kind == "final" and store.finals_left(group) <= 0:
            raise no_final
        if kind == "mock" and store.active(group):
            raise busy
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
            sid = store.add(group, kind, rt.sequence, conf, model.filename or "", upload, rt.frame_count,
                            priority=1 if kind == "final" else 0)  # 正式路考插到模擬路考前面
        except ModelRejected as e:
            raise HTTPException(422, str(e)) from e
        except Busy:
            raise busy from None
        except NoFinalLeft:
            raise no_final from None
        finally:
            upload.unlink(missing_ok=True)
        worker.wake()
        return {"id": sid, "status": "queued", "position": store.position(sid)}

    @app.get("/api/submissions/{sid}")
    def submission(sid: int, who: Viewer = Depends(viewer)):
        """排隊與進度；有成績之後 result 是計分結果（見 results.score_result）。"""
        row = visible(sid, who)
        return {**summary(row), "position": store.position(sid), "video_url": video_url(row),
                "progress": {"done": row["frames_done"], "total": row["frames_total"]},
                "result": json.loads(row["result"]) if row["result"] else None}

    @app.get("/api/submissions/{sid}/rescore")
    def rescore(sid: int, conf: float, who: Viewer = Depends(viewer)):
        """CONF 滑桿：用新的 CONF 重算存下來的框（低門檻 0.05 的全部框），回傳跟 result 一樣格式的成績。

        只是讓學生看看調 CONF 的效果：不重新偵測、不排隊、不改交件的成績。正式路考的 CONF 鎖定，不能重算。
        """
        row = visible(sid, who)
        if row["kind"] == "final":
            raise HTTPException(400, "正式路考的 CONF 交件時就鎖定了，不能重算。")
        if not row["result"]:
            raise HTTPException(409, "成績還沒出來，等成績出來再調 CONF。")
        if not 0.05 <= conf <= 1:
            raise HTTPException(400, "CONF 要在 0.05 到 1 之間。")
        answers = worker.answers(row["sequence"])
        frames = [f["frame"] for f in answers["frames"]]
        return score_result(answers, store.detections(sid, frames), conf)

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

    @app.get("/api/submissions/{sid}/video")
    def video(sid: int, t: str = "", e: int = 0):
        """這次路考的影片，給結果頁的 <video> 播放（支援 Range，拖時間軸才順）。

        <video> 不能帶 X-Group 標頭，所以權限放在網址的簽章 t 裡：看得到這次結果的人（自己這組、老師）
        才拿得到 video_url。網址 6 小時後失效（e 是到期時間）；簽章不對、過期一律 403，不透露有沒有這次路考。
        """
        row = store.get(sid)
        if row is None or e < clock() or not hmac.compare_digest(t, video_signature(row, e)):
            raise HTTPException(403, "影片網址不對或已經失效，請回結果頁重新整理。")
        path = worker.video(row["sequence"])
        if not path.is_file():
            raise HTTPException(404, "找不到路考影片，請通知老師。")
        return FileResponse(path, media_type="video/mp4")

    # 委託頁（index.html）的下載：不需要組別代碼
    @app.get(f"/downloads/{TRAINING_VIDEO}.mp4")
    def training_video():
        """訓練影片：學生自己挑幀標註。支援 Range，下載中斷可以接著下。"""
        path = data_dir / "videos" / f"{TRAINING_VIDEO}.mp4"
        if not path.is_file():
            raise HTTPException(404, "找不到訓練影片，請通知老師。")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/downloads/train_yolo.py")
    def training_program():
        """本機訓練程式（Colab 版在 GitHub，委託頁用「在 Colab 開啟」連過去）。"""
        path = student_dir / "train_yolo.py"
        if not path.is_file():
            raise HTTPException(404, "找不到訓練程式，請通知老師。")
        return FileResponse(path, media_type="text/x-python; charset=utf-8", filename=path.name)

    @app.get("/api/groups/{group}/records")
    def records(group: str, who: Viewer = Depends(viewer)):
        """這組的路考紀錄，新的在前面。"""
        if not who.can_see(group):
            raise HTTPException(403, "只能看自己這組的路考紀錄。")
        return [summary(r) for r in store.of_group(group)]

    # 管理頁（teacher.html）：全部要老師密碼
    @app.get("/api/teacher/records", dependencies=[Depends(teacher)])
    def all_records():
        """所有組的路考紀錄，新的在前面。"""
        return [summary(r) for r in store.everything()]

    def group_table() -> list[dict]:
        return [{"group": g, "code": c, "finals_left": store.finals_left(g)} for g, c in sorted(codes.load().items())]

    def known_group(group: str) -> str:
        if group not in codes.load():
            raise HTTPException(404, f"沒有第 {group} 組。")
        return group

    @app.get("/api/teacher/groups", dependencies=[Depends(teacher)])
    def group_list():
        """各組的組別代碼和正式路考還剩幾次。"""
        return group_table()

    @app.post("/api/teacher/groups/{group}/code", dependencies=[Depends(teacher)])
    def reset_code(group: str):
        """重設這組的組別代碼，舊的馬上失效。"""
        return {"group": group, "code": codes.reset(known_group(group))}

    @app.post("/api/teacher/groups/{group}/final-returns", dependencies=[Depends(teacher)])
    def return_final(group: str):
        """還給這組一次正式路考（例如模型出錯要重交）。"""
        store.return_final(known_group(group))
        return {"group": group, "finals_left": store.finals_left(group)}

    class Clear(BaseModel):
        confirm: str = ""

    @app.post("/api/teacher/clear", dependencies=[Depends(teacher)])
    def clear(body: Clear):
        """學期結束清空交件、結果、路考紀錄（含還的正式路考次數）。組別代碼和正式路考開關不清。
        要帶 confirm="清空"（管理頁再確認一次之後才送），防止誤按。"""
        if body.confirm != "清空":
            raise HTTPException(400, "清空之後找不回來，請在管理頁再確認一次。")
        store.clear()
        return {"cleared": True}

    class GroupCount(BaseModel):
        count: int = Field(ge=1, le=99)

    @app.put("/api/teacher/groups", dependencies=[Depends(teacher)])
    def set_groups(body: GroupCount):
        """組別改成 01…count：原本的組保留代碼，新的組產生代碼，多的刪掉（路考紀錄留著）。"""
        codes.set_count(body.count)
        return group_table()

    @app.post("/api/teacher/codes", dependencies=[Depends(teacher)])
    def reset_all_codes():
        """每組都換新代碼（例如新學期），舊的全部失效。"""
        codes.reset_all()
        return group_table()

    app.mount("/", FrontEnd(directory=STATIC, html=True), name="static")
    return app


class FrontEnd(StaticFiles):
    """前端檔案加 Cache-Control: no-cache：瀏覽器每次用 ETag 問一次有沒有新版（沒改是 304），部署後不會跑舊的 JS。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
