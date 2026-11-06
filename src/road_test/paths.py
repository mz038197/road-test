"""路考用到的資料夾。資料都放在 repo 外，用環境變數指過去，因為答案、路考影片、交件不能進 git。"""
import os
from pathlib import Path


def _from_env(name: str, meaning: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"沒有設定環境變數 {name}（{meaning}）。請參考 README 的「資料夾」一節。")
    return Path(value)


def data_dir() -> Path:
    """路考資料夾：路考影片、答案、交件、結果、組別代碼。部署時就是 Fly 的 volume。"""
    return _from_env("ROAD_TEST_DATA", "路考資料夾")


def answers_dir() -> Path:
    return data_dir() / "answers"


def videos_dir() -> Path:
    """路考影片（Seq05VD.mp4、0006R0.mp4）和訓練影片（0016E5.mp4）。MP4 第 n 幀就是標註 f(n)。"""
    return data_dir() / "videos"


def student_dir() -> Path:
    """發給學生的訓練程式（train_yolo.py、train_yolo_colab.ipynb），委託頁從這裡提供下載。

    平常是 repo 的 student/；Docker 映像裡套件裝在 .venv，找不到 repo，所以用 ROAD_TEST_STUDENT 指過去。
    """
    value = os.environ.get("ROAD_TEST_STUDENT")
    return Path(value) if value else Path(__file__).resolve().parents[2] / "student"


def camvid_dir() -> Path:
    """CamVid（SegNet 版 480×360）資料夾，只有老師產生答案時用得到。"""
    return _from_env("CAMVID_DIR", "CamVid 資料夾")
