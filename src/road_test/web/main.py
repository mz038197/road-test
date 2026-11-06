"""在本機或 Fly 上啟動路考網站：`uv run road-test-site [--host 0.0.0.0] [--port 8000]`。

環境變數：
  ROAD_TEST_DATA     路考資料夾（必填，見 README）
  ROAD_TEST_WORKERS  幾個 worker（預設 2）
  ROAD_TEST_THREADS  每個 worker 推論用幾個執行緒（預設 CPU 核心數 ÷ worker 數）
"""
import argparse
import os
from functools import partial

import cv2
import uvicorn

from road_test import paths
from road_test.detector import OnnxDetector
from road_test.web.app import RoadTest, create_app

# 目前只開放模擬路考；正式路考（Seq05VD）在 #9 加上
SEQUENCES = {"mock": "0006R0"}


def frame_count(video) -> int:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"找不到路考影片 {video}，請參考 README 的「資料夾」一節。")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return n


def build_app():
    workers = int(os.environ.get("ROAD_TEST_WORKERS", "2"))
    threads = int(os.environ.get("ROAD_TEST_THREADS") or max(1, (os.cpu_count() or 1) // max(workers, 1)))
    road_tests = {kind: RoadTest(kind, seq, frame_count(paths.videos_dir() / f"{seq}.mp4"))
                  for kind, seq in SEQUENCES.items()}
    return create_app(paths.data_dir(), partial(OnnxDetector, threads=threads), road_tests, workers=workers)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="啟動路考網站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
