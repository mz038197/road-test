"""在本機或 Fly 上啟動路考網站：`uv run road-test-site [--host 0.0.0.0] [--port 8000]`。

環境變數：
  ROAD_TEST_DATA          路考資料夾（必填，見 README）
  ROAD_TEST_WORKERS、ROAD_TEST_THREADS、ROAD_TEST_IDLE_MINUTES、ROAD_TEST_MAX_UPLOAD_MB  見 settings.py
  ROAD_TEST_TEACHER_PASSWORD  老師密碼（沒設定時老師的 API 一律拒絕）
"""
import argparse
import os
from functools import partial

import cv2
import uvicorn

from road_test import paths
from road_test.detector import OnnxDetector
from road_test.web import settings
from road_test.web.app import RoadTest, create_app
from road_test.web.idle import IdleExit
from road_test.web.teacher import password_from_env

# 正式路考平常關著，老師開放時才收件（見 app 的 /api/teacher/final）
SEQUENCES = {"mock": "0006R0", "final": "Seq05VD"}


def frame_count(video) -> int:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"找不到路考影片 {video}，請參考 README 的「資料夾」一節。")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return n


def build_app(config: settings.Settings | None = None):
    config = config or settings.from_env(os.environ, os.cpu_count())
    road_tests = {kind: RoadTest(kind, seq, frame_count(paths.videos_dir() / f"{seq}.mp4"))
                  for kind, seq in SEQUENCES.items()}
    return create_app(paths.data_dir(), partial(OnnxDetector, threads=config.threads), road_tests,
                      workers=config.workers, teacher_password=password_from_env(),
                      max_upload_bytes=config.max_upload_bytes)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="啟動路考網站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    config = settings.from_env(os.environ, os.cpu_count())
    print(f"路考網站：{config.workers} 個 worker × {config.threads} 個執行緒；"
          + (f"閒置 {config.idle_minutes:g} 分鐘自動結束" if config.idle_minutes else "不會自動結束"), flush=True)
    app = build_app(config)
    # 一律包起來（/healthz 由它回應）；有設定閒置時間才會真的自己結束
    guard = IdleExit(app, busy=app.state.store.busy, minutes=config.idle_minutes or 0)
    server = uvicorn.Server(uvicorn.Config(guard, host=args.host, port=args.port))
    if config.idle_minutes:
        guard.watch(on_idle=lambda: setattr(server, "should_exit", True))  # 正常結束，exit 0
    server.run()


if __name__ == "__main__":
    main()
