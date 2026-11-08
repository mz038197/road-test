"""交件、偵測結果、成績都存在路考資料夾的 SQLite（road_test_v2.db），模型檔存在 submissions/。

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
DATABASE = "road_test_v2.db"
FINAL_TRIES = 1  # 正式路考每組幾次；老師還次數（#10）就在 final_returns 加一列


class Busy(Exception):
    """這組已經有一件在跑。"""


class NoFinalLeft(Exception):
    """這組的正式路考次數用完了。"""


class DuplicateGroup(Exception):
    """這個班級已經有同名的組別。"""

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
CREATE TABLE IF NOT EXISTS settings (    -- 老師的開關，例如 final_open（正式路考開放中）
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                    -- JSON
);
CREATE TABLE IF NOT EXISTS final_returns (  -- 老師還給某組的正式路考次數（#10），一列 = 多一次
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grp         TEXT NOT NULL,
    returned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS class_groups (  -- 組別：老師替自己的班級建立（road-test ADR 0001）
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id    INTEGER NOT NULL,          -- router 的整數 class_id
    name        TEXT NOT NULL,
    final_tries INTEGER NOT NULL,          -- 這組有幾次正式路考
    created_at  TEXT NOT NULL,
    UNIQUE (class_id, name)
);
CREATE TABLE IF NOT EXISTS class_finals (  -- 正式路考開關，每個班級一個；沒有列就是關著
    class_id INTEGER PRIMARY KEY,
    open     INTEGER NOT NULL
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
        # road_test_v2.db：改成依班級分組時重新開始（road-test ADR 0001，不搬舊資料）；舊的 road_test.db 不讀
        self.path = data_dir / DATABASE
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

    def finals_left(self, group: str, db: sqlite3.Connection | None = None) -> int:
        """這組還能交幾次正式路考：FINAL_TRIES + 老師還的次數 − 已經交的（收下的都算，出錯的也算）。"""
        if db is None:
            with self._db() as db:
                return self.finals_left(group, db)
        used = db.execute("SELECT COUNT(*) FROM submissions WHERE grp = ? AND kind = 'final'", (group,)).fetchone()[0]
        returned = db.execute("SELECT COUNT(*) FROM final_returns WHERE grp = ?", (group,)).fetchone()[0]
        return FINAL_TRIES + returned - used

    def return_final(self, group: str) -> None:
        """還給這組一次正式路考（#10 管理頁用）。"""
        with self._db() as db:
            db.execute("INSERT INTO final_returns (grp, returned_at) VALUES (?, ?)", (group, now()))

    def add(self, group: str, kind: str, sequence: str, conf: float, filename: str, upload: Path,
            frames_total: int, priority: int = 0) -> int:
        """收件並排隊，模型檔改名成「組別_路考種類_上傳時間_編號.onnx」。

        模擬路考：這組有任何一件在跑就丟 Busy。正式路考：次數用完丟 NoFinalLeft；
        不用等這組的模擬路考跑完（正式路考開放時，模擬路考可能還在補幀）。
        """
        with self._tx() as db:
            if kind == "final":
                if self.finals_left(group, db) <= 0:
                    raise NoFinalLeft
                busy = db.execute(f"SELECT 1 FROM submissions WHERE grp = ? AND kind = 'final' AND status IN {ACTIVE}",
                                  (group,)).fetchone()
            else:
                busy = db.execute(f"SELECT 1 FROM submissions WHERE grp = ? AND status IN {ACTIVE}", (group,)).fetchone()
            if busy:
                raise Busy
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

    def clear(self) -> None:
        """學期結束清空：交件、偵測框、成績、還的正式路考次數，還有交上來的模型檔。開關（settings）不動。

        正在跑的那件會在 worker 下一次寫入時發現自己不見了，自然結束（見 save_detections）。
        收件中的暫存檔（upload-*.tmp）不刪，那件收完就是清空後的第一件。
        """
        with self._tx() as db:
            for table in ("submissions", "detections", "final_returns"):
                db.execute(f"DELETE FROM {table}")
        for path in self.models_dir.glob("*.onnx"):
            path.unlink(missing_ok=True)

    def everything(self) -> list[sqlite3.Row]:
        """所有組的交件，新的在前面（管理頁的路考紀錄）。"""
        with self._db() as db:
            return db.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall()

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

    # 老師的開關（存在 SQLite，重開機還在）
    def setting(self, key: str, default=None):
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    # 班級的組別和正式路考開關（老師用個人金鑰管理）
    def class_groups(self, class_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM class_groups WHERE class_id = ? ORDER BY id", (class_id,)).fetchall()

    def add_class_group(self, class_id: int, name: str, final_tries: int) -> sqlite3.Row:
        """建立組別；同一班已經有同名的組就丟 DuplicateGroup。"""
        try:
            with self._db() as db:
                cur = db.execute("INSERT INTO class_groups (class_id, name, final_tries, created_at) VALUES (?, ?, ?, ?)",
                                 (class_id, name, final_tries, now()))
                return db.execute("SELECT * FROM class_groups WHERE id = ?", (cur.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            raise DuplicateGroup from None

    def change_class_group(self, class_id: int, group_id: int, name: str | None,
                           final_tries: int | None) -> sqlite3.Row | None:
        """改名稱或正式路考次數（None 就不改）；這個班級沒有這組回 None。"""
        try:
            with self._db() as db:
                db.execute("UPDATE class_groups SET name = COALESCE(?, name), final_tries = COALESCE(?, final_tries)"
                           " WHERE id = ? AND class_id = ?", (name, final_tries, group_id, class_id))
                return db.execute("SELECT * FROM class_groups WHERE id = ? AND class_id = ?",
                                  (group_id, class_id)).fetchone()
        except sqlite3.IntegrityError:
            raise DuplicateGroup from None

    def set_class_final(self, class_id: int, is_open: bool) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO class_finals VALUES (?, ?)", (class_id, int(is_open)))

    def class_final_open(self, class_id: int) -> bool:
        with self._db() as db:
            row = db.execute("SELECT open FROM class_finals WHERE class_id = ?", (class_id,)).fetchone()
            return bool(row and row[0])

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
            if db.execute("SELECT 1 FROM submissions WHERE id = ?", (sid,)).fetchone() is None:
                return  # 偵測到一半被清空了，框不用留
            db.executemany("INSERT OR REPLACE INTO detections VALUES (?, ?, ?)",
                           [(sid, n, pack(boxes)) for n, boxes in frames.items()])
            done = db.execute("SELECT COUNT(*) FROM detections WHERE submission_id = ?", (sid,)).fetchone()[0]
            db.execute("UPDATE submissions SET frames_done = ? WHERE id = ?", (done, sid))

    def done_frames(self, sid: int) -> set[int]:
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT frame FROM detections WHERE submission_id = ?", (sid,))}

    def detections(self, sid: int, frames: list[int] | None = None) -> dict[int, list]:
        """{影片幀號: [[x1, y1, x2, y2, conf, 類別編號], ...]}，只有偵測過的幀。frames：只要這些幀（例如計分幀）。"""
        wanted = None if frames is None else set(frames)
        with self._db() as db:
            rows = db.execute("SELECT frame, boxes FROM detections WHERE submission_id = ?", (sid,))
            return {r[0]: json.loads(r[1]) for r in rows if wanted is None or r[0] in wanted}

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
