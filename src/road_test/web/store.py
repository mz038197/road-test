"""交件、偵測結果、成績都存在路考資料夾的 SQLite（road_test.db），模型檔存在 submissions/。

部署時路考資料夾就是 Fly 的 volume，機器關掉再開，排隊和結果都還在。
每次操作開一個新連線，worker 執行緒和 API 可以同時用。
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from road_test.detector import CLASSES

# 交件的狀態：排隊中 → 偵測計分幀 → 有成績了、補完其餘的幀 → 全部完成；中途出錯是 failed
QUEUED, SCORING, FILLING, DONE, FAILED = "queued", "scoring", "filling", "done", "failed"
# 「每組同時一件」算的是這些狀態：成績出來了但其餘的幀還沒補完，也算還在跑
ACTIVE = (QUEUED, SCORING, FILLING)

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grp         TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- mock | final
    sequence    TEXT NOT NULL,
    conf        REAL NOT NULL,             -- 交件時選的 CONF
    filename    TEXT NOT NULL,             -- 學生上傳時的檔名
    model       TEXT,                      -- submissions/ 底下改過名的模型檔
    priority    INTEGER NOT NULL DEFAULT 0,-- 越大越先跑（正式路考）
    status      TEXT NOT NULL,
    frames_done  INTEGER NOT NULL DEFAULT 0,
    frames_total INTEGER NOT NULL,
    result      TEXT,                      -- 計分結果 JSON（見 Store.result）
    error       TEXT,
    uploaded_at TEXT NOT NULL,
    started_at  TEXT,
    scored_at   TEXT,
    done_at     TEXT
);
CREATE TABLE IF NOT EXISTS detections (
    submission_id INTEGER NOT NULL,
    frame         INTEGER NOT NULL,        -- 影片幀號
    boxes         TEXT NOT NULL,           -- [[x1, y1, x2, y2, conf, 類別編號], ...]，低門檻 0.05 的全部框
    PRIMARY KEY (submission_id, frame)
);
"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def pack(boxes: list[dict]) -> str:
    """偵測框存成精簡的陣列：座標到小數 1 位、信心分數到小數 3 位、類別用 CLASSES 的編號。"""
    return json.dumps([[*(round(v, 1) for v in b["box"]), round(b["conf"], 3), CLASSES.index(b["cls"])] for b in boxes])


def unpack(row: list) -> dict:
    return {"cls": CLASSES[row[5]], "box": row[:4], "conf": row[4]}


class Store:
    def __init__(self, data_dir: Path):
        self.models_dir = data_dir / "submissions"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "road_test.db"
        with self._db() as db:
            db.executescript(SCHEMA)
            # 上次關機時正在偵測計分幀的，重新排隊；補幀做到一半的，下次從沒做完的幀接著做
            db.execute("UPDATE submissions SET status = ? WHERE status = ?", (QUEUED, SCORING))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode = WAL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _tx(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    # 交件
    def active(self, group: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(f"SELECT * FROM submissions WHERE grp = ? AND status IN {ACTIVE}", (group,)).fetchone()

    def busy(self) -> bool:
        """還有路考在排隊、偵測或補幀（閒置自動關機要等這些跑完）。"""
        with self._db() as db:
            return db.execute(f"SELECT 1 FROM submissions WHERE status IN {ACTIVE} LIMIT 1").fetchone() is not None

    def add(self, group: str, kind: str, sequence: str, conf: float, filename: str, upload: Path,
            frames_total: int, priority: int = 0) -> int | None:
        """收件並排隊，模型檔改名成「組別_路考種類_上傳時間_編號.onnx」。這組已經有一件在跑就回傳 None。"""
        with self._tx() as db:
            if db.execute(f"SELECT 1 FROM submissions WHERE grp = ? AND status IN {ACTIVE}", (group,)).fetchone():
                return None
            uploaded = now()
            cur = db.execute(
                "INSERT INTO submissions (grp, kind, sequence, conf, filename, priority, status, frames_total, uploaded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (group, kind, sequence, conf, filename, priority, QUEUED, frames_total, uploaded))
            sid = cur.lastrowid
            name = f"{group}_{kind}_{uploaded[:19].replace(':', '').replace('-', '')}_{sid}.onnx"
            upload.replace(self.models_dir / name)
            db.execute("UPDATE submissions SET model = ? WHERE id = ?", (name, sid))
            return sid

    def get(self, sid: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()

    def of_group(self, group: str) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM submissions WHERE grp = ? ORDER BY id DESC", (group,)).fetchall()

    def position(self, sid: int) -> int | None:
        """排第幾位（1 起算），不在排隊就是 None。排的順序跟 worker 拿的順序一樣。"""
        with self._db() as db:
            row = db.execute("SELECT priority, id, status FROM submissions WHERE id = ?", (sid,)).fetchone()
            if row is None or row["status"] != QUEUED:
                return None
            ahead = db.execute(
                "SELECT COUNT(*) FROM submissions WHERE status = ? AND (priority > ? OR (priority = ? AND id < ?))",
                (QUEUED, row["priority"], row["priority"], row["id"])).fetchone()[0]
            return ahead + 1

    # worker 用
    def claim_queued(self) -> sqlite3.Row | None:
        """拿出下一件排隊的交件（優先的先，同優先照上傳順序），狀態改成 scoring。"""
        with self._tx() as db:
            row = db.execute("SELECT * FROM submissions WHERE status = ? ORDER BY priority DESC, id LIMIT 1",
                             (QUEUED,)).fetchone()
            if row:
                db.execute("UPDATE submissions SET status = ?, started_at = ? WHERE id = ?", (SCORING, now(), row["id"]))
            return row

    def filling(self) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM submissions WHERE status = ? ORDER BY priority DESC, id",
                              (FILLING,)).fetchall()

    def save_detections(self, sid: int, frames: dict[int, list[dict]]) -> None:
        with self._tx() as db:
            db.executemany("INSERT OR REPLACE INTO detections VALUES (?, ?, ?)",
                           [(sid, n, pack(boxes)) for n, boxes in frames.items()])
            done = db.execute("SELECT COUNT(*) FROM detections WHERE submission_id = ?", (sid,)).fetchone()[0]
            db.execute("UPDATE submissions SET frames_done = ? WHERE id = ?", (done, sid))

    def done_frames(self, sid: int) -> set[int]:
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT frame FROM detections WHERE submission_id = ?", (sid,))}

    def detections(self, sid: int) -> dict[int, list]:
        """{影片幀號: [[x1, y1, x2, y2, conf, 類別編號], ...]}，只有偵測過的幀。"""
        with self._db() as db:
            return {r[0]: json.loads(r[1]) for r in
                    db.execute("SELECT frame, boxes FROM detections WHERE submission_id = ?", (sid,))}

    def scored(self, sid: int, result: dict) -> None:
        with self._db() as db:
            db.execute("UPDATE submissions SET status = ?, result = ?, scored_at = ? WHERE id = ?",
                       (FILLING, json.dumps(result, ensure_ascii=False), now(), sid))

    def finish(self, sid: int) -> None:
        with self._db() as db:
            db.execute("UPDATE submissions SET status = ?, done_at = ? WHERE id = ? AND status = ?",
                       (DONE, now(), sid, FILLING))

    def fail(self, sid: int, error: str) -> None:
        with self._db() as db:
            db.execute("UPDATE submissions SET status = ?, error = ?, done_at = ? WHERE id = ?",
                       (FAILED, error, now(), sid))
