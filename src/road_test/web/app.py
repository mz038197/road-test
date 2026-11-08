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
from road_test.web.key_check import UNAVAILABLE, Identity, Refusal
from road_test.web.upload_limit import UploadLimit
from road_test.web.results import score_result
from road_test.web.settings import DEFAULT_MAX_UPLOAD_MB
from road_test.web.store import AlreadyMember, Busy, DuplicateGroup, Member, NoFinalLeft, NoSuchGroup, NotMember, Store
from road_test.web.worker import Worker

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
KIND_NAMES = {"mock": "模擬路考", "final": "正式路考"}
TRAINING_VIDEO = "0016E5"  # 訓練影片，在路考資料夾的 videos/ 裡；不會出現在路考裡
VIDEO_URL_HOURS = 6  # 路考影片網址幾小時後失效（結果頁每次拿成績都會拿到新的網址）
NOT_OPEN = "這一班還沒開放路考"  # 班級還沒建立組別時學生看到的
CONTENT_SECURITY_POLICY = "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"


@dataclass(frozen=True)
class RoadTest:
    kind: str          # "mock"（模擬路考）或 "final"（正式路考）
    sequence: str      # 影片／答案的名稱，例如 0006R0
    frame_count: int   # 影片一共幾幀


@dataclass(frozen=True)
class Viewer:
    """誰在看：課堂金鑰看自己選的組，個人金鑰看自己能管理的班級（road-test ADR 0001）。"""
    member: Member | None = None
    classes: frozenset[int] = frozenset()

    def can_see_row(self, row) -> bool:
        """這次交件看不看得到：只給同組的組員和管這個班級的老師（還沒選組的學生什麼都看不到）。"""
        if self.member is not None:
            return row["group_id"] == self.member.group_id
        return row["class_id"] in self.classes


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
               workers: int, fill_chunk: int = 500, student_dir: Path | None = None, clock: Callable[[], float] = time.time,
               max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1_000_000,
               verify_key: Callable[[str], Identity | Refusal] | None = None) -> FastAPI:
    """data_dir：路考資料夾；load_detector：讀模型檔的函式（讀不進來要丟 ModelRejected）；
    road_tests：{"mock": RoadTest, "final": RoadTest}；workers：背景 worker 數（main 用 settings.from_env 的預設），0 就不開（測試自己跑 run_until_idle）；
    student_dir：發給學生的訓練程式（預設 paths.student_dir()）；clock：現在幾秒（測試換成假時鐘）；
    max_upload_bytes：交件的大小上限（超過回 413，見 upload_limit.py）；
    verify_key：驗金鑰的函式（main 用 key_check.KeyCheck.check，測試換成接假 router 的），None 就是一律「驗證服務暫時無法使用」。
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

    @app.middleware("http")
    async def content_security_policy(request: Request, call_next):
        # 金鑰在 sessionStorage，個人金鑰還能呼叫 LLM：只跑網站自己的 script，不載入第三方、不跑 inline script
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # 記在 road_test 底下才會送到 vans-signals（見 signals.py）；不記查詢字串，網址上可能有簽章
        log.error("%s %s 出錯", request.method, request.url.path, exc_info=exc)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    video_key = _video_key(data_dir)

    def video_signature(row, expires: int) -> str:
        """影片網址的簽章：綁定這次路考（編號＋上傳時間）和到期時間，清空後編號重用也不會對到舊網址，
        改網址上的到期時間簽章就對不上。"""
        message = f"{row['id']}:{row['sequence']}:{row['uploaded_at']}:{expires}".encode()
        return hmac.new(video_key, message, hashlib.sha256).hexdigest()[:32]

    def video_url(row) -> str:
        expires = int(clock()) + VIDEO_URL_HOURS * 3600
        return f"/api/submissions/{row['id']}/video?e={expires}&t={video_signature(row, expires)}"

    # 金鑰（road-test ADR 0001）：每個請求帶 Authorization: Bearer vcr_sk_…，用 router 的 Key Check 驗（留 60 秒）。
    # 網站不存金鑰，也不寫進記錄。
    check_key = verify_key or (lambda _key: UNAVAILABLE)

    def identity(authorization: str | None = Header(None)) -> Identity:
        scheme, _, key = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not key.strip():
            raise HTTPException(401, "請貼上你的 API Key。")
        who = check_key(key)
        if isinstance(who, Refusal):
            raise HTTPException(503 if who.reason == "unavailable" else 401, who.message)
        return who

    def teacher_key(who: Identity = Depends(identity)) -> Identity:
        """個人金鑰、而且有班級可以管（admin 是所有班級）才是老師。"""
        if who.kind != "personal":
            raise HTTPException(403, "課堂金鑰不能用老師的功能，請貼上你的個人 API Key。")
        if not who.is_teacher:
            raise HTTPException(403, "你沒有可管理的班級")
        return who

    def student(who: Identity = Depends(identity)) -> Identity:
        """課堂金鑰才是學生：只能在自己的 class_id 裡選組、交件、看自己這組。"""
        if who.kind != "classroom":
            raise HTTPException(403, "個人金鑰不能交件，請貼上你的課堂 API Key。")
        return who

    def class_state(class_id: int) -> dict:
        is_open = store.class_final_open(class_id)
        return {"final_open": is_open, "kinds": [
            {"kind": k, "name": KIND_NAMES[k], "open": (k == "final") == is_open} for k in road_tests]}

    @app.get("/api/student")
    def student_state(who: Identity = Depends(student)):
        """學生進來時看的：自己班級的組別、自己選了哪一組、哪一種路考現在收件、這組還剩幾次正式路考。"""
        groups = store.class_groups(who.class_id)
        member = store.member(who.class_id, who.student_id)
        return {"class_id": who.class_id, "student_id": who.student_id,
                "groups": [{"id": g["id"], "name": g["name"]} for g in groups],
                "message": "" if groups else NOT_OPEN,
                "member": member_view(member), **class_state(who.class_id),
                "finals_left": member and store.finals_left(Member(who.class_id, member["group_id"], who.student_id))}

    def member_view(row) -> dict | None:
        return row and {"group_id": row["group_id"], "group_name": row["group_name"], "display_name": row["display_name"]}

    def joined_member(who: Identity) -> Member:
        row = store.member(who.class_id, who.student_id)
        if row is None:
            raise HTTPException(409, "請先選組別，選好之後才能交件、看路考紀錄。")
        return Member(who.class_id, row["group_id"], who.student_id)

    @app.get("/api/student/records")
    def student_records(who: Identity = Depends(student)):
        """自己這組的路考紀錄，新的在前面。"""
        return [summary(r) for r in store.of_class_group(joined_member(who).group_id)]

    class Join(BaseModel):
        group_id: int
        display_name: str = Field(min_length=1, max_length=40)

    @app.post("/api/student/group", status_code=201)
    def join_group(body: Join, who: Identity = Depends(student)):
        """第一次進來選組並填顯示名稱，之後鎖定；選錯由老師改。只能選自己班級的組。"""
        name = body.display_name.strip()
        if not name:
            raise HTTPException(400, "請填顯示名稱，讓老師知道你是誰。")
        try:
            return member_view(store.join(who.class_id, who.student_id, body.group_id, name))
        except NoSuchGroup:
            raise HTTPException(404, "你的班級沒有這個組別。") from None
        except AlreadyMember:
            raise HTTPException(409, "你已經選過組別了，選錯請找老師改。") from None

    def viewer(who: Identity = Depends(identity)) -> Viewer:
        """課堂金鑰看自己選的組；個人金鑰看自己能管理的班級（管理頁播放任一組用）。"""
        if who.kind == "classroom":
            row = store.member(who.class_id, who.student_id)
            return Viewer(member=row and Member(who.class_id, row["group_id"], who.student_id))
        return Viewer(classes=frozenset(cid for cid, _name in who.classes))

    def visible(sid: int, who: Viewer):
        row = store.get(sid)
        if row is None or not who.can_see_row(row):
            raise HTTPException(404, "找不到這次路考。")
        return row

    def summary(row) -> dict:
        result = json.loads(row["result"]) if row["result"] else None
        return {
            "id": row["id"], "group": row["group_name"],
            "class_id": row["class_id"], "group_id": row["group_id"], "student_id": row["student_id"], "kind": row["kind"], "kind_name": KIND_NAMES[row["kind"]],
            "sequence": row["sequence"], "conf": row["conf"], "conf_locked": row["kind"] == "final",
            "filename": row["filename"],
            "status": row["status"], "score": result["score"] if result else None, "error": row["error"],
            "uploaded_at": row["uploaded_at"], "started_at": row["started_at"],
            "scored_at": row["scored_at"], "done_at": row["done_at"],
        }

    class FinalSwitch(BaseModel):
        open: bool

    @app.post("/api/submissions", status_code=201)
    def submit(kind: str = Form("mock"), conf: float = Form(0.35), confirm: bool = Form(False),
               model: UploadFile = File(), who: Identity = Depends(student)):
        """交件。正式路考要 confirm=true（學生確認了「交了就不能重交」），交出的 CONF 就鎖定。

        交到自己選的班級組別：正式路考開關看班級、次數看組別。"""
        team = joined_member(who)
        is_open = store.class_final_open(team.class_id)
        if kind not in road_tests:
            raise HTTPException(400, f"{KIND_NAMES.get(kind, kind)}目前沒有開放。")
        # 以收到交件的時間為準：開關在這一刻是什麼就照什麼，收下之後老師再關也照樣跑完
        if kind == "final" and not is_open:
            raise HTTPException(400, "正式路考目前沒有開放，要等老師在課堂上開放才能交。")
        if kind == "mock" and is_open:
            raise HTTPException(400, "正式路考開放期間，模擬路考暫停收件，等老師關閉正式路考後再交。")
        if not 0.05 <= conf <= 1:
            raise HTTPException(400, "CONF 要在 0.05 到 1 之間。")
        if kind == "final" and not confirm:
            raise HTTPException(400, "正式路考交了就不能重交，請先勾選確認。")
        busy = HTTPException(409, "你們這組已經有一件路考在跑，等它跑完再交。")
        no_final = HTTPException(409, "你們這組的正式路考次數用完了。要重交請找老師加次數。")
        if kind == "final" and store.finals_left(team) <= 0:
            raise no_final
        if kind == "mock" and store.active(team):
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
            sid = store.add(team, kind, rt.sequence, conf, model.filename or "", upload, rt.frame_count,
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

        <video> 不能帶 Authorization 標頭，所以權限放在網址的簽章 t 裡：看得到這次結果的人（自己這組、老師）
        才拿得到 video_url。網址 6 小時後失效（e 是到期時間）；簽章不對、過期一律 403，不透露有沒有這次路考。
        """
        row = store.get(sid)
        if row is None or e < clock() or not hmac.compare_digest(t, video_signature(row, e)):
            raise HTTPException(403, "影片網址不對或已經失效，請回結果頁重新整理。")
        path = worker.video(row["sequence"])
        if not path.is_file():
            raise HTTPException(404, "找不到路考影片，請通知老師。")
        return FileResponse(path, media_type="video/mp4")

    # 委託頁（index.html）的下載：不需要金鑰
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

    def managed_class(class_id: int, who: Identity = Depends(teacher_key)) -> tuple[int, str]:
        name = dict(who.classes).get(class_id)
        if name is None:
            raise HTTPException(404, "找不到這個班級，或你不能管理它。")
        return class_id, name

    def group_view(row, members: list | None = None) -> dict:
        view = {"id": row["id"], "name": row["name"], "final_tries": row["final_tries"]}
        if members is not None:
            view["members"] = [{"student_id": m["student_id"], "display_name": m["display_name"]}
                               for m in members if m["group_id"] == row["id"]]
        return view

    def class_view(cls: tuple[int, str]) -> dict:
        class_id, name = cls
        members = store.members(class_id)
        return {"id": class_id, "name": name, "final_open": store.class_final_open(class_id),
                "groups": [group_view(g, members) for g in store.class_groups(class_id)]}

    @app.get("/api/teacher/classes")
    def teacher_classes(who: Identity = Depends(teacher_key)):
        """老師能管理的班級（router 的 Key Check 給的 classes，帶班級名稱）。"""
        return {"is_admin": who.is_admin, "classes": [{"id": cid, "name": name} for cid, name in who.classes]}

    @app.get("/api/teacher/classes/{class_id}")
    def teacher_class(cls: tuple[int, str] = Depends(managed_class)):
        """一個班級的組別（每組幾次正式路考）和正式路考開關。"""
        return class_view(cls)

    @app.get("/api/teacher/classes/{class_id}/records")
    def class_records(cls: tuple[int, str] = Depends(managed_class)):
        """這個班級所有組的路考紀錄，新的在前面，每筆帶交件的學生的顯示名稱。"""
        return [{**summary(r), "display_name": r["display_name"]} for r in store.of_class(cls[0])]

    class NewGroup(BaseModel):
        name: str = Field(min_length=1, max_length=40)
        final_tries: int = Field(1, ge=1, le=99)

    class GroupChange(BaseModel):
        name: str | None = Field(None, min_length=1, max_length=40)
        final_tries: int | None = Field(None, ge=1, le=99)

    def group_name(name: str | None) -> str | None:
        if name is not None and not name.strip():
            raise HTTPException(400, "組別要有名稱。")
        return name.strip() if name is not None else None

    taken = HTTPException(409, "這個班級已經有同名的組別了。")

    @app.post("/api/teacher/classes/{class_id}/groups", status_code=201)
    def add_class_group(body: NewGroup, cls: tuple[int, str] = Depends(managed_class)):
        """替班級建立組別，設定這組有幾次正式路考。"""
        try:
            return group_view(store.add_class_group(cls[0], group_name(body.name), body.final_tries))
        except DuplicateGroup:
            raise taken from None

    @app.put("/api/teacher/classes/{class_id}/groups/{group_id}")
    def change_class_group(group_id: int, body: GroupChange, cls: tuple[int, str] = Depends(managed_class)):
        """改組別的名稱或正式路考次數。"""
        try:
            row = store.change_class_group(cls[0], group_id, group_name(body.name), body.final_tries)
        except DuplicateGroup:
            raise taken from None
        if row is None:
            raise HTTPException(404, "這個班級沒有這個組別。")
        return group_view(row)

    class Move(BaseModel):
        group_id: int

    @app.put("/api/teacher/classes/{class_id}/members/{student_id}")
    def move_member(student_id: int, body: Move, cls: tuple[int, str] = Depends(managed_class)):
        """把選錯組的學生改到同一班的另一組。之前交的路考照樣算原本那組。"""
        try:
            row = store.move(cls[0], student_id, body.group_id)
        except NoSuchGroup:
            raise HTTPException(404, "這個班級沒有這個組別。") from None
        except NotMember:
            raise HTTPException(404, "這個學生還沒在這個班級選組。") from None
        return {"student_id": student_id, **member_view(row)}

    @app.put("/api/teacher/classes/{class_id}/final")
    def switch_class_final(body: FinalSwitch, cls: tuple[int, str] = Depends(managed_class)):
        """開關這個班級的正式路考，別的班級不受影響。"""
        store.set_class_final(cls[0], body.open)
        return class_view(cls)

    app.mount("/", FrontEnd(directory=STATIC, html=True), name="static")
    return app


class FrontEnd(StaticFiles):
    """前端檔案加 Cache-Control: no-cache：瀏覽器每次用 ETag 問一次有沒有新版（沒改是 304），部署後不會跑舊的 JS。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
