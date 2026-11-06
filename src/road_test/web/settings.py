"""路考網站的設定，從環境變數讀（本機用 PowerShell 設，Fly 上寫在 fly.toml 的 [env]）。

  ROAD_TEST_WORKERS       幾個 worker（同時跑幾段偵測），預設 CPU 核心數 ÷ 2
  ROAD_TEST_THREADS       每個 worker 推論用幾個執行緒，預設 CPU 核心數 ÷ worker 數
  ROAD_TEST_IDLE_MINUTES  沒有人連進來、也沒有路考在跑，幾分鐘後網站自己結束（Fly 就把機器關掉）。
                          沒設定或 0 就不會自己結束（本機用）
  ROAD_TEST_MAX_UPLOAD_MB 模型檔大小上限（MB），預設 100

worker 數的預設只寫在這裡（create_app 一定要傳 workers）。
"""
from collections.abc import Mapping
from dataclasses import dataclass


DEFAULT_MAX_UPLOAD_MB = 100


@dataclass(frozen=True)
class Settings:
    workers: int
    threads: int
    idle_minutes: float | None
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1_000_000


def _number(env: Mapping[str, str], name: str, kind, minimum):
    text = (env.get(name) or "").strip()
    if not text:
        return None
    try:
        value = kind(text)
    except ValueError:
        value = None
    if value is None or value < minimum:
        raise SystemExit(f"環境變數 {name} 要是不小於 {minimum} 的數字，現在是「{text}」。")
    return value


def from_env(env: Mapping[str, str], cpu_count: int | None) -> Settings:
    # 每個 worker 2 個執行緒最划算（Fly 上實測，見 README「部署」）：4 核心 2 個 worker、16 核心 8 個
    workers = _number(env, "ROAD_TEST_WORKERS", int, 1) or max(1, (cpu_count or 1) // 2)
    threads = _number(env, "ROAD_TEST_THREADS", int, 1) or max(1, (cpu_count or 1) // workers)
    idle = _number(env, "ROAD_TEST_IDLE_MINUTES", float, 0) or None
    upload_mb = _number(env, "ROAD_TEST_MAX_UPLOAD_MB", float, 1) or DEFAULT_MAX_UPLOAD_MB
    return Settings(workers, threads, idle, int(upload_mb * 1_000_000))
